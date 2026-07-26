from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from instaloader_webui.api.dependencies import (
    get_library_service,
    get_settings_repository,
    require_csrf,
    require_password_change_complete,
)
from instaloader_webui.api.envelope import ApiEnvelope
from instaloader_webui.api.library_dtos import (
    JobResponse,
    SettingsResponse,
    serialize_job,
    serialize_settings,
)
from instaloader_webui.db.library_repositories import SettingsRepository
from instaloader_webui.services.library_service import LibraryService

router = APIRouter(prefix="/api/settings", tags=["settings"])


class UpdateSettingsRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    profile_sync_interval_minutes: int = Field(gt=0)


class SyncAllResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    jobs: tuple[JobResponse, ...]


@router.get("", response_model=ApiEnvelope[SettingsResponse])
def get_settings(
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
    _: Annotated[object, Depends(require_password_change_complete)],
) -> ApiEnvelope[SettingsResponse]:
    return ApiEnvelope(success=True, data=serialize_settings(settings_repository.get()))


@router.patch("", response_model=ApiEnvelope[SettingsResponse])
def update_settings(
    payload: UpdateSettingsRequest,
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
    _: Annotated[object, Depends(require_csrf)],
) -> ApiEnvelope[SettingsResponse]:
    settings = settings_repository.update_interval(
        payload.profile_sync_interval_minutes,
        datetime.now(UTC),
    )
    return ApiEnvelope(success=True, data=serialize_settings(settings))


@router.post("/sync-all", response_model=ApiEnvelope[SyncAllResponse])
def sync_all(
    service: Annotated[LibraryService, Depends(get_library_service)],
    _: Annotated[object, Depends(require_csrf)],
) -> ApiEnvelope[SyncAllResponse]:
    jobs = service.sync_all(datetime.now(UTC))
    return ApiEnvelope(
        success=True,
        data=SyncAllResponse(jobs=tuple(serialize_job(job) for job in jobs)),
    )
