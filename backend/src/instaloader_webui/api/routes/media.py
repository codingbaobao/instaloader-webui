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
    MediaResponse,
    serialize_job,
    serialize_media,
)
from instaloader_webui.config import Settings
from instaloader_webui.db.library_repositories import LibraryRepository
from instaloader_webui.services.instagram_inputs import InvalidInstagramInput
from instaloader_webui.services.library_service import (
    LibraryService,
    MediaNotFoundError,
)

router = APIRouter(prefix="/api/media", tags=["media"])


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
