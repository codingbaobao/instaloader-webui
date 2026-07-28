"""Response DTOs for authenticated followee discovery and import."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from instaloader_webui.api.library_dtos import JobResponse, serialize_job
from instaloader_webui.db.followee_import_repositories import (
    FolloweeCandidateSnapshot,
    FolloweeCommitSnapshot,
    FolloweeImportBatchSnapshot,
)
from instaloader_webui.db.library_repositories import JobSnapshot


class FolloweeCandidateResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    instagram_user_id: str
    username: str
    full_name: str
    profile_pic_url: str | None
    is_private: bool
    already_exists: bool


class FolloweeImportBatchResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    state: str
    source_username: str
    session_imported_at: datetime
    job: JobResponse
    total_count: int
    importable_count: int
    existing_count: int
    error: str | None
    created_at: datetime
    completed_at: datetime | None
    imported_at: datetime | None
    candidates: tuple[FolloweeCandidateResponse, ...]


class FolloweeImportCommitResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    imported_count: int
    existing_count: int
    jobs: tuple[JobResponse, ...]


def serialize_followee_candidate(
    candidate: FolloweeCandidateSnapshot,
) -> FolloweeCandidateResponse:
    return FolloweeCandidateResponse(
        id=candidate.id,
        instagram_user_id=candidate.instagram_user_id,
        username=candidate.username,
        full_name=candidate.full_name,
        profile_pic_url=candidate.profile_pic_url,
        is_private=candidate.is_private,
        already_exists=candidate.already_exists,
    )


def serialize_followee_batch(
    batch: FolloweeImportBatchSnapshot,
    job: JobSnapshot,
) -> FolloweeImportBatchResponse:
    return FolloweeImportBatchResponse(
        id=batch.id,
        state=batch.state,
        source_username=batch.source_username,
        session_imported_at=batch.session_imported_at,
        job=serialize_job(job),
        total_count=batch.total_count,
        importable_count=batch.importable_count,
        existing_count=batch.existing_count,
        error=batch.error,
        created_at=batch.created_at,
        completed_at=batch.completed_at,
        imported_at=batch.imported_at,
        candidates=tuple(
            serialize_followee_candidate(candidate)
            for candidate in batch.candidates
        ),
    )


def serialize_followee_commit(
    result: FolloweeCommitSnapshot,
) -> FolloweeImportCommitResponse:
    return FolloweeImportCommitResponse(
        imported_count=result.imported_count,
        existing_count=result.existing_count,
        jobs=tuple(serialize_job(job) for job in result.jobs),
    )
