"""Story-first orchestration for one tracked Instagram profile sync."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
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


@dataclass(slots=True)
class ProfileSyncCoordinator:
    """Save Stories first, then scan and process unique Reels and Posts."""

    source: ProfileMediaSource
    processor: CandidateProcessor
    progress: ProgressCallback
    record_issue: IssueCallback
    is_syncable: SyncableCallback

    def run(self, *, profile: object, job_id: str) -> ProfileSyncResult:
        """Run one sync while keeping iterator and infrastructure errors fatal."""
        processed = 0
        issue_count = 0

        self.progress(0, None, "saving_stories", _SAVING_STORIES)
        stories = self._unique(tuple(self.source.iter_stories(profile)))
        for candidate in stories:
            if not self.is_syncable():
                self.progress(
                    processed,
                    None,
                    "saving_stories",
                    _STOPPED,
                )
                return ProfileSyncResult(
                    processed=processed,
                    total=None,
                    issue_count=issue_count,
                    stopped=True,
                )
            failed = self._process(candidate, job_id=job_id)
            processed += 1
            issue_count += int(failed)
            self.progress(
                processed,
                None,
                "saving_stories",
                _SAVING_STORIES,
            )

        self.progress(
            processed,
            None,
            "scanning_media",
            _SCANNING_MEDIA,
        )
        reels = self._unique(tuple(self.source.iter_reels(profile)))
        posts = self._unique(tuple(self.source.iter_posts(profile)))
        reel_shortcodes = {candidate.identity.value for candidate in reels}
        posts = tuple(
            candidate
            for candidate in posts
            if candidate.identity.value not in reel_shortcodes
        )
        total = processed + len(reels) + len(posts)

        self.progress(
            processed,
            total,
            "processing_reels",
            _PROCESSING_REELS,
        )
        for candidate in reels:
            if not self.is_syncable():
                self.progress(
                    processed,
                    total,
                    "processing_reels",
                    _STOPPED,
                )
                return ProfileSyncResult(
                    processed=processed,
                    total=total,
                    issue_count=issue_count,
                    stopped=True,
                )
            failed = self._process(candidate, job_id=job_id)
            processed += 1
            issue_count += int(failed)
            self.progress(
                processed,
                total,
                "processing_reels",
                _PROCESSING_REELS,
            )

        if posts:
            self.progress(
                processed,
                total,
                "processing_posts",
                _PROCESSING_POSTS,
            )
        for candidate in posts:
            if not self.is_syncable():
                self.progress(
                    processed,
                    total,
                    "processing_posts",
                    _STOPPED,
                )
                return ProfileSyncResult(
                    processed=processed,
                    total=total,
                    issue_count=issue_count,
                    stopped=True,
                )
            failed = self._process(candidate, job_id=job_id)
            processed += 1
            issue_count += int(failed)
            self.progress(
                processed,
                total,
                "processing_posts",
                _PROCESSING_POSTS,
            )

        return ProfileSyncResult(
            processed=processed,
            total=total,
            issue_count=issue_count,
            stopped=False,
        )

    def _process(self, candidate: MediaCandidate, *, job_id: str) -> bool:
        try:
            self.processor.process(candidate, job_id=job_id)
        except MediaItemFailure as failure:
            self.record_issue(failure.issue)
            return True
        return False

    @staticmethod
    def _unique(
        candidates: tuple[MediaCandidate, ...],
    ) -> tuple[MediaCandidate, ...]:
        unique: dict[str, MediaCandidate] = {}
        for candidate in candidates:
            unique.setdefault(candidate.identity.value, candidate)
        return tuple(unique.values())
