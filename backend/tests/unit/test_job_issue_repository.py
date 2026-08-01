from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from sqlalchemy import delete, func, select, update

from instaloader_webui.db.library_repositories import (
    JobIssueInput,
    JobRepository,
)
from instaloader_webui.db.migrations import run_migrations
from instaloader_webui.db.models import Job, JobIssue

NOW = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)


@pytest.fixture
def jobs(session_factory, test_settings) -> JobRepository:
    run_migrations(test_settings)
    return JobRepository(session_factory)


def _enqueue_running(jobs: JobRepository):
    queued = jobs.enqueue(
        job_type="profile_sync",
        payload={"profile_id": "profile-1"},
        status_text="Queued.",
        now=NOW,
    )
    claimed = jobs.claim_next(NOW + timedelta(seconds=1))
    assert claimed is not None
    assert claimed.id == queued.id
    return claimed


def _issue(identity_value: str = "DOqEJyxCRGJ") -> JobIssueInput:
    return JobIssueInput(
        identity_type="shortcode",
        identity_value=identity_value,
        media_kind="reel",
        error_code="instagram_unavailable",
        safe_message="Instagram could not be reached. Try again later.",
        exception_class_chain=("BadResponseException", "ConnectionException"),
    )


def test_complete_with_warnings_returns_structured_issues(
    jobs: JobRepository,
) -> None:
    job = _enqueue_running(jobs)

    recorded = jobs.record_issue(
        job_id=job.id,
        issue=_issue(),
        now=NOW + timedelta(seconds=2),
    )
    jobs.complete_with_warnings(
        job_id=job.id,
        status_text="Completed with 1 warning.",
        now=NOW + timedelta(seconds=3),
    )

    detail = jobs.get(job.id, include_issues=True)
    assert detail is not None
    assert detail.state == "completed_with_warnings"
    assert detail.status_text == "Completed with 1 warning."
    assert detail.error is None
    assert detail.completed_at == NOW + timedelta(seconds=3)
    assert detail.issue_count == 1
    assert detail.issues == (recorded,)
    assert detail.issues[0].identity_value == "DOqEJyxCRGJ"
    assert detail.issues[0].shortcode == "DOqEJyxCRGJ"
    assert detail.issues[0].story_media_id is None
    assert detail.issues[0].exception_class_chain == (
        "BadResponseException",
        "ConnectionException",
    )


def test_list_returns_issue_count_without_issue_details(jobs: JobRepository) -> None:
    job = _enqueue_running(jobs)
    jobs.record_issue(
        job_id=job.id,
        issue=_issue(),
        now=NOW + timedelta(seconds=2),
    )

    [summary] = jobs.list()

    assert summary.id == job.id
    assert summary.issue_count == 1
    assert summary.issues == ()


def test_update_progress_persists_phase(jobs: JobRepository) -> None:
    job = _enqueue_running(jobs)

    jobs.update_progress(
        job_id=job.id,
        current=3,
        total=5,
        status_text="Downloading stories.",
        phase="downloading",
        now=NOW + timedelta(seconds=2),
    )

    updated = jobs.get(job.id)
    assert updated is not None
    assert updated.progress_current == 3
    assert updated.progress_total == 5
    assert updated.status_text == "Downloading stories."
    assert updated.phase == "downloading"


def test_complete_with_warnings_only_updates_running_jobs(
    jobs: JobRepository,
) -> None:
    pending = jobs.enqueue(
        job_type="profile_sync",
        payload={"profile_id": "profile-1"},
        status_text="Queued.",
        now=NOW,
    )

    jobs.complete_with_warnings(
        job_id=pending.id,
        status_text="Should not complete.",
        now=NOW + timedelta(seconds=1),
    )

    unchanged = jobs.get(pending.id)
    assert unchanged is not None
    assert unchanged.state == "pending"
    assert unchanged.status_text == "Queued."
    assert unchanged.completed_at is None


def test_complete_with_warnings_clears_prior_fatal_error(
    jobs: JobRepository, session_factory
) -> None:
    job = _enqueue_running(jobs)
    with session_factory.begin() as session:
        session.execute(
            update(Job).where(Job.id == job.id).values(error="stale fatal error")
        )

    jobs.complete_with_warnings(
        job_id=job.id,
        status_text="Completed with warnings.",
        now=NOW + timedelta(seconds=2),
    )

    completed = jobs.get(job.id)
    assert completed is not None
    assert completed.state == "completed_with_warnings"
    assert completed.error is None


def test_deleting_job_cascades_recorded_issues(
    jobs: JobRepository, session_factory
) -> None:
    job = _enqueue_running(jobs)
    jobs.record_issue(
        job_id=job.id,
        issue=_issue(),
        now=NOW + timedelta(seconds=2),
    )

    with session_factory.begin() as session:
        session.execute(delete(Job).where(Job.id == job.id))

    with session_factory() as session:
        issue_count = session.scalar(
            select(func.count(JobIssue.id)).where(JobIssue.job_id == job.id)
        )
    assert issue_count == 0


@pytest.mark.parametrize(
    "invalid_chain",
    [
        ("BadResponseException", 42),
        ("",),
        ("x" * 129,),
        ("a", "b", "c", "d", "e", "f", "g", "h", "i"),
    ],
    ids=["non-string", "empty", "overlong", "too-many"],
)
def test_record_issue_rejects_invalid_chain_before_database_write(
    jobs: JobRepository,
    session_factory,
    invalid_chain: tuple[object, ...],
) -> None:
    invalid_issue = JobIssueInput(
        identity_type="shortcode",
        identity_value="DOqEJyxCRGJ",
        media_kind="reel",
        error_code="instagram_unavailable",
        safe_message="Instagram could not be reached. Try again later.",
        exception_class_chain=cast(tuple[str, ...], invalid_chain),
    )

    with pytest.raises(ValueError, match="exception class chain"):
        jobs.record_issue(
            job_id="missing-job",
            issue=invalid_issue,
            now=NOW,
        )

    with session_factory() as session:
        issue_count = session.scalar(select(func.count(JobIssue.id)))
    assert issue_count == 0


@pytest.mark.parametrize(
    "persisted_chain",
    [
        "{not-json",
        '"BadResponseException"',
        '["BadResponseException",42]',
        f'["{"x" * 129}"]',
        '["a","b","c","d","e","f","g","h","i"]',
    ],
)
def test_job_detail_rejects_malformed_exception_class_chain(
    jobs: JobRepository,
    session_factory,
    persisted_chain: str,
) -> None:
    job = _enqueue_running(jobs)
    jobs.record_issue(
        job_id=job.id,
        issue=_issue(),
        now=NOW + timedelta(seconds=2),
    )
    with session_factory.begin() as session:
        session.execute(
            update(JobIssue)
            .where(JobIssue.job_id == job.id)
            .values(exception_class_chain_text=persisted_chain)
        )

    with pytest.raises(ValueError, match="exception class chain"):
        jobs.get(job.id, include_issues=True)
