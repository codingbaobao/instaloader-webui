from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from sqlalchemy import delete, func, select, update

from instaloader_webui.db.library_repositories import (
    JobIssueInput,
    JobRepository,
    LibraryRepository,
)
from instaloader_webui.db.models import Job, JobIssue, JobProgressSegment
from instaloader_webui.db.schema import initialize_database

NOW = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)


@pytest.fixture
def jobs(session_factory, test_settings) -> JobRepository:
    initialize_database(test_settings)
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


def test_profile_sync_snapshots_target_and_ordered_segment_progress(
    jobs: JobRepository,
    session_factory,
) -> None:
    library = LibraryRepository(session_factory)
    profile = library.upsert_profile_stub(
        username="mihi_727",
        tracked=True,
        now=NOW,
    )

    found_profile, queued = jobs.enqueue_active_profile_sync(
        profile_id=profile.id,
        status_text="Queued profile synchronization.",
        now=NOW,
    )

    assert found_profile == profile
    assert queued is not None
    assert queued.target_label == "@mihi_727"
    assert queued.target_url is None
    assert queued.progress_segments == ()

    jobs.initialize_profile_sync_progress(
        job_id=queued.id,
        now=NOW + timedelta(seconds=1),
    )
    initialized = jobs.get(queued.id)
    assert initialized is not None
    assert tuple(segment.segment for segment in initialized.progress_segments) == (
        "stories",
        "feed",
    )
    assert all(segment.state == "pending" for segment in initialized.progress_segments)
    assert all(segment.scanned == 0 for segment in initialized.progress_segments)

    claimed = jobs.claim_next(NOW + timedelta(seconds=2))
    assert claimed is not None
    jobs.update_segment_progress(
        job_id=claimed.id,
        segment="stories",
        state="completed",
        scanned=4,
        total=4,
        saved=1,
        existing=3,
        warnings=0,
        status_text="Stories complete.",
        now=NOW + timedelta(seconds=3),
    )
    jobs.update_segment_progress(
        job_id=claimed.id,
        segment="feed",
        state="running",
        scanned=7,
        total=None,
        saved=2,
        existing=5,
        warnings=1,
        status_text="Scanning Feed content.",
        now=NOW + timedelta(seconds=4),
    )

    [listed] = jobs.list()
    assert listed.status_text == "Scanning Feed content."
    stories, feed = listed.progress_segments
    assert (stories.state, stories.scanned, stories.saved, stories.existing) == (
        "completed",
        4,
        1,
        3,
    )
    assert (
        feed.state,
        feed.scanned,
        feed.total,
        feed.saved,
        feed.existing,
        feed.warnings,
    ) == ("running", 7, None, 2, 5, 1)

    jobs.fail_active_segment(
        job_id=claimed.id,
        now=NOW + timedelta(seconds=5),
    )
    failed_segment = jobs.get(claimed.id)
    assert failed_segment is not None
    assert failed_segment.progress_segments[1].state == "failed"

    with session_factory.begin() as session:
        session.execute(delete(Job).where(Job.id == queued.id))
    with session_factory() as session:
        assert (
            session.scalar(
                select(func.count(JobProgressSegment.job_id)).where(
                    JobProgressSegment.job_id == queued.id
                )
            )
            == 0
        )


def test_single_media_target_is_independent_from_payload_decoding(
    jobs: JobRepository,
) -> None:
    canonical_url = "https://www.instagram.com/p/DcdTMB3iXSB/"

    queued = jobs.enqueue(
        job_type="single_media",
        payload={"shortcode": "DcdTMB3iXSB"},
        target_label=canonical_url,
        target_url=canonical_url,
        status_text="Queued media download.",
        now=NOW,
    )

    snapshot = jobs.get(queued.id)
    assert snapshot is not None
    assert snapshot.payload == {"shortcode": "DcdTMB3iXSB"}
    assert snapshot.target_label == canonical_url
    assert snapshot.target_url == canonical_url
    assert snapshot.progress_segments == ()


def test_claim_next_skips_excluded_instagram_types_without_reordering_local_jobs(
    jobs: JobRepository,
) -> None:
    # Break caught: FIFO claiming without exclusions immediately runs another
    # Instagram request during a global cooldown and leaves local work blocked.
    instagram_jobs = [
        jobs.enqueue(
            job_type=job_type,
            payload={"sequence": index},
            status_text="Queued Instagram job.",
            now=NOW + timedelta(seconds=index),
        )
        for index, job_type in enumerate(
            ("profile_sync", "single_media", "followee_discovery")
        )
    ]
    local = jobs.enqueue(
        job_type="delete_media",
        payload={"media_id": "local-media"},
        status_text="Queued local deletion.",
        now=NOW + timedelta(seconds=3),
    )

    claimed = jobs.claim_next(
        NOW + timedelta(seconds=4),
        excluded_types={"profile_sync", "single_media", "followee_discovery"},
    )

    assert claimed is not None
    assert claimed.id == local.id
    for instagram_job in instagram_jobs:
        pending = jobs.get(instagram_job.id)
        assert pending is not None
        assert pending.state == "pending"


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
