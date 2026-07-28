"""Execute claimed public-library jobs in the single worker process."""

from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Callable
from urllib.parse import urlsplit

from instaloader_webui.db.library_repositories import (
    JobRepository,
    JobSnapshot,
    LibraryRepository,
)
from instaloader_webui.instagram.public_adapter import (
    PublicInstagramAdapterError,
    PublicInstaloaderAdapter,
)
from instaloader_webui.instagram.session_store import InstagramSessionStore
from instaloader_webui.services.profile_avatars import profile_avatar_path

_MAXIMUM_ERROR_LENGTH = 240


class JobRunner:
    """Run one already-claimed job and record its terminal state."""

    def __init__(
        self,
        *,
        data_root: Path,
        media_root: Path,
        jobs_root: Path,
        library: LibraryRepository,
        jobs: JobRepository,
        instagram_sessions: InstagramSessionStore,
    ) -> None:
        self._data_root = data_root
        self._media_root = media_root.resolve()
        self._jobs_root = jobs_root
        self._library = library
        self._jobs = jobs
        self._instagram_sessions = instagram_sessions

    def run(self, job: JobSnapshot) -> None:
        """Dispatch a claimed job and persist success or a concise failure."""
        try:
            self._progress(job, 0, None, "Starting worker job.")
            self._dispatch(job)
        except Exception as error:
            try:
                self._record_profile_sync_failure(job)
                self._record_profile_deletion_failure(job)
            except Exception:
                # The original operation failure remains the job's useful outcome.
                pass
            self._jobs.fail(
                job_id=job.id,
                error=shorten_job_error(error),
                now=datetime.now(UTC),
            )
        else:
            completed = self._jobs.get(job.id)
            status_text = (
                completed.status_text
                if completed is not None and completed.status_text
                else "Worker job completed."
            )
            self._jobs.succeed(
                job_id=job.id,
                status_text=status_text,
                now=datetime.now(UTC),
            )

    def _dispatch(self, job: JobSnapshot) -> None:
        if job.type == "profile_sync":
            profile_id = _required_payload_text(job, "profile_id")
            self._adapter(job).sync_profile(profile_id, job.id)
            return
        if job.type == "single_media":
            shortcode = _required_payload_text(job, "shortcode")
            original_url = _required_payload_text(job, "original_url")
            self._adapter(job).download_shortcode(
                shortcode,
                job.id,
                expected_kind=_expected_kind(original_url),
                original_url=original_url,
            )
            return
        if job.type == "delete_media":
            self._delete_media(job, _required_payload_text(job, "media_id"))
            return
        if job.type == "delete_profile":
            self._delete_profile(job, _required_payload_text(job, "profile_id"))
            return
        raise ValueError(f"Unsupported worker job type: {job.type}")

    def _adapter(self, job: JobSnapshot) -> PublicInstaloaderAdapter:
        return PublicInstaloaderAdapter(
            data_root=self._data_root,
            media_root=self._media_root,
            jobs_root=self._jobs_root,
            library=self._library,
            instagram_sessions=self._instagram_sessions,
            progress=lambda current, total, status_text: self._progress(
                job, current, total, status_text
            ),
        )

    def _delete_media(self, job: JobSnapshot, media_id: str) -> None:
        media = self._library.get_media(media_id)
        if media is None:
            self._progress(job, 0, 0, "Media records were already removed.")
            return
        total = len(media.assets)
        for current, asset in enumerate(media.assets, start=1):
            self._delete_asset(asset.relative_path)
            self._progress(job, current, total, "Removed local media file.")
        self._library.delete_media_records(media.id)

    def _delete_profile(self, job: JobSnapshot, profile_id: str) -> None:
        profile = self._library.get_profile(profile_id)
        if profile is None:
            self._progress(job, 0, 0, "Profile records were already removed.")
            return
        media_items = self._library.list_all_media_for_profile(profile_id)
        assets = tuple(asset for media in media_items for asset in media.assets)
        avatar_path = profile_avatar_path(self._media_root, profile_id)
        avatar_exists = avatar_path.is_file()
        avatar_count = 1 if avatar_exists else 0
        total = len(assets) + avatar_count
        if avatar_exists:
            avatar_path.unlink()
            self._progress(job, 1, total, "Removed local profile avatar.")
        for current, asset in enumerate(assets, start=avatar_count + 1):
            self._delete_asset(asset.relative_path)
            self._progress(job, current, total, "Removed local profile media file.")
        self._library.delete_profile_records(profile_id)

    def _delete_asset(self, relative_path: str) -> None:
        candidate = self._media_root / relative_path
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self._media_root):
            raise ValueError("Recorded media path is outside the media library.")
        candidate.unlink(missing_ok=True)

    def _progress(
        self,
        job: JobSnapshot,
        current: int,
        total: int | None,
        status_text: str,
    ) -> None:
        self._jobs.update_progress(
            job_id=job.id,
            current=current,
            total=total,
            status_text=status_text,
            now=datetime.now(UTC),
        )

    def _record_profile_sync_failure(self, job: JobSnapshot) -> None:
        if job.type != "profile_sync":
            return
        profile_id = job.payload.get("profile_id")
        if not isinstance(profile_id, str) or not profile_id:
            return
        if self._library.get_profile(profile_id) is not None:
            self._library.set_profile_sync_result(
                profile_id=profile_id,
                succeeded=False,
                now=datetime.now(UTC),
            )

    def _record_profile_deletion_failure(self, job: JobSnapshot) -> None:
        if job.type != "delete_profile":
            return
        profile_id = job.payload.get("profile_id")
        if not isinstance(profile_id, str) or not profile_id:
            return
        self._library.mark_profile_deletion_failed(
            profile_id,
            datetime.now(UTC),
        )


def enqueue_due_profile_syncs(
    *,
    library: LibraryRepository,
    jobs: JobRepository,
    now: datetime,
    settings_claim_due_sync: Callable[[datetime], bool],
) -> int:
    """Coalesce one due synchronization job for each tracked active profile."""
    if not settings_claim_due_sync(now):
        return 0
    enqueued = 0
    for profile in library.list_profiles():
        if not profile.tracked or profile.status != "active":
            continue
        before = jobs.has_active_profile_sync(profile.id)
        jobs.enqueue_profile_sync(
            profile_id=profile.id,
            status_text="Queued scheduled profile synchronization.",
            now=now,
        )
        if not before:
            enqueued += 1
    return enqueued


def shorten_job_error(error: Exception) -> str:
    """Avoid persisting long upstream URLs, paths, or traceback-like messages."""
    if isinstance(error, PublicInstagramAdapterError):
        message = str(error)
    elif isinstance(error, OSError):
        detail = error.strerror or error.__class__.__name__
        message = f"Filesystem operation failed: {detail}"
    elif error.__class__.__module__.startswith("instaloader"):
        message = "Instagram operation failed."
    else:
        message = str(error) or error.__class__.__name__
    compact = " ".join(message.split())
    return compact[:_MAXIMUM_ERROR_LENGTH]


def _required_payload_text(job: JobSnapshot, key: str) -> str:
    value = job.payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Worker job is missing {key}.")
    return value


def _expected_kind(original_url: str) -> str:
    path_parts = tuple(
        part.casefold() for part in urlsplit(original_url).path.split("/") if part
    )
    return "reel" if path_parts[:1] == ("reel",) else "post"
