"""Story-first, resumable orchestration for one Instagram profile sync."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from instaloader_webui.instagram.media_types import MediaCandidate
from instaloader_webui.instagram.safe_issues import (
    MediaItemFailure,
    SafeMediaIssue,
)

ProgressCallback = Callable[[int, int | None, str, str], None]
IssueCallback = Callable[[SafeMediaIssue], None]
SyncableCallback = Callable[[], bool]

_SAVING_STORIES = "Saving current Instagram stories before they expire…"
_SCANNING_MEDIA = "Scanning Instagram posts and reels…"
_PROCESSING_REELS = "Processing Instagram reels."
_PROCESSING_POSTS = "Processing Instagram posts."
_STOPPED = "Profile synchronization stopped before the next media item."
_BACKFILL_PENDING = (
    "Profile sync time slice ended. More profile history will continue on the "
    "next scheduled sync."
)
_BLOCKING_ISSUE_CODES = frozenset(
    {
        "challenge_required",
        "instagram_rate_limited",
        "instagram_session_rejected",
        "instagram_access_denied",
    }
)


class ProfileMediaSource(Protocol):
    """Provide lazy media-candidate iterables for one resolved profile."""

    def iter_stories(self, profile: object) -> Iterable[MediaCandidate]: ...

    def iter_reels(self, profile: object) -> Iterable[MediaCandidate]: ...

    def iter_posts(self, profile: object) -> Iterable[MediaCandidate]: ...


class CandidateProcessor(Protocol):
    """Process one candidate without exposing its concrete storage boundary."""

    def process(self, candidate: MediaCandidate, *, job_id: str) -> object: ...


@dataclass(frozen=True, slots=True)
class ProfileSyncResult:
    """The complete orchestration outcome returned to the job runner."""

    processed: int
    total: int | None
    issue_count: int
    stopped: bool
    backfill_pending: bool = False


@dataclass(slots=True)
class ProfileSyncCoordinator:
    """Save Stories first, then merge recent Reels and Posts lazily."""

    source: ProfileMediaSource
    processor: CandidateProcessor
    progress: ProgressCallback
    record_issue: IssueCallback
    is_syncable: SyncableCallback
    monotonic: Callable[[], float]
    pause_between_new_media: Callable[[], None]
    time_slice_seconds: float

    def run(self, *, profile: object, job_id: str) -> ProfileSyncResult:
        """Run one bounded sync while keeping infrastructure errors fatal."""
        processed = 0
        issue_count = 0

        self.progress(0, None, "saving_stories", _SAVING_STORIES)
        stories = self._unique(tuple(self.source.iter_stories(profile)))
        for candidate in stories:
            if not self.is_syncable():
                self.progress(processed, None, "saving_stories", _STOPPED)
                return ProfileSyncResult(
                    processed=processed,
                    total=None,
                    issue_count=issue_count,
                    stopped=True,
                )
            failed, _saved = self._process(candidate, job_id=job_id)
            processed += 1
            issue_count += int(failed)
            self.progress(processed, None, "saving_stories", _SAVING_STORIES)

        self.progress(processed, None, "scanning_media", _SCANNING_MEDIA)
        candidates = self._merge_long_lived(
            self.source.iter_reels(profile),
            self.source.iter_posts(profile),
        )
        long_lived_started_at = self.monotonic()
        pause_before_candidate = False
        active_phase: str | None = None
        active_text = ""

        for candidate in candidates:
            phase, status_text = self._phase(candidate)
            if phase != active_phase:
                active_phase = phase
                active_text = status_text
                self.progress(processed, None, phase, status_text)
            if self._time_slice_expired(long_lived_started_at):
                return self._backfill_pending_result(
                    processed=processed,
                    issue_count=issue_count,
                    phase=phase,
                )
            if pause_before_candidate:
                self.pause_between_new_media()
                if self._time_slice_expired(long_lived_started_at):
                    return self._backfill_pending_result(
                        processed=processed,
                        issue_count=issue_count,
                        phase=phase,
                    )
            if not self.is_syncable():
                self.progress(processed, None, phase, _STOPPED)
                return ProfileSyncResult(
                    processed=processed,
                    total=None,
                    issue_count=issue_count,
                    stopped=True,
                )

            failed, saved = self._process(candidate, job_id=job_id)
            processed += 1
            issue_count += int(failed)
            pause_before_candidate = saved
            self.progress(processed, None, phase, active_text)

        return ProfileSyncResult(
            processed=processed,
            total=processed,
            issue_count=issue_count,
            stopped=False,
        )

    def _time_slice_expired(self, started_at: float) -> bool:
        return self.monotonic() - started_at >= self.time_slice_seconds

    def _backfill_pending_result(
        self,
        *,
        processed: int,
        issue_count: int,
        phase: str,
    ) -> ProfileSyncResult:
        self.progress(processed, None, phase, _BACKFILL_PENDING)
        return ProfileSyncResult(
            processed=processed,
            total=None,
            issue_count=issue_count,
            stopped=False,
            backfill_pending=True,
        )

    def _process(
        self,
        candidate: MediaCandidate,
        *,
        job_id: str,
    ) -> tuple[bool, bool]:
        try:
            result = self.processor.process(candidate, job_id=job_id)
        except MediaItemFailure as failure:
            if failure.issue.error_code in _BLOCKING_ISSUE_CODES:
                raise
            self.record_issue(failure.issue)
            return True, False
        return False, getattr(result, "status", None) == "saved"

    @classmethod
    def _merge_long_lived(
        cls,
        reels: Iterable[MediaCandidate],
        posts: Iterable[MediaCandidate],
    ) -> Iterator[MediaCandidate]:
        reel_iterator = iter(reels)
        post_iterator = iter(posts)
        reel_candidate = next(reel_iterator, None)
        post_candidate = next(post_iterator, None)
        seen: set[str] = set()

        while reel_candidate is not None or post_candidate is not None:
            use_reel = post_candidate is None or (
                reel_candidate is not None
                and cls._published_at(reel_candidate)
                >= cls._published_at(post_candidate)
            )
            if use_reel:
                selected = reel_candidate
                selected_iterator = reel_iterator
            else:
                selected = post_candidate
                selected_iterator = post_iterator
            if selected is None:  # pragma: no cover - guarded by loop state.
                return

            if selected.identity.value not in seen:
                seen.add(selected.identity.value)
                yield selected

            next_candidate = next(selected_iterator, None)
            if use_reel:
                reel_candidate = next_candidate
            else:
                post_candidate = next_candidate

    @staticmethod
    def _published_at(candidate: MediaCandidate) -> datetime:
        return candidate.published_at_hint or datetime.min.replace(tzinfo=UTC)

    @staticmethod
    def _phase(candidate: MediaCandidate) -> tuple[str, str]:
        if candidate.kind == "reel":
            return "processing_reels", _PROCESSING_REELS
        return "processing_posts", _PROCESSING_POSTS

    @staticmethod
    def _unique(
        candidates: tuple[MediaCandidate, ...],
    ) -> tuple[MediaCandidate, ...]:
        unique: dict[str, MediaCandidate] = {}
        for candidate in candidates:
            unique.setdefault(candidate.identity.value, candidate)
        return tuple(unique.values())
