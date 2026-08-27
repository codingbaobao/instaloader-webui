from datetime import UTC, datetime, timedelta

import pytest

from instaloader_webui.db.library_repositories import JobIssueInput, JobRepository

NOW = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)
pytestmark = pytest.mark.anyio


async def _complete_password_change(client) -> None:
    session = await client.get("/api/auth/session")
    response = await client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": session.json()["data"]["csrf_token"]},
        json={
            "current_password": "correct-horse-battery-staple",
            "new_password": "correct-horse-battery-staple",
        },
    )
    assert response.status_code == 200


def _seed_job_with_issue(jobs: JobRepository):
    queued = jobs.enqueue(
        job_type="profile_sync",
        payload={"profile_id": "profile-1"},
        status_text="Queued.",
        now=NOW,
    )
    running = jobs.claim_next(NOW + timedelta(seconds=1))
    assert running is not None
    assert running.id == queued.id
    jobs.update_progress(
        job_id=running.id,
        current=1,
        total=2,
        status_text="Downloading reels.",
        phase="downloading",
        now=NOW + timedelta(seconds=2),
    )
    jobs.record_issue(
        job_id=running.id,
        issue=JobIssueInput(
            identity_type="shortcode",
            identity_value="DOqEJyxCRGJ",
            media_kind="reel",
            error_code="instagram_unavailable",
            safe_message="Instagram could not be reached. Try again later.",
            exception_class_chain=(
                "BadResponseException",
                "ConnectionException",
            ),
        ),
        now=NOW + timedelta(seconds=3),
    )
    return running


async def test_job_list_reports_issue_count_without_issue_details(
    authenticated_client,
) -> None:
    await _complete_password_change(authenticated_client)
    job = _seed_job_with_issue(authenticated_client.app.state.job_repository)

    response = await authenticated_client.get("/api/jobs")

    assert response.status_code == 200
    [serialized] = response.json()["data"]
    assert serialized["id"] == job.id
    assert serialized["phase"] == "downloading"
    assert serialized["issue_count"] == 1
    assert serialized["issues"] == []


async def test_job_detail_returns_safe_structured_issues(
    authenticated_client,
) -> None:
    await _complete_password_change(authenticated_client)
    job = _seed_job_with_issue(authenticated_client.app.state.job_repository)

    response = await authenticated_client.get(f"/api/jobs/{job.id}")

    assert response.status_code == 200
    serialized = response.json()["data"]
    assert serialized["phase"] == "downloading"
    assert serialized["issue_count"] == 1
    assert serialized["issues"] == [
        {
            "identity_type": "shortcode",
            "identity_value": "DOqEJyxCRGJ",
            "shortcode": "DOqEJyxCRGJ",
            "story_media_id": None,
            "media_kind": "reel",
            "error_code": "instagram_unavailable",
            "safe_message": "Instagram could not be reached. Try again later.",
            "exception_class_chain": [
                "BadResponseException",
                "ConnectionException",
            ],
            "occurred_at": "2026-07-31T08:00:03Z",
        }
    ]
    assert "Traceback" not in response.text
    assert "sessionid=" not in response.text
    assert "__a=1" not in response.text


async def test_job_api_exposes_targets_and_ordered_profile_segments(
    authenticated_client,
) -> None:
    await _complete_password_change(authenticated_client)
    jobs: JobRepository = authenticated_client.app.state.job_repository
    queued = jobs.enqueue(
        job_type="profile_sync",
        payload={"profile_id": "profile-1"},
        target_label="@mihi_727",
        status_text="Queued.",
        now=NOW,
    )
    running = jobs.claim_next(NOW + timedelta(seconds=1))
    assert running is not None
    jobs.initialize_profile_sync_progress(
        job_id=queued.id,
        now=NOW + timedelta(seconds=2),
    )
    jobs.update_segment_progress(
        job_id=queued.id,
        segment="stories",
        state="completed",
        scanned=3,
        total=3,
        saved=1,
        existing=2,
        warnings=0,
        status_text="Stories complete.",
        now=NOW + timedelta(seconds=3),
    )
    jobs.update_segment_progress(
        job_id=queued.id,
        segment="feed",
        state="running",
        scanned=42,
        total=None,
        saved=2,
        existing=39,
        warnings=1,
        status_text="Scanning Feed content.",
        now=NOW + timedelta(seconds=4),
    )

    response = await authenticated_client.get("/api/jobs")

    assert response.status_code == 200
    [serialized] = response.json()["data"]
    assert serialized["target_label"] == "@mihi_727"
    assert serialized["target_url"] is None
    assert [segment["segment"] for segment in serialized["progress_segments"]] == [
        "stories",
        "feed",
    ]
    assert [segment["label"] for segment in serialized["progress_segments"]] == [
        "Stories",
        "Feed content",
    ]
    assert serialized["progress_segments"][0] | {
        "updated_at": serialized["progress_segments"][0]["updated_at"]
    } == {
        "segment": "stories",
        "label": "Stories",
        "state": "completed",
        "scanned": 3,
        "total": 3,
        "saved": 1,
        "existing": 2,
        "warnings": 0,
        "updated_at": serialized["progress_segments"][0]["updated_at"],
    }
    assert serialized["progress_segments"][1]["state"] == "running"
    assert serialized["progress_segments"][1]["total"] is None
    assert serialized["issues"] == []


async def test_job_api_exposes_canonical_single_media_url(
    authenticated_client,
) -> None:
    await _complete_password_change(authenticated_client)
    canonical_url = "https://www.instagram.com/p/DcdTMB3iXSB/"
    jobs: JobRepository = authenticated_client.app.state.job_repository
    queued = jobs.enqueue(
        job_type="single_media",
        payload={"shortcode": "DcdTMB3iXSB"},
        target_label=canonical_url,
        target_url=canonical_url,
        status_text="Queued.",
        now=NOW,
    )

    response = await authenticated_client.get(f"/api/jobs/{queued.id}")

    assert response.status_code == 200
    serialized = response.json()["data"]
    assert serialized["target_label"] == canonical_url
    assert serialized["target_url"] == canonical_url
    assert "?" not in serialized["target_url"]
    assert "sessionid=" not in response.text
