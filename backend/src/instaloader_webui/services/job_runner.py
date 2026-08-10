"""Execute claimed public-library jobs in the single worker process."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from instaloader_webui.db.followee_import_repositories import (
    FolloweeImportRepository,
)
from instaloader_webui.db.library_repositories import (
    JobIssueInput,
    JobRepository,
    JobSnapshot,
    LibraryRepository,
)
from instaloader_webui.instagram.followee_discovery import (
    FolloweeDiscoveryAdapter,
    FolloweeDiscoveryError,
)
from instaloader_webui.instagram.profile_lookup import ProfileLookupResolver
from instaloader_webui.instagram.public_adapter import (
    PublicInstagramAdapterError,
    PublicInstaloaderAdapter,
)
from instaloader_webui.instagram.safe_issues import (
    MediaItemFailure,
    SafeMediaIssue,
    log_media_issue,
)
from instaloader_webui.instagram.worker_runtime import WorkerInstaloaderRuntime
from instaloader_webui.services.instagram_inputs import (
    PostInput,
    ReelInput,
    StoryInput,
)
from instaloader_webui.services.profile_avatars import profile_avatar_candidates

_MAXIMUM_ERROR_LENGTH = 240
_MEDIA_ISSUE_REPORTING_FAILED = "Media issue reporting failed."
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _DispatchOutcome:
    warning_count: int = 0
    backfill_pending: bool = False


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
        followee_imports: FolloweeImportRepository,
        loader_runtime: WorkerInstaloaderRuntime,
        profile_lookup_resolver: ProfileLookupResolver,
    ) -> None:
        self._data_root = data_root
        self._media_root = media_root.resolve()
        self._jobs_root = jobs_root
        self._library = library
        self._jobs = jobs
        self._followee_imports = followee_imports
        self._loader_runtime = loader_runtime
        self._profile_lookup_resolver = profile_lookup_resolver

    def run(self, job: JobSnapshot) -> None:
        """Dispatch a claimed job and persist success or a concise failure."""
        try:
            self._progress(job, 0, None, "Starting worker job.")
            outcome = self._dispatch(job)
        except MediaItemFailure as failure:
            try:
                self._record_media_issue(job, failure.issue)
            except Exception:  # noqa: BLE001
                self._fail_job(
                    job,
                    RuntimeError(_MEDIA_ISSUE_REPORTING_FAILED),
                )
            else:
                self._jobs.fail(
                    job_id=job.id,
                    error=failure.issue.safe_message,
                    now=datetime.now(UTC),
                )
        except Exception as error:  # noqa: BLE001
            self._fail_job(job, error)
        else:
            completed = self._jobs.get(job.id)
            status_text = (
                completed.status_text
                if completed is not None and completed.status_text
                else "Worker job completed."
            )
            now = datetime.now(UTC)
            if outcome.warning_count:
                warning_status = (
                    "Saved 25 new posts and reels with "
                    f"{outcome.warning_count} warning(s). More profile history "
                    "will continue on the next scheduled sync."
                    if outcome.backfill_pending
                    else f"Completed with {outcome.warning_count} warning(s)."
                )
                self._jobs.complete_with_warnings(
                    job_id=job.id,
                    status_text=warning_status,
                    now=now,
                )
            else:
                self._jobs.succeed(
                    job_id=job.id,
                    status_text=status_text,
                    now=now,
                )

    def _fail_job(self, job: JobSnapshot, error: Exception) -> None:
        safe_error = shorten_job_error(error)
        try:
            self._record_profile_sync_failure(job)
            self._record_profile_deletion_failure(job)
            self._record_followee_discovery_failure(job, safe_error)
        except Exception:  # noqa: BLE001, S110
            # The original operation failure remains the job's useful outcome.
            pass
        self._jobs.fail(
            job_id=job.id,
            error=safe_error,
            now=datetime.now(UTC),
        )

    def _dispatch(self, job: JobSnapshot) -> _DispatchOutcome:
        if job.type == "profile_sync":
            profile_id = _required_payload_text(job, "profile_id")
            result = self._adapter(job).sync_profile(profile_id, job.id)
            return _DispatchOutcome(
                warning_count=result.issue_count,
                backfill_pending=result.backfill_pending,
            )
        if job.type == "single_media":
            self._adapter(job).download_input(_decode_media_input(job), job.id)
            return _DispatchOutcome()
        if job.type == "delete_media":
            self._delete_media(job, _required_payload_text(job, "media_id"))
            return _DispatchOutcome()
        if job.type == "delete_profile":
            self._delete_profile(job, _required_payload_text(job, "profile_id"))
            return _DispatchOutcome()
        if job.type == "followee_discovery":
            self._discover_followees(
                job,
                _required_payload_text(job, "batch_id"),
            )
            return _DispatchOutcome()
        raise ValueError(f"Unsupported worker job type: {job.type}")

    def _discover_followees(self, job: JobSnapshot, batch_id: str) -> None:
        batch = self._followee_imports.get(batch_id)
        if batch is None:
            raise ValueError("Followee discovery batch does not exist.")
        self._followee_imports.start_discovery(
            batch_id=batch_id,
            job_id=job.id,
        )
        adapter = FolloweeDiscoveryAdapter(
            jobs_root=self._jobs_root,
            loader_runtime=self._loader_runtime,
            profile_lookup_resolver=self._profile_lookup_resolver,
            progress=lambda current, total, status_text: self._progress(
                job,
                current,
                total,
                status_text,
            ),
        )
        discovered = adapter.discover(
            source_username=batch.source_username,
            session_imported_at=batch.session_imported_at,
        )
        self._followee_imports.complete_discovery(
            batch_id=batch_id,
            followees=discovered,
            now=datetime.now(UTC),
        )
        self._progress(
            job,
            len(discovered),
            len(discovered),
            f"Found {len(discovered)} followed Instagram accounts.",
        )

    def _adapter(self, job: JobSnapshot) -> PublicInstaloaderAdapter:
        return PublicInstaloaderAdapter(
            data_root=self._data_root,
            media_root=self._media_root,
            jobs_root=self._jobs_root,
            library=self._library,
            loader_runtime=self._loader_runtime,
            profile_lookup_resolver=self._profile_lookup_resolver,
            progress=lambda current, total, phase, status_text: self._progress(
                job,
                current,
                total,
                status_text,
                phase=phase,
            ),
            issue=lambda issue: self._record_media_issue(job, issue),
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
        avatar_paths = tuple(
            avatar.path
            for avatar in profile_avatar_candidates(self._media_root, profile_id)
            if avatar.path.is_file()
        )
        avatar_count = 1 if avatar_paths else 0
        total = len(assets) + avatar_count
        if avatar_paths:
            for avatar_path in avatar_paths:
                avatar_path.unlink(missing_ok=True)
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
        *,
        phase: str | None = None,
    ) -> None:
        self._jobs.update_progress(
            job_id=job.id,
            current=current,
            total=total,
            status_text=status_text,
            now=datetime.now(UTC),
            phase=phase,
        )

    def _record_media_issue(
        self,
        job: JobSnapshot,
        issue: SafeMediaIssue,
    ) -> None:
        self._jobs.record_issue(
            job_id=job.id,
            issue=JobIssueInput(
                identity_type=issue.identity.identity_type,
                identity_value=issue.identity.value,
                media_kind=issue.kind,
                error_code=issue.error_code,
                safe_message=issue.safe_message,
                exception_class_chain=issue.exception_class_chain,
            ),
            now=datetime.now(UTC),
        )
        log_media_issue(_LOGGER, job_id=job.id, issue=issue)

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

    def _record_followee_discovery_failure(
        self,
        job: JobSnapshot,
        safe_error: str,
    ) -> None:
        if job.type != "followee_discovery":
            return
        batch_id = job.payload.get("batch_id")
        if not isinstance(batch_id, str) or not batch_id:
            return
        self._followee_imports.fail_discovery(
            batch_id=batch_id,
            error=safe_error,
            now=datetime.now(UTC),
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
    if isinstance(error, (PublicInstagramAdapterError, FolloweeDiscoveryError)):
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


def _decode_media_input(job: JobSnapshot) -> PostInput | ReelInput | StoryInput:
    media_kind = _required_payload_text(job, "media_kind")
    identity_type = _required_payload_text(job, "identity_type")
    identity_value = _required_payload_text(job, "identity_value")
    original_url = _required_payload_text(job, "original_url")

    if media_kind in {"post", "reel"}:
        shortcode = _required_payload_text(job, "shortcode")
        if identity_type != "shortcode" or identity_value != shortcode:
            raise ValueError("Worker job has inconsistent media identity.")
        input_type = ReelInput if media_kind == "reel" else PostInput
        return input_type(shortcode=shortcode, canonical_url=original_url)

    if media_kind == "story":
        story_media_id = _required_payload_text(job, "story_media_id")
        if (
            identity_type != "story_media_id"
            or identity_value != story_media_id
        ):
            raise ValueError("Worker job has inconsistent media identity.")
        return StoryInput(
            username=_required_payload_text(job, "username"),
            story_media_id=story_media_id,
            canonical_url=original_url,
        )

    raise ValueError("Worker job has unsupported media kind.")
