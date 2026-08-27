"""Explicit response models for the public-library API consumed by the UI."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from instaloader_webui.db.library_repositories import (
    AppSettingsSnapshot,
    AssetSnapshot,
    JobIssueSnapshot,
    JobProgressSegmentSnapshot,
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
    role: str
    position: int
    file_size: int
    created_at: datetime


class MediaResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

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
    assets: tuple[AssetResponse, ...]


class MediaFeedResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[MediaResponse, ...]
    newer_cursor: str | None
    older_cursor: str | None


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


class JobIssueResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    identity_type: str
    identity_value: str
    shortcode: str | None
    story_media_id: str | None
    media_kind: str
    error_code: str
    safe_message: str
    exception_class_chain: tuple[str, ...]
    occurred_at: datetime


class JobProgressSegmentResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    segment: Literal["stories", "feed"]
    label: str
    state: Literal["pending", "running", "completed", "failed"]
    scanned: int
    total: int | None
    saved: int
    existing: int
    warnings: int
    updated_at: datetime


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
    phase: str | None
    target_label: str | None
    target_url: str | None
    progress_segments: tuple[JobProgressSegmentResponse, ...]
    issue_count: int
    issues: tuple[JobIssueResponse, ...]
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
        role=asset.role,
        position=asset.position,
        file_size=asset.file_size,
        created_at=asset.created_at,
    )


def serialize_media(media: MediaSnapshot) -> MediaResponse:
    return MediaResponse(
        id=media.id,
        instagram_media_id=media.instagram_media_id,
        shortcode=media.shortcode,
        story_media_id=media.story_media_id,
        identity_type=media.identity_type,
        identity_value=media.identity_value,
        owner_profile_id=media.owner_profile_id,
        kind=media.kind,
        caption=media.caption,
        accessibility_caption=media.accessibility_caption,
        published_at=media.published_at,
        original_url=media.original_url,
        story_expires_at=media.story_expires_at,
        downloaded_at=media.downloaded_at,
        created_at=media.created_at,
        updated_at=media.updated_at,
        assets=tuple(serialize_asset(asset) for asset in media.assets),
    )


def serialize_profile(profile: ProfileSnapshot, *, media_count: int) -> ProfileResponse:
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


def serialize_job_issue(issue: JobIssueSnapshot) -> JobIssueResponse:
    return JobIssueResponse(
        identity_type=issue.identity_type,
        identity_value=issue.identity_value,
        shortcode=issue.shortcode,
        story_media_id=issue.story_media_id,
        media_kind=issue.media_kind,
        error_code=issue.error_code,
        safe_message=issue.safe_message,
        exception_class_chain=issue.exception_class_chain,
        occurred_at=issue.occurred_at,
    )


def serialize_job_progress_segment(
    segment: JobProgressSegmentSnapshot,
) -> JobProgressSegmentResponse:
    label = "Stories" if segment.segment == "stories" else "Feed content"
    return JobProgressSegmentResponse(
        segment=segment.segment,
        label=label,
        state=segment.state,
        scanned=segment.scanned,
        total=segment.total,
        saved=segment.saved,
        existing=segment.existing,
        warnings=segment.warnings,
        updated_at=segment.updated_at,
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
        phase=job.phase,
        target_label=job.target_label,
        target_url=job.target_url,
        progress_segments=tuple(
            serialize_job_progress_segment(segment)
            for segment in job.progress_segments
        ),
        issue_count=job.issue_count,
        issues=tuple(serialize_job_issue(issue) for issue in job.issues),
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
