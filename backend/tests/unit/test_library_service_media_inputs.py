from datetime import UTC, datetime

import pytest

from instaloader_webui.services.instagram_inputs import InvalidInstagramInput
from instaloader_webui.services.library_service import LibraryService

NOW = datetime(2026, 7, 31, tzinfo=UTC)


class StubLibraryRepository:
    pass


class RecordingJobRepository:
    def __init__(self) -> None:
        self.enqueued_payload: dict[str, object] | None = None
        self.target_label: str | None = None
        self.target_url: str | None = None

    def enqueue(
        self,
        *,
        job_type: str,
        payload: dict[str, object],
        target_label: str | None,
        target_url: str | None,
        status_text: str,
        now: datetime,
    ) -> object:
        assert job_type == "single_media"
        assert status_text == "Queued media download."
        assert now == NOW
        self.enqueued_payload = payload
        self.target_label = target_label
        self.target_url = target_url
        return object()


def test_add_story_enqueues_canonical_single_media_job() -> None:
    jobs = RecordingJobRepository()
    service = LibraryService(  # type: ignore[arg-type]
        library=StubLibraryRepository(), jobs=jobs
    )

    service.add_media(
        "https://www.instagram.com/stories/katerina.soria/3952742051065980676"
        "?igsh=secret",
        NOW,
    )

    assert jobs.enqueued_payload == {
        "media_kind": "story",
        "identity_type": "story_media_id",
        "identity_value": "3952742051065980676",
        "story_media_id": "3952742051065980676",
        "username": "katerina.soria",
        "original_url": (
            "https://www.instagram.com/stories/"
            "katerina.soria/3952742051065980676/"
        ),
    }
    assert jobs.target_label == (
        "https://www.instagram.com/stories/"
        "katerina.soria/3952742051065980676/"
    )
    assert jobs.target_url == jobs.target_label


def test_add_post_enqueues_canonical_single_media_job() -> None:
    jobs = RecordingJobRepository()
    service = LibraryService(  # type: ignore[arg-type]
        library=StubLibraryRepository(), jobs=jobs
    )

    service.add_media("https://www.instagram.com/p/CmzV2H-rrlI/?igsh=secret", NOW)

    assert jobs.enqueued_payload == {
        "media_kind": "post",
        "identity_type": "shortcode",
        "identity_value": "CmzV2H-rrlI",
        "shortcode": "CmzV2H-rrlI",
        "original_url": "https://www.instagram.com/p/CmzV2H-rrlI/",
    }
    assert jobs.target_label == "https://www.instagram.com/p/CmzV2H-rrlI/"
    assert jobs.target_url == jobs.target_label


def test_add_reel_enqueues_canonical_single_media_job() -> None:
    jobs = RecordingJobRepository()
    service = LibraryService(  # type: ignore[arg-type]
        library=StubLibraryRepository(), jobs=jobs
    )

    service.add_media("https://www.instagram.com/reel/DOqEJyxCRGJ/?igsh=secret", NOW)

    assert jobs.enqueued_payload == {
        "media_kind": "reel",
        "identity_type": "shortcode",
        "identity_value": "DOqEJyxCRGJ",
        "shortcode": "DOqEJyxCRGJ",
        "original_url": "https://www.instagram.com/reel/DOqEJyxCRGJ/",
    }
    assert jobs.target_label == "https://www.instagram.com/reel/DOqEJyxCRGJ/"
    assert jobs.target_url == jobs.target_label


def test_add_media_rejects_a_profile_input() -> None:
    jobs = RecordingJobRepository()
    service = LibraryService(  # type: ignore[arg-type]
        library=StubLibraryRepository(), jobs=jobs
    )

    with pytest.raises(
        InvalidInstagramInput,
        match="^A post, reel, or Story URL is required here\\.$",
    ):
        service.add_media("@natgeo", NOW)

    assert jobs.enqueued_payload is None


def test_add_profile_rejects_a_media_input() -> None:
    jobs = RecordingJobRepository()
    service = LibraryService(  # type: ignore[arg-type]
        library=StubLibraryRepository(), jobs=jobs
    )

    with pytest.raises(InvalidInstagramInput, match="^A profile input is required here\\.$"):
        service.add_profile("https://www.instagram.com/p/CmzV2H-rrlI/", NOW)

    assert jobs.enqueued_payload is None
