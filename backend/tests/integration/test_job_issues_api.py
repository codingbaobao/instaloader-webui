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
