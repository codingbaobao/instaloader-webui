from typing import Annotated

from fastapi import APIRouter, Depends

from instaloader_webui.api.dependencies import (
    ApiError,
    get_job_repository,
    require_password_change_complete,
)
from instaloader_webui.api.envelope import ApiEnvelope
from instaloader_webui.api.library_dtos import JobResponse, serialize_job
from instaloader_webui.db.library_repositories import JobRepository

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("", response_model=ApiEnvelope[tuple[JobResponse, ...]])
def list_jobs(
    jobs: Annotated[JobRepository, Depends(get_job_repository)],
    _: Annotated[object, Depends(require_password_change_complete)],
) -> ApiEnvelope[tuple[JobResponse, ...]]:
    return ApiEnvelope(
        success=True, data=tuple(serialize_job(job) for job in jobs.list())
    )


@router.get("/{job_id}", response_model=ApiEnvelope[JobResponse])
def get_job(
    job_id: str,
    jobs: Annotated[JobRepository, Depends(get_job_repository)],
    _: Annotated[object, Depends(require_password_change_complete)],
) -> ApiEnvelope[JobResponse]:
    job = jobs.get(job_id, include_issues=True)
    if job is None:
        raise ApiError(404, "job_not_found", f"Job {job_id} was not found.")
    return ApiEnvelope(success=True, data=serialize_job(job))
