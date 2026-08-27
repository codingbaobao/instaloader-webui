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
    PostInput,
    ProfileInput,
    ReelInput,
    StoryInput,
    parse_instagram_input,
)


class ProfileNotFoundError(LookupError):
    pass


class ProfileNotActiveError(RuntimeError):
    pass


class ProfileSyncStoppedError(RuntimeError):
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
        if not isinstance(parsed, ProfileInput):
            raise InvalidInstagramInput("A profile input is required here.")
        profile = self._library.upsert_profile_stub(
            username=parsed.username,
            tracked=True,
            now=now,
        )
        return profile, self.sync_profile(profile.id, now)

    def add_media(self, raw_input: str, now: datetime) -> JobSnapshot:
        parsed = parse_instagram_input(raw_input)
        if isinstance(parsed, ProfileInput):
            raise InvalidInstagramInput("A post, reel, or Story URL is required here.")
        if isinstance(parsed, StoryInput):
            payload: dict[str, object] = {
                "media_kind": "story",
                "identity_type": "story_media_id",
                "identity_value": parsed.story_media_id,
                "story_media_id": parsed.story_media_id,
                "username": parsed.username,
                "original_url": parsed.canonical_url,
            }
        else:
            assert isinstance(parsed, (PostInput, ReelInput))
            payload = {
                "media_kind": parsed.kind,
                "identity_type": "shortcode",
                "identity_value": parsed.shortcode,
                "shortcode": parsed.shortcode,
                "original_url": parsed.canonical_url,
            }
        return self._jobs.enqueue(
            job_type="single_media",
            payload=payload,
            target_label=parsed.canonical_url,
            target_url=parsed.canonical_url,
            status_text="Queued media download.",
            now=now,
        )

    def sync_profile(self, profile_id: str, now: datetime) -> JobSnapshot:
        profile, job = self._jobs.enqueue_active_profile_sync(
            profile_id=profile_id,
            status_text="Queued profile synchronization.",
            now=now,
        )
        if profile is None:
            raise ProfileNotFoundError(profile_id)
        if profile.status != "active":
            raise ProfileNotActiveError(profile_id)
        if not profile.tracked:
            raise ProfileSyncStoppedError(profile_id)
        assert job is not None
        return job

    def set_profile_sync_enabled(
        self, profile_id: str, enabled: bool, now: datetime
    ) -> ProfileSnapshot:
        profile = self._library.get_profile(profile_id)
        if profile is None:
            raise ProfileNotFoundError(profile_id)
        if profile.status != "active":
            raise ProfileNotActiveError(profile_id)
        try:
            updated_profile = self._library.set_profile_sync_enabled(
                profile_id=profile_id,
                enabled=enabled,
                now=now,
            )
        except ValueError as error:
            raise ProfileNotActiveError(profile_id) from error
        if updated_profile is None:
            raise ProfileNotFoundError(profile_id)
        return updated_profile

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
