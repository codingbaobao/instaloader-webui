from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
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
    ProfileResponse,
    serialize_job,
    serialize_profile,
)
from instaloader_webui.config import Settings
from instaloader_webui.db.library_repositories import LibraryRepository, ProfileSnapshot
from instaloader_webui.services.instagram_inputs import InvalidInstagramInput
from instaloader_webui.services.library_service import (
    LibraryService,
    ProfileNotActiveError,
    ProfileNotFoundError,
    ProfileSyncStoppedError,
)
from instaloader_webui.services.profile_avatars import stored_profile_avatar

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


class InstagramInputRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    input: str = Field(min_length=1, max_length=2_048)


class ProfileSyncStateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool


class ProfileCreateResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    profile: ProfileResponse
    job: JobResponse


@router.get("", response_model=ApiEnvelope[tuple[ProfileResponse, ...]])
def list_profiles(
    library: Annotated[LibraryRepository, Depends(get_library_repository)],
    _: Annotated[object, Depends(require_password_change_complete)],
) -> ApiEnvelope[tuple[ProfileResponse, ...]]:
    profiles = library.list_profiles()
    return ApiEnvelope(
        success=True,
        data=tuple(_serialize_profile(library, profile) for profile in profiles),
    )


@router.post("", response_model=ApiEnvelope[ProfileCreateResponse])
def add_profile(
    payload: InstagramInputRequest,
    service: Annotated[LibraryService, Depends(get_library_service)],
    library: Annotated[LibraryRepository, Depends(get_library_repository)],
    _: Annotated[object, Depends(require_csrf)],
) -> ApiEnvelope[ProfileCreateResponse]:
    try:
        profile, job = service.add_profile(payload.input, datetime.now(UTC))
    except InvalidInstagramInput as error:
        raise ApiError(422, "invalid_instagram_input", str(error)) from error
    return ApiEnvelope(
        success=True,
        data=ProfileCreateResponse(
            profile=_serialize_profile(library, profile),
            job=serialize_job(job),
        ),
    )


@router.get("/{profile_id}", response_model=ApiEnvelope[ProfileResponse])
def get_profile(
    profile_id: str,
    library: Annotated[LibraryRepository, Depends(get_library_repository)],
    _: Annotated[object, Depends(require_password_change_complete)],
) -> ApiEnvelope[ProfileResponse]:
    profile = library.get_profile(profile_id)
    if profile is None:
        raise _profile_not_found(profile_id)
    return ApiEnvelope(success=True, data=_serialize_profile(library, profile))


@router.get("/{profile_id}/avatar")
def get_profile_avatar(
    profile_id: str,
    library: Annotated[LibraryRepository, Depends(get_library_repository)],
    settings: Annotated[Settings, Depends(get_settings)],
    _: Annotated[object, Depends(require_password_change_complete)],
) -> FileResponse:
    if library.get_profile(profile_id) is None:
        raise _profile_not_found(profile_id)

    try:
        avatar = stored_profile_avatar(settings.media_root, profile_id)
    except ValueError as error:
        raise _profile_avatar_not_found(profile_id) from error
    if avatar is None:
        raise _profile_avatar_not_found(profile_id)

    return FileResponse(
        path=avatar.path,
        media_type=avatar.media_type,
        headers={"Cache-Control": "private, no-cache"},
    )


@router.post("/{profile_id}/sync", response_model=ApiEnvelope[JobResponse])
def sync_profile(
    profile_id: str,
    service: Annotated[LibraryService, Depends(get_library_service)],
    _: Annotated[object, Depends(require_csrf)],
) -> ApiEnvelope[JobResponse]:
    try:
        job = service.sync_profile(profile_id, datetime.now(UTC))
    except ProfileNotFoundError as error:
        raise _profile_not_found(profile_id) from error
    except ProfileNotActiveError as error:
        raise ApiError(
            409,
            "profile_not_active",
            "This profile cannot change synchronization state while deletion is pending.",
        ) from error
    except ProfileSyncStoppedError as error:
        raise ApiError(
            409,
            "profile_sync_stopped",
            "Resume synchronization before requesting a profile sync.",
        ) from error
    return ApiEnvelope(success=True, data=serialize_job(job))


@router.patch("/{profile_id}/sync", response_model=ApiEnvelope[ProfileResponse])
def set_profile_sync_enabled(
    profile_id: str,
    payload: ProfileSyncStateRequest,
    service: Annotated[LibraryService, Depends(get_library_service)],
    library: Annotated[LibraryRepository, Depends(get_library_repository)],
    _: Annotated[object, Depends(require_csrf)],
) -> ApiEnvelope[ProfileResponse]:
    try:
        profile = service.set_profile_sync_enabled(
            profile_id,
            payload.enabled,
            datetime.now(UTC),
        )
    except ProfileNotFoundError as error:
        raise _profile_not_found(profile_id) from error
    except ProfileNotActiveError as error:
        raise ApiError(
            409,
            "profile_not_active",
            "This profile cannot change synchronization state while deletion is pending.",
        ) from error
    return ApiEnvelope(success=True, data=_serialize_profile(library, profile))


@router.delete("/{profile_id}", response_model=ApiEnvelope[JobResponse])
def delete_profile(
    profile_id: str,
    service: Annotated[LibraryService, Depends(get_library_service)],
    _: Annotated[object, Depends(require_csrf)],
) -> ApiEnvelope[JobResponse]:
    try:
        job = service.delete_profile(profile_id, datetime.now(UTC))
    except ProfileNotFoundError as error:
        raise _profile_not_found(profile_id) from error
    return ApiEnvelope(success=True, data=serialize_job(job))


def _serialize_profile(
    library: LibraryRepository, profile: ProfileSnapshot
) -> ProfileResponse:
    media_count = library.count_media(profile_id=profile.id)
    return serialize_profile(profile, media_count=media_count)


def _profile_not_found(profile_id: str) -> ApiError:
    return ApiError(
        status_code=404,
        code="profile_not_found",
        message=f"Profile {profile_id} was not found.",
    )


def _profile_avatar_not_found(profile_id: str) -> ApiError:
    return ApiError(
        status_code=404,
        code="profile_avatar_not_found",
        message=f"Profile avatar for {profile_id} was not found.",
    )
