"""Authenticated API for discovering and importing followed accounts."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from instaloader_webui.api.dependencies import (
    ApiError,
    get_followee_import_repository,
    get_followee_import_service,
    get_job_repository,
    require_csrf,
    require_password_change_complete,
)
from instaloader_webui.api.envelope import ApiEnvelope
from instaloader_webui.api.followee_import_dtos import (
    FolloweeImportBatchResponse,
    FolloweeImportCommitResponse,
    serialize_followee_batch,
    serialize_followee_commit,
)
from instaloader_webui.db.followee_import_repositories import (
    FolloweeCandidatesNotFoundError,
    FolloweeImportNotReadyError,
    FolloweeImportRepository,
    FolloweeImportRevisionError,
)
from instaloader_webui.db.library_repositories import JobRepository
from instaloader_webui.services.followee_import_service import (
    FolloweeImportService,
    FolloweeImportSessionError,
)

router = APIRouter(prefix="/api/followee-imports", tags=["followee-imports"])

CandidateId = Annotated[str, Field(min_length=1, max_length=36)]


class FolloweeImportCommitRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_ids: tuple[CandidateId, ...] = Field(min_length=1, max_length=10_000)


@router.post("", response_model=ApiEnvelope[FolloweeImportBatchResponse])
def create_followee_import(
    service: Annotated[FolloweeImportService, Depends(get_followee_import_service)],
    jobs: Annotated[JobRepository, Depends(get_job_repository)],
    _: Annotated[object, Depends(require_csrf)],
) -> ApiEnvelope[FolloweeImportBatchResponse]:
    try:
        batch = service.create_or_get_active(datetime.now(UTC))
    except FolloweeImportSessionError as error:
        raise ApiError(
            409,
            "instagram_session_required",
            str(error),
        ) from error
    return ApiEnvelope(
        success=True,
        data=serialize_followee_batch(batch, _required_job(jobs, batch.job_id)),
    )


@router.get(
    "/{batch_id}",
    response_model=ApiEnvelope[FolloweeImportBatchResponse],
)
def get_followee_import(
    batch_id: str,
    service: Annotated[FolloweeImportService, Depends(get_followee_import_service)],
    jobs: Annotated[JobRepository, Depends(get_job_repository)],
    _: Annotated[object, Depends(require_password_change_complete)],
) -> ApiEnvelope[FolloweeImportBatchResponse]:
    try:
        batch = service.get(batch_id)
    except FolloweeImportSessionError as error:
        raise ApiError(
            409,
            "followee_import_rescan_required",
            str(error),
        ) from error
    if batch is None:
        raise _batch_not_found(batch_id)
    return ApiEnvelope(
        success=True,
        data=serialize_followee_batch(batch, _required_job(jobs, batch.job_id)),
    )


@router.post(
    "/{batch_id}/commit",
    response_model=ApiEnvelope[FolloweeImportCommitResponse],
)
def commit_followee_import(
    batch_id: str,
    payload: FolloweeImportCommitRequest,
    service: Annotated[FolloweeImportService, Depends(get_followee_import_service)],
    repository: Annotated[
        FolloweeImportRepository,
        Depends(get_followee_import_repository),
    ],
    _: Annotated[object, Depends(require_csrf)],
) -> ApiEnvelope[FolloweeImportCommitResponse]:
    if repository.get(batch_id) is None:
        raise _batch_not_found(batch_id)
    try:
        result = service.commit(
            batch_id=batch_id,
            candidate_ids=payload.candidate_ids,
            now=datetime.now(UTC),
        )
    except (FolloweeImportSessionError, FolloweeImportRevisionError) as error:
        raise ApiError(
            409,
            "followee_import_rescan_required",
            str(error),
        ) from error
    except FolloweeImportNotReadyError as error:
        raise ApiError(
            409,
            "followee_import_not_ready",
            "This followee import batch is not ready.",
        ) from error
    except FolloweeCandidatesNotFoundError as error:
        raise ApiError(
            422,
            "invalid_followee_candidates",
            "One or more selected followee candidates are invalid.",
        ) from error
    return ApiEnvelope(success=True, data=serialize_followee_commit(result))


def _required_job(jobs: JobRepository, job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise RuntimeError("Followee import job record is missing.")
    return job


def _batch_not_found(batch_id: str) -> ApiError:
    return ApiError(
        404,
        "followee_import_not_found",
        f"Followee import batch {batch_id} was not found.",
    )
