import base64
import binascii
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from instaloader_webui.api.dependencies import (
    ApiError,
    get_library_repository,
    get_library_service,
    get_settings,
    require_csrf,
    require_password_change_complete,
)
from instaloader_webui.api.envelope import ApiEnvelope
from instaloader_webui.api.library_dtos import (
    JobResponse,
    MediaFeedResponse,
    MediaResponse,
    serialize_job,
    serialize_media,
)
from instaloader_webui.config import Settings
from instaloader_webui.db.library_repositories import (
    LibraryRepository,
    MediaFeedPosition,
    MediaSnapshot,
)
from instaloader_webui.services.instagram_inputs import InvalidInstagramInput
from instaloader_webui.services.library_service import (
    LibraryService,
    MediaNotFoundError,
)

router = APIRouter(prefix="/api/media", tags=["media"])


@dataclass(frozen=True, slots=True)
class _FeedCursor:
    direction: Literal["newer", "older"]
    position: MediaFeedPosition


class InstagramInputRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    input: str = Field(min_length=1, max_length=2_048)


@router.get("", response_model=ApiEnvelope[tuple[MediaResponse, ...]])
def list_media(
    library: Annotated[LibraryRepository, Depends(get_library_repository)],
    _: Annotated[object, Depends(require_password_change_complete)],
    profile_id: str | None = None,
    kind: Literal["post", "reel", "story"] | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> ApiEnvelope[tuple[MediaResponse, ...]]:
    return ApiEnvelope(
        success=True,
        data=tuple(
            serialize_media(media)
            for media in library.list_media(
                profile_id=profile_id, kind=kind, limit=limit
            )
        ),
    )


@router.get("/feed", response_model=ApiEnvelope[MediaFeedResponse])
def list_media_feed(
    library: Annotated[LibraryRepository, Depends(get_library_repository)],
    _: Annotated[object, Depends(require_password_change_complete)],
    anchor_id: Annotated[str | None, Query(min_length=1, max_length=36)] = None,
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
    profile_id: str | None = None,
    kind: Literal["post", "reel", "story"] | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ApiEnvelope[MediaFeedResponse]:
    if (anchor_id is None) == (cursor is None):
        raise _invalid_feed_cursor()

    if anchor_id is not None:
        window = library.list_media_feed(
            anchor_id=anchor_id,
            profile_id=profile_id,
            kind=kind,
            limit=limit,
        )
        if window is None:
            raise _media_not_found(anchor_id)
    else:
        assert cursor is not None
        decoded = _decode_feed_cursor(
            cursor,
            profile_id=profile_id,
            kind=kind,
        )
        window = library.list_media_feed(
            position=decoded.position,
            direction=decoded.direction,
            profile_id=profile_id,
            kind=kind,
            limit=limit,
        )
        assert window is not None

    items = window.items
    return ApiEnvelope(
        success=True,
        data=MediaFeedResponse(
            items=tuple(serialize_media(media) for media in items),
            newer_cursor=(
                _encode_feed_cursor(
                    items[0],
                    direction="newer",
                    profile_id=profile_id,
                    kind=kind,
                )
                if window.has_newer and items
                else None
            ),
            older_cursor=(
                _encode_feed_cursor(
                    items[-1],
                    direction="older",
                    profile_id=profile_id,
                    kind=kind,
                )
                if window.has_older and items
                else None
            ),
        ),
    )


def _encode_feed_cursor(
    media: MediaSnapshot,
    *,
    direction: Literal["newer", "older"],
    profile_id: str | None,
    kind: str | None,
) -> str:
    payload = json.dumps(
        {
            "direction": direction,
            "id": media.id,
            "kind": kind,
            "profile_id": profile_id,
            "published_at": media.published_at.isoformat(),
            "version": 1,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_feed_cursor(
    cursor: str,
    *,
    profile_id: str | None,
    kind: str | None,
) -> _FeedCursor:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(
            base64.b64decode(
                cursor + padding,
                altchars=b"-_",
                validate=True,
            ).decode("utf-8")
        )
        if not isinstance(payload, dict):
            raise TypeError
        direction = payload.get("direction")
        media_id = payload.get("id")
        published_at_raw = payload.get("published_at")
        if (
            payload.get("version") != 1
            or direction not in ("newer", "older")
            or not isinstance(media_id, str)
            or not media_id
            or len(media_id) > 36
            or not isinstance(published_at_raw, str)
            or payload.get("profile_id") != profile_id
            or payload.get("kind") != kind
        ):
            raise ValueError
        published_at = datetime.fromisoformat(published_at_raw)
        if published_at.tzinfo is None or published_at.utcoffset() is None:
            raise ValueError
    except (
        binascii.Error,
        json.JSONDecodeError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ):
        raise _invalid_feed_cursor() from None
    return _FeedCursor(
        direction=direction,
        position=MediaFeedPosition(
            published_at=published_at,
            media_id=media_id,
        ),
    )


def _invalid_feed_cursor() -> ApiError:
    return ApiError(
        422,
        "invalid_media_feed_cursor",
        "The media feed position is invalid.",
    )


@router.post("", response_model=ApiEnvelope[JobResponse])
def add_media(
    payload: InstagramInputRequest,
    service: Annotated[LibraryService, Depends(get_library_service)],
    _: Annotated[object, Depends(require_csrf)],
) -> ApiEnvelope[JobResponse]:
    try:
        job = service.add_media(payload.input, datetime.now(UTC))
    except InvalidInstagramInput as error:
        raise ApiError(422, "invalid_instagram_input", str(error)) from error
    return ApiEnvelope(success=True, data=serialize_job(job))


@router.get("/{media_id}", response_model=ApiEnvelope[MediaResponse])
def get_media(
    media_id: str,
    library: Annotated[LibraryRepository, Depends(get_library_repository)],
    _: Annotated[object, Depends(require_password_change_complete)],
) -> ApiEnvelope[MediaResponse]:
    media = library.get_media(media_id)
    if media is None:
        raise _media_not_found(media_id)
    return ApiEnvelope(success=True, data=serialize_media(media))


@router.get("/{media_id}/assets/{asset_id}")
def get_asset(
    media_id: str,
    asset_id: str,
    library: Annotated[LibraryRepository, Depends(get_library_repository)],
    settings: Annotated[Settings, Depends(get_settings)],
    _: Annotated[object, Depends(require_password_change_complete)],
) -> FileResponse:
    media = library.get_media(media_id)
    if media is None:
        raise _media_not_found(media_id)
    asset = next(
        (candidate for candidate in media.assets if candidate.id == asset_id), None
    )
    if asset is None:
        raise _asset_not_found(asset_id)
    path = _resolve_asset_path(settings.media_root, asset.relative_path)
    if path is None or not path.is_file():
        raise _asset_not_found(asset_id)
    return FileResponse(path=path, media_type=asset.mime_type)


@router.delete("/{media_id}", response_model=ApiEnvelope[JobResponse])
def delete_media(
    media_id: str,
    service: Annotated[LibraryService, Depends(get_library_service)],
    _: Annotated[object, Depends(require_csrf)],
) -> ApiEnvelope[JobResponse]:
    try:
        job = service.delete_media(media_id, datetime.now(UTC))
    except MediaNotFoundError as error:
        raise _media_not_found(media_id) from error
    return ApiEnvelope(success=True, data=serialize_job(job))


def _resolve_asset_path(media_root: Path, relative_path: str) -> Path | None:
    root = media_root.resolve()
    candidate = (root / relative_path).resolve()
    return candidate if candidate.is_relative_to(root) else None


def _media_not_found(media_id: str) -> ApiError:
    return ApiError(404, "media_not_found", f"Media {media_id} was not found.")


def _asset_not_found(asset_id: str) -> ApiError:
    return ApiError(404, "asset_not_found", f"Asset {asset_id} was not found.")
