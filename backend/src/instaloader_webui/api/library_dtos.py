"""Explicit response models for the public-library API consumed by the UI."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from instaloader_webui.db.library_repositories import (
    AppSettingsSnapshot,
    AssetSnapshot,
    JobSnapshot,
    MediaSnapshot,
    ProfileSnapshot,
)
from instaloader_webui.instagram.session_store import InstagramSessionStatus


class AssetResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    media_id: str
    relative_path: str
    mime_type: str
    kind: str
    position: int
    file_size: int
    created_at: datetime


class MediaResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    instagram_media_id: str | None
    shortcode: str
    owner_profile_id: str
    kind: str
    caption: str
    accessibility_caption: str
    published_at: datetime
    original_url: str
    downloaded_at: datetime | None
    created_at: datetime
    updated_at: datetime
    assets: tuple[AssetResponse, ...]


class ProfileResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

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
    media_count: int


class JobResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    type: str
    state: str
    payload: dict[str, Any]
    progress_current: int
    progress_total: int | None
    status_text: str
    error: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime


class SettingsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    profile_sync_interval_minutes: int
    next_sync_at: datetime
    created_at: datetime
    updated_at: datetime


class InstagramSessionStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    configured: bool
    username: str | None
    imported_at: datetime | None
    last_validated_at: datetime | None


def serialize_asset(asset: AssetSnapshot) -> AssetResponse:
    return AssetResponse(
        id=asset.id,
        media_id=asset.media_id,
        relative_path=asset.relative_path,
        mime_type=asset.mime_type,
        kind=asset.kind,
        position=asset.position,
        file_size=asset.file_size,
        created_at=asset.created_at,
    )


def serialize_media(media: MediaSnapshot) -> MediaResponse:
    return MediaResponse(
        id=media.id,
        instagram_media_id=media.instagram_media_id,
        shortcode=media.shortcode,
        owner_profile_id=media.owner_profile_id,
        kind=media.kind,
        caption=media.caption,
        accessibility_caption=media.accessibility_caption,
        published_at=media.published_at,
        original_url=media.original_url,
        downloaded_at=media.downloaded_at,
        created_at=media.created_at,
        updated_at=media.updated_at,
        assets=tuple(serialize_asset(asset) for asset in media.assets),
    )


def serialize_profile(
    profile: ProfileSnapshot, *, media_count: int
) -> ProfileResponse:
    return ProfileResponse(
        id=profile.id,
        instagram_user_id=profile.instagram_user_id,
        username=profile.username,
        full_name=profile.full_name,
        biography=profile.biography,
        profile_pic_url=profile.profile_pic_url,
        tracked=profile.tracked,
        status=profile.status,
        last_sync_attempted_at=profile.last_sync_attempted_at,
        last_sync_succeeded_at=profile.last_sync_succeeded_at,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
        media_count=media_count,
    )


def serialize_job(job: JobSnapshot) -> JobResponse:
    return JobResponse(
        id=job.id,
        type=job.type,
        state=job.state,
        payload=_copy_json_value(job.payload),
        progress_current=job.progress_current,
        progress_total=job.progress_total,
        status_text=job.status_text,
        error=job.error,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        updated_at=job.updated_at,
    )


def serialize_settings(settings: AppSettingsSnapshot) -> SettingsResponse:
    return SettingsResponse(
        id=settings.id,
        profile_sync_interval_minutes=settings.profile_sync_interval_minutes,
        next_sync_at=settings.next_sync_at,
        created_at=settings.created_at,
        updated_at=settings.updated_at,
    )


def serialize_instagram_session_status(
    status: InstagramSessionStatus,
) -> InstagramSessionStatusResponse:
    return InstagramSessionStatusResponse(
        configured=status.configured,
        username=status.username,
        imported_at=status.imported_at,
        last_validated_at=status.last_validated_at,
    )


def _copy_json_value(value: object) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _copy_json_value(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_copy_json_value(item) for item in value]
    return value
