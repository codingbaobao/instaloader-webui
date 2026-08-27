"""Strict, bounded serialization for resumable Instaloader iterators."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, NoReturn

from instaloader.nodeiterator import FrozenNodeIterator
from sqlalchemy.orm import Session, sessionmaker

from instaloader_webui.db.models import ProfileSyncCheckpoint

CURSOR_VERSION = 1
MAX_CURSOR_JSON_BYTES = 2 * 1024 * 1024
SAFE_CHECKPOINT_ERROR = "Profile sync checkpoint is invalid."
_DOCUMENT_KEYS = frozenset(
    {
        "version",
        "query_hash",
        "query_variables",
        "query_referer",
        "context_username",
        "total_index",
        "best_before",
        "remaining_data",
        "first_node",
        "doc_id",
    }
)


class ProfileSyncCheckpointError(ValueError):
    """A persisted iterator checkpoint cannot be safely used."""


def _invalid() -> NoReturn:
    raise ProfileSyncCheckpointError(SAFE_CHECKPOINT_ERROR)


def _validate_optional_string(value: object) -> str | None:
    if value is None or type(value) is str:
        return value
    _invalid()


def _validate_json_value(value: object) -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            _invalid()
        return
    if type(value) is list:
        for item in value:
            _validate_json_value(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                _invalid()
            _validate_json_value(item)
        return
    _invalid()


def _validated_document(value: object) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _DOCUMENT_KEYS:
        _invalid()
    document = value
    if type(document["version"]) is not int or document["version"] != CURSOR_VERSION:
        _invalid()
    for key in ("query_hash", "query_referer", "context_username", "doc_id"):
        _validate_optional_string(document[key])
    if type(document["query_variables"]) is not dict:
        _invalid()
    total_index = document["total_index"]
    if type(total_index) is not int or total_index < 0:
        _invalid()
    best_before = document["best_before"]
    if best_before is not None and (
        type(best_before) not in (int, float) or not math.isfinite(best_before)
    ):
        _invalid()
    for key in ("remaining_data", "first_node"):
        if document[key] is not None and type(document[key]) is not dict:
            _invalid()
    for key in ("query_variables", "remaining_data", "first_node"):
        _validate_json_value(document[key])
    return document


def encode_frozen_iterator(frozen: FrozenNodeIterator) -> str:
    """Encode one iterator checkpoint without accepting lossy JSON values."""
    document = {
        "version": CURSOR_VERSION,
        "query_hash": frozen.query_hash,
        "query_variables": frozen.query_variables,
        "query_referer": frozen.query_referer,
        "context_username": frozen.context_username,
        "total_index": frozen.total_index,
        "best_before": frozen.best_before,
        "remaining_data": frozen.remaining_data,
        "first_node": frozen.first_node,
        "doc_id": frozen.doc_id,
    }
    try:
        _validated_document(document)
        encoded = json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(encoded.encode("utf-8")) > MAX_CURSOR_JSON_BYTES:
            _invalid()
        return encoded
    except ProfileSyncCheckpointError:
        raise
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise ProfileSyncCheckpointError(SAFE_CHECKPOINT_ERROR) from error


def decode_frozen_iterator(cursor_json: str) -> FrozenNodeIterator:
    """Decode one exact application cursor version into Instaloader state."""
    try:
        if type(cursor_json) is not str:
            _invalid()
        if len(cursor_json.encode("utf-8")) > MAX_CURSOR_JSON_BYTES:
            _invalid()
        value = json.loads(cursor_json, parse_constant=lambda _value: _invalid())
        document = _validated_document(value)
        return FrozenNodeIterator(
            query_hash=document["query_hash"],
            query_variables=document["query_variables"],
            query_referer=document["query_referer"],
            context_username=document["context_username"],
            total_index=document["total_index"],
            best_before=document["best_before"],
            remaining_data=document["remaining_data"],
            first_node=document["first_node"],
            doc_id=document["doc_id"],
        )
    except ProfileSyncCheckpointError:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ProfileSyncCheckpointError(SAFE_CHECKPOINT_ERROR) from error


@dataclass(frozen=True, slots=True)
class ProfileSyncCheckpointSnapshot:
    profile_id: str
    source: Literal["posts", "reels"]
    cursor_version: int
    frozen: FrozenNodeIterator | None
    backfill_complete: bool
    updated_at: datetime


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _validate_source(source: str) -> Literal["posts", "reels"]:
    if source not in ("posts", "reels"):
        raise ValueError("Profile sync checkpoint source is invalid.")
    return source


def _snapshot(model: ProfileSyncCheckpoint) -> ProfileSyncCheckpointSnapshot:
    source = _validate_source(model.source)
    if model.cursor_version != CURSOR_VERSION:
        raise ProfileSyncCheckpointError(SAFE_CHECKPOINT_ERROR)
    frozen = (
        decode_frozen_iterator(model.cursor_json)
        if model.cursor_json is not None
        else None
    )
    return ProfileSyncCheckpointSnapshot(
        profile_id=model.profile_id,
        source=source,
        cursor_version=model.cursor_version,
        frozen=frozen,
        backfill_complete=model.backfill_complete,
        updated_at=_as_utc(model.updated_at),
    )


class ProfileSyncCheckpointRepository:
    """Persist independent Posts and Reels iterator resume state."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get(
        self,
        profile_id: str,
        source: Literal["posts", "reels"],
    ) -> ProfileSyncCheckpointSnapshot | None:
        validated_source = _validate_source(source)
        with self._session_factory() as session:
            model = session.get(
                ProfileSyncCheckpoint,
                (profile_id, validated_source),
            )
            return _snapshot(model) if model is not None else None

    def save_frozen(
        self,
        *,
        profile_id: str,
        source: Literal["posts", "reels"],
        frozen: FrozenNodeIterator,
        now: datetime,
    ) -> None:
        self._replace(
            profile_id=profile_id,
            source=source,
            cursor_json=encode_frozen_iterator(frozen),
            backfill_complete=False,
            now=now,
        )

    def mark_complete(
        self,
        *,
        profile_id: str,
        source: Literal["posts", "reels"],
        now: datetime,
    ) -> None:
        self._replace(
            profile_id=profile_id,
            source=source,
            cursor_json=None,
            backfill_complete=True,
            now=now,
        )

    def reset(
        self,
        *,
        profile_id: str,
        source: Literal["posts", "reels"],
        now: datetime,
    ) -> None:
        self._replace(
            profile_id=profile_id,
            source=source,
            cursor_json=None,
            backfill_complete=False,
            now=now,
        )

    def _replace(
        self,
        *,
        profile_id: str,
        source: Literal["posts", "reels"],
        cursor_json: str | None,
        backfill_complete: bool,
        now: datetime,
    ) -> None:
        validated_source = _validate_source(source)
        current_time = _as_utc(now)
        with self._session_factory.begin() as session:
            model = session.get(
                ProfileSyncCheckpoint,
                (profile_id, validated_source),
            )
            if model is None:
                model = ProfileSyncCheckpoint(
                    profile_id=profile_id,
                    source=validated_source,
                    cursor_version=CURSOR_VERSION,
                    cursor_json=cursor_json,
                    backfill_complete=backfill_complete,
                    updated_at=current_time,
                )
                session.add(model)
            else:
                model.cursor_version = CURSOR_VERSION
                model.cursor_json = cursor_json
                model.backfill_complete = backfill_complete
                model.updated_at = current_time
