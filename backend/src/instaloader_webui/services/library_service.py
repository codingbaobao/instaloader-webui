"""Application operations that turn public-library requests into worker jobs."""

from datetime import datetime

from instaloader_webui.db.library_repositories import (
    JobRepository,
    JobSnapshot,
    LibraryRepository,
    ProfileSnapshot,
)
from instaloader_webui.services.instagram_inputs import (
    InvalidInstagramInput,
    parse_instagram_input,
)


class ProfileNotFoundError(LookupError):
    pass


class MediaNotFoundError(LookupError):
    pass


class LibraryService:
    def __init__(
        self, *, library: LibraryRepository, jobs: JobRepository
    ) -> None:
        self._library = library
        self._jobs = jobs

    def add_profile(
        self, raw_input: str, now: datetime
    ) -> tuple[ProfileSnapshot, JobSnapshot]:
        parsed = parse_instagram_input(raw_input)
        if parsed.kind != "profile":
            raise InvalidInstagramInput("A profile input is required here.")
        profile = self._library.upsert_profile_stub(
            username=parsed.value,
            tracked=True,
            now=now,
        )
        return profile, self.sync_profile(profile.id, now)

    def add_media(self, raw_input: str, now: datetime) -> JobSnapshot:
        parsed = parse_instagram_input(raw_input)
        if parsed.kind != "media":
            raise InvalidInstagramInput("A post, reel, or TV URL is required here.")
        assert parsed.original_url is not None
        return self._jobs.enqueue(
            job_type="single_media",
            payload={"shortcode": parsed.value, "original_url": parsed.original_url},
            status_text="Queued media download.",
            now=now,
        )

    def sync_profile(self, profile_id: str, now: datetime) -> JobSnapshot:
        profile = self._library.get_profile(profile_id)
        if profile is None:
            raise ProfileNotFoundError(profile_id)
        active_job = self._active_profile_sync(profile_id)
        if active_job is not None:
            return active_job
        return self._jobs.enqueue(
            job_type="profile_sync",
            payload={"profile_id": profile.id},
            status_text="Queued profile synchronization.",
            now=now,
        )

    def sync_all(self, now: datetime) -> tuple[JobSnapshot, ...]:
        return tuple(
            self.sync_profile(profile.id, now)
            for profile in self._library.list_profiles()
            if profile.tracked and profile.status == "active"
        )

    def delete_profile(self, profile_id: str, now: datetime) -> JobSnapshot:
        profile = self._library.mark_profile_for_deletion(profile_id, now)
        if profile is None:
            raise ProfileNotFoundError(profile_id)
        return self._jobs.enqueue(
            job_type="delete_profile",
            payload={"profile_id": profile.id},
            status_text="Queued profile deletion.",
            now=now,
        )

    def delete_media(self, media_id: str, now: datetime) -> JobSnapshot:
        media = self._library.get_media(media_id)
        if media is None:
            raise MediaNotFoundError(media_id)
        return self._jobs.enqueue(
            job_type="delete_media",
            payload={"media_id": media.id},
            status_text="Queued media deletion.",
            now=now,
        )

    def _active_profile_sync(self, profile_id: str) -> JobSnapshot | None:
        for job in self._jobs.list():
            if (
                job.type == "profile_sync"
                and job.state in {"pending", "running"}
                and job.payload.get("profile_id") == profile_id
            ):
                return job
        return None
