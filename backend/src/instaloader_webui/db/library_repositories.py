from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Literal, cast
from uuid import uuid4

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from instaloader_webui.db.models import (
    AppSetting,
    Job,
    JobIssue,
    JobProgressSegment,
    MediaAsset,
    MediaItem,
    Profile,
)

GLOBAL_APP_SETTINGS_ID = "global"
_MAX_EXCEPTION_CLASS_CHAIN_LENGTH = 8
_MAX_EXCEPTION_CLASS_NAME_LENGTH = 128


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class AssetSnapshot:
    id: str
    media_id: str
    relative_path: str
    mime_type: str
    kind: str
    role: str
    position: int
    file_size: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MediaIdentity:
    identity_type: Literal["shortcode", "story_media_id"]
    value: str


@dataclass(frozen=True, slots=True)
class ProfileSnapshot:
    id: str
    instagram_user_id: str | None
    username: str
    full_name: str
    biography: str
    profile_pic_url: str | None
    tracked: bool
    status: str
    last_sync_attempted_at: datetime | None
    last_sync_succeeded_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class MediaSnapshot:
    id: str
    instagram_media_id: str | None
    shortcode: str | None
    story_media_id: str | None
    identity_type: str
    identity_value: str
    owner_profile_id: str
    kind: str
    caption: str
    accessibility_caption: str
    published_at: datetime
    original_url: str
    story_expires_at: datetime | None
    downloaded_at: datetime | None
    created_at: datetime
    updated_at: datetime
    assets: tuple[AssetSnapshot, ...]


@dataclass(frozen=True, slots=True)
class MediaFeedPosition:
    published_at: datetime
    media_id: str


@dataclass(frozen=True, slots=True)
class MediaFeedWindow:
    items: tuple[MediaSnapshot, ...]
    has_newer: bool
    has_older: bool


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    id: str
    type: str
    state: str
    payload: Mapping[str, object]
    progress_current: int
    progress_total: int | None
    status_text: str
    error: str | None
    phase: str | None
    target_label: str | None
    target_url: str | None
    progress_segments: tuple[JobProgressSegmentSnapshot, ...]
    issue_count: int
    issues: tuple[JobIssueSnapshot, ...]
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class JobProgressSegmentSnapshot:
    segment: Literal["stories", "feed"]
    state: Literal["pending", "running", "completed", "failed"]
    scanned: int
    total: int | None
    saved: int
    existing: int
    warnings: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class JobIssueInput:
    identity_type: str
    identity_value: str
    media_kind: str
    error_code: str
    safe_message: str
    exception_class_chain: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class JobIssueSnapshot:
    id: str
    job_id: str
    identity_type: str
    identity_value: str
    shortcode: str | None
    story_media_id: str | None
    media_kind: str
    error_code: str
    safe_message: str
    exception_class_chain: tuple[str, ...]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class AppSettingsSnapshot:
    id: str
    profile_sync_interval_minutes: int
    next_sync_at: datetime
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class NormalizedAsset:
    relative_path: str
    mime_type: str
    kind: Literal["image", "video"]
    role: Literal["content", "poster"]
    position: int
    file_size: int


@dataclass(frozen=True, slots=True)
class NormalizedMedia:
    identity: MediaIdentity
    instagram_media_id: str | None
    shortcode: str | None
    kind: str
    caption: str
    accessibility_caption: str
    published_at: datetime
    story_expires_at: datetime | None
    original_url: str


def _asset_snapshot(model: MediaAsset) -> AssetSnapshot:
    return AssetSnapshot(
        id=model.id,
        media_id=model.media_item_id,
        relative_path=model.relative_path,
        mime_type=model.mime_type,
        kind=model.kind,
        role=model.role,
        position=model.position,
        file_size=model.file_size,
        created_at=_as_utc(model.created_at),
    )


def _profile_snapshot(model: Profile) -> ProfileSnapshot:
    return ProfileSnapshot(
        id=model.id,
        instagram_user_id=model.instagram_user_id,
        username=model.username,
        full_name=model.full_name,
        biography=model.biography,
        profile_pic_url=model.profile_pic_url,
        tracked=model.tracked,
        status=model.status,
        last_sync_attempted_at=(
            _as_utc(model.last_sync_attempted_at)
            if model.last_sync_attempted_at is not None
            else None
        ),
        last_sync_succeeded_at=(
            _as_utc(model.last_sync_succeeded_at)
            if model.last_sync_succeeded_at is not None
            else None
        ),
        created_at=_as_utc(model.created_at),
        updated_at=_as_utc(model.updated_at),
    )


def _media_snapshot(model: MediaItem, assets: list[MediaAsset]) -> MediaSnapshot:
    ordered_assets = sorted(
        assets,
        key=lambda asset: (
            asset.position,
            0 if asset.role == "content" else 1,
            _as_utc(asset.created_at),
            asset.id,
        ),
    )
    return MediaSnapshot(
        id=model.id,
        instagram_media_id=model.instagram_media_id,
        shortcode=model.shortcode,
        story_media_id=(
            model.identity_value if model.identity_type == "story_media_id" else None
        ),
        identity_type=model.identity_type,
        identity_value=model.identity_value,
        owner_profile_id=model.owner_profile_id,
        kind=model.kind,
        caption=model.caption,
        accessibility_caption=model.accessibility_caption,
        published_at=_as_utc(model.published_at),
        original_url=model.original_url,
        story_expires_at=(
            _as_utc(model.story_expires_at)
            if model.story_expires_at is not None
            else None
        ),
        downloaded_at=(
            _as_utc(model.downloaded_at) if model.downloaded_at is not None else None
        ),
        created_at=_as_utc(model.created_at),
        updated_at=_as_utc(model.updated_at),
        assets=tuple(_asset_snapshot(asset) for asset in ordered_assets),
    )


def _freeze_json_value(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_json_value(nested_value) for key, nested_value in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json_value(item) for item in value)
    return value


def _freeze_job_payload(payload: dict[str, object]) -> Mapping[str, object]:
    return MappingProxyType(
        {key: _freeze_json_value(value) for key, value in payload.items()}
    )


def _validate_exception_class_chain(value: object) -> tuple[str, ...]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) > _MAX_EXCEPTION_CLASS_CHAIN_LENGTH
        or any(
            not isinstance(item, str)
            or not item
            or len(item) > _MAX_EXCEPTION_CLASS_NAME_LENGTH
            for item in value
        )
    ):
        raise ValueError("Invalid exception class chain.")
    return tuple(value)


def _decode_exception_class_chain(value: str) -> tuple[str, ...]:
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError("Persisted exception class chain is malformed.") from error
    if not isinstance(decoded, list):
        raise ValueError("Persisted exception class chain is malformed.")
    return _validate_exception_class_chain(decoded)


def _job_issue_snapshot(model: JobIssue) -> JobIssueSnapshot:
    return JobIssueSnapshot(
        id=model.id,
        job_id=model.job_id,
        identity_type=model.identity_type,
        identity_value=model.identity_value,
        shortcode=(
            model.identity_value if model.identity_type == "shortcode" else None
        ),
        story_media_id=(
            model.identity_value
            if model.identity_type == "story_media_id"
            else None
        ),
        media_kind=model.media_kind,
        error_code=model.error_code,
        safe_message=model.safe_message,
        exception_class_chain=_decode_exception_class_chain(
            model.exception_class_chain_text
        ),
        occurred_at=_as_utc(model.occurred_at),
    )


def _job_snapshot(
    model: Job,
    *,
    progress_segments: tuple[JobProgressSegmentSnapshot, ...] = (),
    issue_count: int = 0,
    issues: tuple[JobIssueSnapshot, ...] = (),
) -> JobSnapshot:
    payload = json.loads(model.payload_text)
    if not isinstance(payload, dict):
        raise ValueError("Persisted job payload must be a JSON object.")
    return JobSnapshot(
        id=model.id,
        type=model.type,
        state=model.state,
        payload=_freeze_job_payload(payload),
        progress_current=model.progress_current,
        progress_total=model.progress_total,
        status_text=model.status_text,
        error=model.error,
        phase=model.phase,
        target_label=model.target_label,
        target_url=model.target_url,
        progress_segments=progress_segments,
        issue_count=issue_count,
        issues=issues,
        created_at=_as_utc(model.created_at),
        started_at=_as_utc(model.started_at) if model.started_at is not None else None,
        completed_at=(
            _as_utc(model.completed_at) if model.completed_at is not None else None
        ),
        updated_at=_as_utc(model.updated_at),
    )


def _job_progress_segment_snapshot(
    model: JobProgressSegment,
) -> JobProgressSegmentSnapshot:
    segment = cast(Literal["stories", "feed"], model.segment)
    state = cast(
        Literal["pending", "running", "completed", "failed"],
        model.state,
    )
    if segment not in ("stories", "feed"):
        raise ValueError("Persisted job progress segment is invalid.")
    if state not in ("pending", "running", "completed", "failed"):
        raise ValueError("Persisted job progress state is invalid.")
    return JobProgressSegmentSnapshot(
        segment=segment,
        state=state,
        scanned=model.scanned,
        total=model.total,
        saved=model.saved,
        existing=model.existing,
        warnings=model.warnings,
        updated_at=_as_utc(model.updated_at),
    )


def _progress_segments_by_job_id(
    session: Session,
    job_ids: Collection[str],
) -> dict[str, tuple[JobProgressSegmentSnapshot, ...]]:
    if not job_ids:
        return {}
    grouped: dict[str, list[JobProgressSegmentSnapshot]] = {
        job_id: [] for job_id in job_ids
    }
    rows = session.scalars(
        select(JobProgressSegment).where(JobProgressSegment.job_id.in_(job_ids))
    )
    for row in rows:
        grouped[row.job_id].append(_job_progress_segment_snapshot(row))
    order = {"stories": 0, "feed": 1}
    return {
        job_id: tuple(sorted(segments, key=lambda item: order[item.segment]))
        for job_id, segments in grouped.items()
    }


def job_snapshot(model: Job) -> JobSnapshot:
    """Expose an immutable job DTO to focused transactional repositories."""
    return _job_snapshot(model)


def _settings_snapshot(model: AppSetting) -> AppSettingsSnapshot:
    return AppSettingsSnapshot(
        id=model.id,
        profile_sync_interval_minutes=model.profile_sync_interval_minutes,
        next_sync_at=_as_utc(model.next_sync_at),
        created_at=_as_utc(model.created_at),
        updated_at=_as_utc(model.updated_at),
    )


class LibraryRepository:
    """Persist public-library records without leaking mutable ORM objects."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def list_profiles(self) -> tuple[ProfileSnapshot, ...]:
        with self._session_factory() as session:
            profiles = session.scalars(
                select(Profile).order_by(Profile.tracked.desc(), Profile.username)
            ).all()
            return tuple(_profile_snapshot(profile) for profile in profiles)

    def get_profile(self, profile_id: str) -> ProfileSnapshot | None:
        with self._session_factory() as session:
            model = session.get(Profile, profile_id)
            return _profile_snapshot(model) if model is not None else None

    def find_profile_by_username(self, username: str) -> ProfileSnapshot | None:
        with self._session_factory() as session:
            model = session.scalar(select(Profile).where(Profile.username == username))
            return _profile_snapshot(model) if model is not None else None

    def find_profile_by_instagram_user_id(
        self, instagram_user_id: str
    ) -> ProfileSnapshot | None:
        """Resolve a profile by Instagram's stable numeric identity."""
        with self._session_factory() as session:
            model = session.scalar(
                select(Profile).where(
                    Profile.instagram_user_id == instagram_user_id
                )
            )
            return _profile_snapshot(model) if model is not None else None

    def upsert_profile_stub(
        self, *, username: str, tracked: bool, now: datetime
    ) -> ProfileSnapshot:
        current_time = _as_utc(now)
        with self._session_factory.begin() as session:
            model = session.scalar(select(Profile).where(Profile.username == username))
            if model is None:
                model = Profile(
                    id=str(uuid4()),
                    instagram_user_id=None,
                    username=username,
                    full_name="",
                    biography="",
                    profile_pic_url=None,
                    tracked=tracked,
                    status="active",
                    last_sync_attempted_at=None,
                    last_sync_succeeded_at=None,
                    created_at=current_time,
                    updated_at=current_time,
                )
                session.add(model)
            else:
                model.tracked = model.tracked or tracked
                model.status = "active"
                model.updated_at = current_time
            session.flush()
            return _profile_snapshot(model)

    def set_profile_sync_enabled(
        self, *, profile_id: str, enabled: bool, now: datetime
    ) -> ProfileSnapshot | None:
        with self._session_factory.begin() as session:
            model = session.get(Profile, profile_id)
            if model is None:
                return None
            if model.status != "active":
                raise ValueError("Profile is not active.")
            model.tracked = enabled
            model.updated_at = _as_utc(now)
            session.flush()
            return _profile_snapshot(model)

    def update_profile_metadata(
        self,
        *,
        profile_id: str,
        instagram_user_id: str,
        username: str,
        full_name: str,
        biography: str,
        profile_pic_url: str | None,
        now: datetime,
    ) -> ProfileSnapshot:
        with self._session_factory.begin() as session:
            model = session.get(Profile, profile_id)
            if model is None:
                raise LookupError(f"Profile {profile_id} does not exist.")
            model.instagram_user_id = instagram_user_id
            model.username = username
            model.full_name = full_name
            model.biography = biography
            model.profile_pic_url = profile_pic_url
            model.updated_at = _as_utc(now)
            session.flush()
            return _profile_snapshot(model)

    def set_profile_sync_result(
        self, *, profile_id: str, succeeded: bool, now: datetime
    ) -> ProfileSnapshot:
        current_time = _as_utc(now)
        with self._session_factory.begin() as session:
            model = session.get(Profile, profile_id)
            if model is None:
                raise LookupError(f"Profile {profile_id} does not exist.")
            model.last_sync_attempted_at = current_time
            if succeeded:
                model.last_sync_succeeded_at = current_time
            model.updated_at = current_time
            session.flush()
            return _profile_snapshot(model)

    def mark_profile_for_deletion(
        self, profile_id: str, now: datetime
    ) -> ProfileSnapshot | None:
        with self._session_factory.begin() as session:
            model = session.get(Profile, profile_id)
            if model is None:
                return None
            model.status = "deletion_pending"
            model.updated_at = _as_utc(now)
            session.flush()
            return _profile_snapshot(model)

    def mark_profile_deletion_failed(
        self, profile_id: str, now: datetime
    ) -> ProfileSnapshot | None:
        """Transition an existing profile after its deletion job fails."""
        with self._session_factory.begin() as session:
            model = session.get(Profile, profile_id)
            if model is None:
                return None
            model.status = "deletion_failed"
            model.updated_at = _as_utc(now)
            session.flush()
            return _profile_snapshot(model)

    def delete_profile_records(self, profile_id: str) -> tuple[str, ...]:
        with self._session_factory.begin() as session:
            media_ids = list(
                session.scalars(
                    select(MediaItem.id).where(MediaItem.owner_profile_id == profile_id)
                )
            )
            paths = tuple(
                session.scalars(
                    select(MediaAsset.relative_path)
                    .where(MediaAsset.media_item_id.in_(media_ids))
                    .order_by(MediaAsset.relative_path)
                )
            ) if media_ids else ()
            if media_ids:
                session.execute(
                    delete(MediaAsset).where(MediaAsset.media_item_id.in_(media_ids))
                )
                session.execute(
                    delete(MediaItem).where(MediaItem.id.in_(media_ids))
                )
            session.execute(delete(Profile).where(Profile.id == profile_id))
            return paths

    def list_media(
        self,
        *,
        profile_id: str | None = None,
        kind: str | None = None,
        limit: int = 100,
    ) -> tuple[MediaSnapshot, ...]:
        query = (
            select(MediaItem)
            .order_by(MediaItem.published_at.desc(), MediaItem.id.desc())
            .limit(limit)
        )
        if profile_id is not None:
            query = query.where(MediaItem.owner_profile_id == profile_id)
        if kind is not None:
            query = query.where(MediaItem.kind == kind)
        with self._session_factory() as session:
            media_items = list(session.scalars(query).all())
            return self._media_snapshots(session, media_items)

    def list_media_feed(
        self,
        *,
        anchor_id: str | None = None,
        position: MediaFeedPosition | None = None,
        direction: Literal["newer", "older"] | None = None,
        profile_id: str | None = None,
        kind: str | None = None,
        limit: int = 20,
    ) -> MediaFeedWindow | None:
        filters = []
        if profile_id is not None:
            filters.append(MediaItem.owner_profile_id == profile_id)
        if kind is not None:
            filters.append(MediaItem.kind == kind)

        with self._session_factory() as session:
            anchor: MediaItem | None = None
            if anchor_id is not None:
                anchor = session.get(MediaItem, anchor_id)
                if (
                    anchor is None
                    or (
                        profile_id is not None
                        and anchor.owner_profile_id != profile_id
                    )
                    or (kind is not None and anchor.kind != kind)
                ):
                    return None
                position = MediaFeedPosition(
                    published_at=_as_utc(anchor.published_at),
                    media_id=anchor.id,
                )
            if position is None:
                raise ValueError("A media feed anchor or position is required.")

            published_at = _as_utc(position.published_at)
            newer_condition = or_(
                MediaItem.published_at > published_at,
                and_(
                    MediaItem.published_at == published_at,
                    MediaItem.id > position.media_id,
                ),
            )
            older_condition = or_(
                MediaItem.published_at < published_at,
                and_(
                    MediaItem.published_at == published_at,
                    MediaItem.id < position.media_id,
                ),
            )

            def load_newer(count: int) -> tuple[list[MediaItem], bool]:
                rows = session.scalars(
                    select(MediaItem)
                    .where(*filters, newer_condition)
                    .order_by(MediaItem.published_at.asc(), MediaItem.id.asc())
                    .limit(count + 1)
                ).all()
                selected = rows[:count]
                return list(reversed(selected)), len(rows) > count

            def load_older(count: int) -> tuple[list[MediaItem], bool]:
                rows = session.scalars(
                    select(MediaItem)
                    .where(*filters, older_condition)
                    .order_by(MediaItem.published_at.desc(), MediaItem.id.desc())
                    .limit(count + 1)
                ).all()
                return list(rows[:count]), len(rows) > count

            if anchor is not None:
                newer_count = (limit - 1) // 2
                newer, has_newer = load_newer(newer_count)
                older_count = limit - 1 - len(newer)
                older, has_older = load_older(older_count)
                if len(older) < older_count:
                    newer, has_newer = load_newer(limit - 1 - len(older))
                models = [*newer, anchor, *older]
                return MediaFeedWindow(
                    items=self._media_snapshots(session, models),
                    has_newer=has_newer,
                    has_older=has_older,
                )

            if direction == "newer":
                models, has_newer = load_newer(limit)
                return MediaFeedWindow(
                    items=self._media_snapshots(session, models),
                    has_newer=has_newer,
                    has_older=False,
                )
            if direction == "older":
                models, has_older = load_older(limit)
                return MediaFeedWindow(
                    items=self._media_snapshots(session, models),
                    has_newer=False,
                    has_older=has_older,
                )
            raise ValueError("A media feed cursor direction is required.")

    def list_all_media_for_profile(self, profile_id: str) -> tuple[MediaSnapshot, ...]:
        """Return every media item owned by one profile for worker deletion."""
        with self._session_factory() as session:
            media_items = list(
                session.scalars(
                    select(MediaItem)
                    .where(MediaItem.owner_profile_id == profile_id)
                    .order_by(MediaItem.published_at.desc(), MediaItem.id)
                ).all()
            )
            return self._media_snapshots(session, media_items)

    def count_media(
        self,
        *,
        profile_id: str | None = None,
        kind: str | None = None,
    ) -> int:
        query = select(func.count(MediaItem.id))
        if profile_id is not None:
            query = query.where(MediaItem.owner_profile_id == profile_id)
        if kind is not None:
            query = query.where(MediaItem.kind == kind)
        with self._session_factory() as session:
            return int(session.scalar(query) or 0)

    def get_media(self, media_id: str) -> MediaSnapshot | None:
        with self._session_factory() as session:
            model = session.get(MediaItem, media_id)
            if model is None:
                return None
            return self._media_snapshots(session, [model])[0]

    def find_media_by_shortcode(self, shortcode: str) -> MediaSnapshot | None:
        return self.find_media_by_identity(MediaIdentity("shortcode", shortcode))

    def find_media_by_identity(
        self, identity: MediaIdentity
    ) -> MediaSnapshot | None:
        with self._session_factory() as session:
            model = session.scalar(
                select(MediaItem).where(
                    MediaItem.identity_type == identity.identity_type,
                    MediaItem.identity_value == identity.value,
                )
            )
            if model is None:
                return None
            return self._media_snapshots(session, [model])[0]

    def set_media_kind(
        self, *, shortcode: str, kind: str, now: datetime
    ) -> MediaSnapshot | None:
        """Update a known item's normalized kind without replacing its assets."""
        if kind not in {"post", "reel"}:
            raise ValueError("Media kind must be post or reel.")
        with self._session_factory.begin() as session:
            model = session.scalar(
                select(MediaItem).where(MediaItem.shortcode == shortcode)
            )
            if model is None:
                return None
            model.kind = kind
            model.updated_at = _as_utc(now)
            session.flush()
            return self._media_snapshots(session, [model])[0]

    def upsert_media(
        self,
        *,
        normalized: NormalizedMedia,
        profile_id: str,
        assets: tuple[NormalizedAsset, ...],
        now: datetime,
    ) -> MediaSnapshot:
        current_time = _as_utc(now)
        with self._session_factory.begin() as session:
            model = session.scalar(
                select(MediaItem).where(
                    MediaItem.identity_type == normalized.identity.identity_type,
                    MediaItem.identity_value == normalized.identity.value,
                )
            )
            if model is None:
                model = MediaItem(
                    id=str(uuid4()),
                    instagram_media_id=normalized.instagram_media_id,
                    shortcode=normalized.shortcode,
                    identity_type=normalized.identity.identity_type,
                    identity_value=normalized.identity.value,
                    owner_profile_id=profile_id,
                    kind=normalized.kind,
                    caption=normalized.caption,
                    accessibility_caption=normalized.accessibility_caption,
                    published_at=_as_utc(normalized.published_at),
                    original_url=normalized.original_url,
                    story_expires_at=(
                        _as_utc(normalized.story_expires_at)
                        if normalized.story_expires_at is not None
                        else None
                    ),
                    downloaded_at=current_time,
                    created_at=current_time,
                    updated_at=current_time,
                )
                session.add(model)
                session.flush()
            else:
                model.instagram_media_id = normalized.instagram_media_id
                model.shortcode = normalized.shortcode
                model.identity_type = normalized.identity.identity_type
                model.identity_value = normalized.identity.value
                model.owner_profile_id = profile_id
                model.kind = normalized.kind
                model.caption = normalized.caption
                model.accessibility_caption = normalized.accessibility_caption
                model.published_at = _as_utc(normalized.published_at)
                model.original_url = normalized.original_url
                model.story_expires_at = (
                    _as_utc(normalized.story_expires_at)
                    if normalized.story_expires_at is not None
                    else None
                )
                model.downloaded_at = current_time
                model.updated_at = current_time
                session.execute(
                    delete(MediaAsset).where(MediaAsset.media_item_id == model.id)
                )
            asset_models = [
                MediaAsset(
                    id=str(uuid4()),
                    media_item_id=model.id,
                    relative_path=asset.relative_path,
                    mime_type=asset.mime_type,
                    kind=asset.kind,
                    role=asset.role,
                    position=asset.position,
                    file_size=asset.file_size,
                    created_at=current_time,
                )
                for asset in assets
            ]
            session.add_all(asset_models)
            session.flush()
            return _media_snapshot(model, asset_models)

    def delete_media_records(self, media_id: str) -> tuple[str, ...]:
        with self._session_factory.begin() as session:
            paths = tuple(
                session.scalars(
                    select(MediaAsset.relative_path)
                    .where(MediaAsset.media_item_id == media_id)
                    .order_by(MediaAsset.relative_path)
                )
            )
            session.execute(delete(MediaAsset).where(MediaAsset.media_item_id == media_id))
            session.execute(delete(MediaItem).where(MediaItem.id == media_id))
            return paths

    @staticmethod
    def _media_snapshots(
        session: Session, media_items: list[MediaItem]
    ) -> tuple[MediaSnapshot, ...]:
        if not media_items:
            return ()
        assets_by_media_id: dict[str, list[MediaAsset]] = {
            media.id: [] for media in media_items
        }
        assets = session.scalars(
            select(MediaAsset)
            .where(MediaAsset.media_item_id.in_(assets_by_media_id))
            .order_by(MediaAsset.position, MediaAsset.created_at)
        ).all()
        for asset in assets:
            assets_by_media_id[asset.media_item_id].append(asset)
        return tuple(
            _media_snapshot(media, assets_by_media_id[media.id]) for media in media_items
        )


class JobRepository:
    """Persist worker jobs with atomic SQLite claiming semantics."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def enqueue(
        self,
        *,
        job_type: str,
        payload: dict[str, object],
        target_label: str | None = None,
        target_url: str | None = None,
        status_text: str,
        now: datetime,
    ) -> JobSnapshot:
        current_time = _as_utc(now)
        model = Job(
            id=str(uuid4()),
            type=job_type,
            state="pending",
            payload_text=json.dumps(payload, separators=(",", ":"), sort_keys=True),
            progress_current=0,
            progress_total=None,
            status_text=status_text,
            error=None,
            target_label=target_label,
            target_url=target_url,
            created_at=current_time,
            started_at=None,
            completed_at=None,
            updated_at=current_time,
        )
        with self._session_factory.begin() as session:
            session.add(model)
            session.flush()
            return _job_snapshot(model)

    def enqueue_profile_sync(
        self,
        *,
        profile_id: str,
        status_text: str,
        now: datetime,
    ) -> JobSnapshot:
        """Return the active sync job for a profile or atomically enqueue one."""
        current_time = _as_utc(now)
        payload = {"profile_id": profile_id}
        payload_text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        with self._session_factory() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            profile = session.get(Profile, profile_id)
            target_label = f"@{profile.username}" if profile is not None else None
            existing = session.scalar(
                select(Job)
                .where(
                    Job.type == "profile_sync",
                    Job.state.in_(("pending", "running")),
                    Job.payload_text == payload_text,
                )
                .order_by(Job.created_at, Job.id)
                .limit(1)
            )
            if existing is not None:
                snapshot = _job_snapshot(existing)
                session.commit()
                return snapshot
            model = Job(
                id=str(uuid4()),
                type="profile_sync",
                state="pending",
                payload_text=payload_text,
                progress_current=0,
                progress_total=None,
                status_text=status_text,
                error=None,
                target_label=target_label,
                target_url=None,
                created_at=current_time,
                started_at=None,
                completed_at=None,
                updated_at=current_time,
            )
            session.add(model)
            session.flush()
            snapshot = _job_snapshot(model)
            session.commit()
            return snapshot

    def enqueue_active_profile_sync(
        self,
        *,
        profile_id: str,
        status_text: str,
        now: datetime,
    ) -> tuple[ProfileSnapshot | None, JobSnapshot | None]:
        """Atomically validate a profile's sync state and coalesce its sync job."""
        current_time = _as_utc(now)
        payload = {"profile_id": profile_id}
        payload_text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        with self._session_factory() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            profile = session.get(Profile, profile_id)
            if profile is None:
                session.commit()
                return None, None
            profile_snapshot = _profile_snapshot(profile)
            if profile.status != "active" or not profile.tracked:
                session.commit()
                return profile_snapshot, None
            existing = session.scalar(
                select(Job)
                .where(
                    Job.type == "profile_sync",
                    Job.state.in_(("pending", "running")),
                    Job.payload_text == payload_text,
                )
                .order_by(Job.created_at, Job.id)
                .limit(1)
            )
            if existing is not None:
                snapshot = _job_snapshot(existing)
                session.commit()
                return profile_snapshot, snapshot
            model = Job(
                id=str(uuid4()),
                type="profile_sync",
                state="pending",
                payload_text=payload_text,
                progress_current=0,
                progress_total=None,
                status_text=status_text,
                error=None,
                target_label=f"@{profile.username}",
                target_url=None,
                created_at=current_time,
                started_at=None,
                completed_at=None,
                updated_at=current_time,
            )
            session.add(model)
            session.flush()
            snapshot = _job_snapshot(model)
            session.commit()
            return profile_snapshot, snapshot

    def list(self, limit: int = 100) -> tuple[JobSnapshot, ...]:
        with self._session_factory() as session:
            jobs = session.scalars(
                select(Job).order_by(Job.created_at.desc()).limit(limit)
            ).all()
            if not jobs:
                return ()
            issue_counts: dict[str, int] = {
                job_id: int(count)
                for job_id, count in session.execute(
                    select(JobIssue.job_id, func.count(JobIssue.id))
                    .where(JobIssue.job_id.in_(job.id for job in jobs))
                    .group_by(JobIssue.job_id)
                ).tuples()
            }
            progress_segments = _progress_segments_by_job_id(
                session,
                tuple(job.id for job in jobs),
            )
            return tuple(
                _job_snapshot(
                    job,
                    progress_segments=progress_segments.get(job.id, ()),
                    issue_count=issue_counts.get(job.id, 0),
                )
                for job in jobs
            )

    def get(
        self, job_id: str, *, include_issues: bool = False
    ) -> JobSnapshot | None:
        with self._session_factory() as session:
            model = session.get(Job, job_id)
            if model is None:
                return None
            issue_models = session.scalars(
                select(JobIssue)
                .where(JobIssue.job_id == job_id)
                .order_by(JobIssue.occurred_at, JobIssue.id)
            ).all()
            issues = (
                tuple(_job_issue_snapshot(issue) for issue in issue_models)
                if include_issues
                else ()
            )
            progress_segments = _progress_segments_by_job_id(session, (job_id,))
            return _job_snapshot(
                model,
                progress_segments=progress_segments.get(job_id, ()),
                issue_count=len(issue_models),
                issues=issues,
            )

    def initialize_profile_sync_progress(
        self,
        *,
        job_id: str,
        now: datetime,
    ) -> None:
        current_time = _as_utc(now)
        with self._session_factory.begin() as session:
            job = session.get(Job, job_id)
            if job is None or job.type != "profile_sync":
                raise ValueError("Profile sync job is missing.")
            session.execute(
                delete(JobProgressSegment).where(
                    JobProgressSegment.job_id == job_id
                )
            )
            session.add_all(
                JobProgressSegment(
                    job_id=job_id,
                    segment=segment,
                    state="pending",
                    scanned=0,
                    total=None,
                    saved=0,
                    existing=0,
                    warnings=0,
                    updated_at=current_time,
                )
                for segment in ("stories", "feed")
            )

    def update_segment_progress(
        self,
        *,
        job_id: str,
        segment: Literal["stories", "feed"],
        state: Literal["pending", "running", "completed", "failed"],
        scanned: int,
        total: int | None,
        saved: int,
        existing: int,
        warnings: int,
        status_text: str,
        now: datetime,
    ) -> None:
        current_time = _as_utc(now)
        with self._session_factory.begin() as session:
            segment_row = session.get(JobProgressSegment, (job_id, segment))
            if segment_row is None:
                raise ValueError("Profile sync progress segment is missing.")
            segment_row.state = state
            segment_row.scanned = scanned
            segment_row.total = total
            segment_row.saved = saved
            segment_row.existing = existing
            segment_row.warnings = warnings
            segment_row.updated_at = current_time
            session.execute(
                update(Job)
                .where(Job.id == job_id, Job.state == "running")
                .values(status_text=status_text, updated_at=current_time)
            )

    def fail_active_segment(self, *, job_id: str, now: datetime) -> None:
        with self._session_factory.begin() as session:
            session.execute(
                update(JobProgressSegment)
                .where(
                    JobProgressSegment.job_id == job_id,
                    JobProgressSegment.state == "running",
                )
                .values(state="failed", updated_at=_as_utc(now))
            )

    def claim_next(
        self,
        now: datetime,
        *,
        excluded_types: Collection[str] = (),
    ) -> JobSnapshot | None:
        current_time = _as_utc(now)
        with self._session_factory() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            statement = select(Job).where(Job.state == "pending")
            if excluded_types:
                statement = statement.where(Job.type.not_in(tuple(excluded_types)))
            model = session.scalar(statement.order_by(Job.created_at, Job.id).limit(1))
            if model is None:
                session.commit()
                return None
            model.state = "running"
            model.started_at = current_time
            model.updated_at = current_time
            session.flush()
            snapshot = _job_snapshot(model)
            session.commit()
            return snapshot

    def update_progress(
        self,
        *,
        job_id: str,
        current: int,
        total: int | None,
        status_text: str,
        now: datetime,
        phase: str | None = None,
    ) -> None:
        with self._session_factory.begin() as session:
            session.execute(
                update(Job)
                .where(Job.id == job_id, Job.state == "running")
                .values(
                    progress_current=current,
                    progress_total=total,
                    status_text=status_text,
                    phase=phase,
                    updated_at=_as_utc(now),
                )
            )

    def record_issue(
        self,
        *,
        job_id: str,
        issue: JobIssueInput,
        now: datetime,
    ) -> JobIssueSnapshot:
        exception_class_chain = _validate_exception_class_chain(
            issue.exception_class_chain
        )
        model = JobIssue(
            id=str(uuid4()),
            job_id=job_id,
            identity_type=issue.identity_type,
            identity_value=issue.identity_value,
            media_kind=issue.media_kind,
            error_code=issue.error_code,
            safe_message=issue.safe_message,
            exception_class_chain_text=json.dumps(
                exception_class_chain,
                separators=(",", ":"),
            ),
            occurred_at=_as_utc(now),
        )
        with self._session_factory.begin() as session:
            session.add(model)
            session.flush()
            return _job_issue_snapshot(model)

    def succeed(self, *, job_id: str, status_text: str, now: datetime) -> None:
        current_time = _as_utc(now)
        with self._session_factory.begin() as session:
            session.execute(
                update(Job)
                .where(Job.id == job_id, Job.state == "running")
                .values(
                    state="succeeded",
                    status_text=status_text,
                    error=None,
                    completed_at=current_time,
                    updated_at=current_time,
                )
            )

    def complete_with_warnings(
        self, *, job_id: str, status_text: str, now: datetime
    ) -> None:
        current_time = _as_utc(now)
        with self._session_factory.begin() as session:
            session.execute(
                update(Job)
                .where(Job.id == job_id, Job.state == "running")
                .values(
                    state="completed_with_warnings",
                    status_text=status_text,
                    error=None,
                    completed_at=current_time,
                    updated_at=current_time,
                )
            )

    def fail(self, *, job_id: str, error: str, now: datetime) -> None:
        current_time = _as_utc(now)
        with self._session_factory.begin() as session:
            session.execute(
                update(Job)
                .where(Job.id == job_id, Job.state == "running")
                .values(
                    state="failed",
                    error=error,
                    completed_at=current_time,
                    updated_at=current_time,
                )
            )

    def recover_interrupted(self, now: datetime) -> int:
        with self._session_factory.begin() as session:
            result = session.execute(
                update(Job)
                .where(Job.state == "running")
                .values(
                    state="pending",
                    started_at=None,
                    updated_at=_as_utc(now),
                )
            )
            return result.rowcount

    def has_active_profile_sync(self, profile_id: str) -> bool:
        with self._session_factory() as session:
            payloads = session.scalars(
                select(Job.payload_text).where(
                    Job.type == "profile_sync", Job.state.in_(("pending", "running"))
                )
            )
            return any(
                isinstance(payload := json.loads(payload_text), dict)
                and payload.get("profile_id") == profile_id
                for payload_text in payloads
            )


class SettingsRepository:
    """Manage the singleton profile-sync schedule atomically."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get(self) -> AppSettingsSnapshot:
        with self._session_factory() as session:
            model = session.get(AppSetting, GLOBAL_APP_SETTINGS_ID)
            if model is None:
                raise RuntimeError("Global application settings are missing.")
            return _settings_snapshot(model)

    def update_interval(self, minutes: int, now: datetime) -> AppSettingsSnapshot:
        if minutes <= 0:
            raise ValueError("Profile synchronization interval must be positive.")
        current_time = _as_utc(now)
        with self._session_factory.begin() as session:
            model = session.get(AppSetting, GLOBAL_APP_SETTINGS_ID)
            if model is None:
                raise RuntimeError("Global application settings are missing.")
            model.profile_sync_interval_minutes = minutes
            model.next_sync_at = current_time + timedelta(minutes=minutes)
            model.updated_at = current_time
            session.flush()
            return _settings_snapshot(model)

    def claim_due_sync(self, now: datetime) -> bool:
        current_time = _as_utc(now)
        with self._session_factory() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            model = session.get(AppSetting, GLOBAL_APP_SETTINGS_ID)
            if model is None:
                session.commit()
                raise RuntimeError("Global application settings are missing.")
            if _as_utc(model.next_sync_at) > current_time:
                session.commit()
                return False
            schedule_base = max(_as_utc(model.next_sync_at), current_time)
            model.next_sync_at = schedule_base + timedelta(
                minutes=model.profile_sync_interval_minutes
            )
            model.updated_at = current_time
            session.commit()
            return True
