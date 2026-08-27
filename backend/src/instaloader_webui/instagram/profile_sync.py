"""Story-first, complete and resumable orchestration for one profile sync."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast

from instaloader.nodeiterator import FrozenNodeIterator

from instaloader_webui.instagram.media_types import MediaCandidate
from instaloader_webui.instagram.profile_sync_checkpoints import (
    ProfileSyncCheckpointError,
)
from instaloader_webui.instagram.safe_issues import MediaItemFailure, SafeMediaIssue

ProgressCallback = Callable[[int, int | None, str, str], None]
IssueCallback = Callable[[SafeMediaIssue], None]
SyncableCallback = Callable[[], bool]
FeedSource = Literal["posts", "reels"]
Segment = Literal["stories", "feed"]
SegmentState = Literal["pending", "running", "completed", "failed"]
SegmentProgressCallback = Callable[
    [Segment, SegmentState, "SegmentCounts", int | None, str], None
]

_SAVING_STORIES = "Saving current Instagram Stories before they expire…"
_SCANNING_FEED = "Scanning Instagram Feed content…"
_STOPPED = "Profile synchronization stopped before the next media item."
_BLOCKING_ISSUE_CODES = frozenset(
    {
        "challenge_required",
        "instagram_rate_limited",
        "instagram_session_rejected",
        "instagram_access_denied",
    }
)
_CHECKPOINT_PAGE_SIZE = 12


class CandidateManifest(Protocol):
    @property
    def count(self) -> int | None: ...

    def __iter__(self) -> CandidateManifest: ...

    def __next__(self) -> MediaCandidate: ...

    def freeze(self) -> FrozenNodeIterator: ...

    def thaw(self, frozen: FrozenNodeIterator) -> None: ...


class ProfileMediaSource(Protocol):
    def iter_stories(self, profile: object) -> Iterable[MediaCandidate]: ...


class CandidateProcessor(Protocol):
    def process(
        self,
        candidate: MediaCandidate,
        *,
        job_id: str,
        before_network: Callable[[], None] | None = None,
    ) -> object: ...


class ProfileCheckpointStore(Protocol):
    def get(self, profile_id: str, source: FeedSource) -> object | None: ...

    def save_frozen(
        self,
        *,
        profile_id: str,
        source: FeedSource,
        frozen: FrozenNodeIterator,
        now: datetime,
    ) -> None: ...

    def mark_complete(
        self,
        *,
        profile_id: str,
        source: FeedSource,
        now: datetime,
    ) -> None: ...

    def reset(
        self,
        *,
        profile_id: str,
        source: FeedSource,
        now: datetime,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class SegmentCounts:
    scanned: int = 0
    saved: int = 0
    existing: int = 0
    warnings: int = 0

    def record(self, outcome: Literal["saved", "existing", "warning"]) -> SegmentCounts:
        return replace(
            self,
            scanned=self.scanned + 1,
            saved=self.saved + int(outcome == "saved"),
            existing=self.existing + int(outcome == "existing"),
            warnings=self.warnings + int(outcome == "warning"),
        )


@dataclass(frozen=True, slots=True, init=False)
class ProfileSyncResult:
    stories: SegmentCounts
    feed: SegmentCounts
    stopped: bool
    _total: int | None

    def __init__(
        self,
        processed: int | None = None,
        total: int | None = None,
        issue_count: int | None = None,
        stopped: bool = False,
        *,
        stories: SegmentCounts | None = None,
        feed: SegmentCounts | None = None,
    ) -> None:
        if stories is None and feed is None:
            scanned = processed or 0
            stories = SegmentCounts()
            feed = SegmentCounts(
                scanned=scanned,
                saved=max(scanned - (issue_count or 0), 0),
                warnings=issue_count or 0,
            )
        elif stories is None or feed is None:
            raise ValueError("Both profile sync segments are required.")
        object.__setattr__(self, "stories", stories)
        object.__setattr__(self, "feed", feed)
        object.__setattr__(self, "stopped", stopped)
        inferred_total = None if stopped else stories.scanned + feed.scanned
        object.__setattr__(self, "_total", total if processed is not None else inferred_total)

    @property
    def processed(self) -> int:
        return self.stories.scanned + self.feed.scanned

    @property
    def total(self) -> int | None:
        return self._total

    @property
    def issue_count(self) -> int:
        return self.stories.warnings + self.feed.warnings


@dataclass(slots=True)
class _MemoryManifest:
    iterator: Iterator[MediaCandidate]

    @property
    def count(self) -> None:
        return None

    def __iter__(self) -> _MemoryManifest:
        return self

    def __next__(self) -> MediaCandidate:
        return next(self.iterator)

    def freeze(self) -> FrozenNodeIterator:
        raise RuntimeError("In-memory manifests cannot be checkpointed.")

    def thaw(self, frozen: FrozenNodeIterator) -> None:
        del frozen
        raise RuntimeError("In-memory manifests cannot be resumed.")


@dataclass(slots=True)
class _FeedState:
    source: FeedSource
    manifest: CandidateManifest
    stop_on_existing: bool
    needs_historical: bool
    checkpoint_enabled: bool
    active: bool = True
    head: MediaCandidate | None = None
    head_cursor: FrozenNodeIterator | None = None
    source_scanned: int = 0


@dataclass(slots=True)
class ProfileSyncCoordinator:
    source: ProfileMediaSource
    processor: CandidateProcessor
    progress: ProgressCallback
    record_issue: IssueCallback
    is_syncable: SyncableCallback
    pause_between_new_media: Callable[[], None]
    profile_id: str | None = None
    checkpoints: ProfileCheckpointStore | None = None
    segment_progress: SegmentProgressCallback | None = None

    def run(self, *, profile: object, job_id: str) -> ProfileSyncResult:
        stories = SegmentCounts()
        feed = SegmentCounts()
        self._segment("stories", "pending", stories, None, _SAVING_STORIES)
        self._segment("feed", "pending", feed, None, _SCANNING_FEED)
        self._segment("stories", "running", stories, None, _SAVING_STORIES)
        self.progress(0, None, "saving_stories", _SAVING_STORIES)

        for candidate in self._unique(tuple(self.source.iter_stories(profile))):
            if not self.is_syncable():
                self._segment("stories", "completed", stories, stories.scanned, _STOPPED)
                return ProfileSyncResult(stories=stories, feed=feed, stopped=True)
            outcome = self._process(candidate, job_id=job_id)
            stories = stories.record(outcome)
            self.progress(stories.scanned, None, "saving_stories", _SAVING_STORIES)
            self._segment("stories", "running", stories, None, _SAVING_STORIES)

        self._segment(
            "stories", "completed", stories, stories.scanned, "Stories complete."
        )
        self.progress(stories.scanned, None, "scanning_media", _SCANNING_FEED)
        self._segment("feed", "running", feed, None, _SCANNING_FEED)

        states = self._fresh_states(profile)
        seen: dict[str, Literal["saved", "existing", "warning"]] = {}
        historical_states: list[_FeedState] = []
        try:
            feed, stopped = self._scan_states(
                states=states,
                feed=feed,
                seen=seen,
                job_id=job_id,
            )
            if stopped:
                self._freeze_active(states)
                self._segment("feed", "completed", feed, None, _STOPPED)
                return ProfileSyncResult(stories=stories, feed=feed, stopped=True)

            historical_states = self._historical_states(profile, states)
            feed, stopped = self._scan_states(
                states=historical_states,
                feed=feed,
                seen=seen,
                job_id=job_id,
            )
            if stopped:
                self._freeze_active(historical_states)
                self._segment("feed", "completed", feed, None, _STOPPED)
                return ProfileSyncResult(stories=stories, feed=feed, stopped=True)
        except BaseException:
            self._freeze_active(states)
            self._freeze_active(historical_states)
            self._segment("feed", "failed", feed, None, "Feed content sync failed.")
            raise

        self._segment("feed", "completed", feed, feed.scanned, "Feed content complete.")
        return ProfileSyncResult(stories=stories, feed=feed, stopped=False)

    def _fresh_states(self, profile: object) -> list[_FeedState]:
        states: list[_FeedState] = []
        for source in ("reels", "posts"):
            checkpoint = self._checkpoint(source)
            frozen = getattr(checkpoint, "frozen", None)
            complete = bool(getattr(checkpoint, "backfill_complete", False))
            states.append(
                _FeedState(
                    source=source,
                    manifest=self._open_manifest(profile, source),
                    stop_on_existing=complete or frozen is not None,
                    needs_historical=frozen is not None,
                    checkpoint_enabled=self.checkpoints is not None,
                )
            )
        return states

    def _historical_states(
        self,
        profile: object,
        fresh_states: list[_FeedState],
    ) -> list[_FeedState]:
        historical: list[_FeedState] = []
        for fresh in fresh_states:
            checkpoint = self._checkpoint(fresh.source)
            frozen = getattr(checkpoint, "frozen", None)
            if not fresh.needs_historical or not isinstance(frozen, FrozenNodeIterator):
                continue
            manifest = self._open_manifest(profile, fresh.source)
            try:
                manifest.thaw(frozen)
            except Exception:  # noqa: BLE001 - invalid cursors restart safely
                self._reset(fresh.source)
                manifest = self._open_manifest(profile, fresh.source)
            historical.append(
                _FeedState(
                    source=fresh.source,
                    manifest=manifest,
                    stop_on_existing=False,
                    needs_historical=False,
                    checkpoint_enabled=self.checkpoints is not None,
                )
            )
        return historical

    def _scan_states(
        self,
        *,
        states: list[_FeedState],
        feed: SegmentCounts,
        seen: dict[str, Literal["saved", "existing", "warning"]],
        job_id: str,
    ) -> tuple[SegmentCounts, bool]:
        pause_before_new = False
        for state, candidate in self._merge(states):
            if not self.is_syncable():
                return feed, True
            identity = candidate.identity.value
            outcome = seen.get(identity)
            if outcome is None:
                outcome = self._process(
                    candidate,
                    job_id=job_id,
                    before_network=(
                        self.pause_between_new_media if pause_before_new else None
                    ),
                )
                seen[identity] = outcome
                feed = feed.record(outcome)
                if outcome != "existing":
                    pause_before_new = outcome == "saved"
                self.progress(feed.scanned, None, "processing_feed", _SCANNING_FEED)
                self._segment("feed", "running", feed, None, _SCANNING_FEED)
            if outcome == "existing" and state.stop_on_existing:
                state.active = False
            state.source_scanned += 1
            if (
                state.checkpoint_enabled
                and state.active
                and state.source_scanned % _CHECKPOINT_PAGE_SIZE == 0
            ):
                self._save(state)
        for state in states:
            if not state.active and not state.stop_on_existing and not state.needs_historical:
                self._mark_complete(state.source)
        return feed, False

    def _merge(
        self,
        states: list[_FeedState],
    ) -> Iterator[tuple[_FeedState, MediaCandidate]]:
        for state in states:
            self._load_head(state)
        while any(state.active and state.head is not None for state in states):
            available = [state for state in states if state.active and state.head is not None]
            selected = max(
                available,
                key=lambda state: self._published_at(cast(MediaCandidate, state.head)),
            )
            candidate = cast(MediaCandidate, selected.head)
            yield selected, candidate
            if not selected.active:
                selected.head = None
                continue
            self._load_head(selected)

    @staticmethod
    def _load_head(state: _FeedState) -> None:
        if state.checkpoint_enabled:
            state.head_cursor = state.manifest.freeze()
        try:
            state.head = next(state.manifest)
        except StopIteration:
            state.active = False
            state.head = None

    def _open_manifest(self, profile: object, source: FeedSource) -> CandidateManifest:
        factory = getattr(self.source, "open_feed_manifest", None)
        if callable(factory):
            return cast(CandidateManifest, factory(profile, source))
        iterable = (
            cast(Any, self.source).iter_reels(profile)
            if source == "reels"
            else cast(Any, self.source).iter_posts(profile)
        )
        return _MemoryManifest(iter(iterable))

    def _checkpoint(self, source: FeedSource) -> object | None:
        if self.checkpoints is None or self.profile_id is None:
            return None
        try:
            return self.checkpoints.get(self.profile_id, source)
        except ProfileSyncCheckpointError:
            self._reset(source)
            return None

    def _save(self, state: _FeedState) -> None:
        if self.checkpoints is None or self.profile_id is None:
            return
        self._save_frozen(state.source, state.manifest.freeze())

    def _save_frozen(
        self,
        source: FeedSource,
        frozen: FrozenNodeIterator,
    ) -> None:
        if self.checkpoints is None or self.profile_id is None:
            return
        self.checkpoints.save_frozen(
            profile_id=self.profile_id,
            source=source,
            frozen=frozen,
            now=datetime.now(UTC),
        )

    def _freeze_active(self, states: list[_FeedState]) -> None:
        for state in states:
            if state.active and state.checkpoint_enabled:
                try:
                    if state.head is not None and state.head_cursor is not None:
                        self._save_frozen(state.source, state.head_cursor)
                    else:
                        self._save(state)
                except Exception:  # noqa: BLE001 - do not mask original failure
                    self._reset(state.source)

    def _mark_complete(self, source: FeedSource) -> None:
        if self.checkpoints is not None and self.profile_id is not None:
            self.checkpoints.mark_complete(
                profile_id=self.profile_id,
                source=source,
                now=datetime.now(UTC),
            )

    def _reset(self, source: FeedSource) -> None:
        if self.checkpoints is not None and self.profile_id is not None:
            self.checkpoints.reset(
                profile_id=self.profile_id,
                source=source,
                now=datetime.now(UTC),
            )

    def _process(
        self,
        candidate: MediaCandidate,
        *,
        job_id: str,
        before_network: Callable[[], None] | None = None,
    ) -> Literal["saved", "existing", "warning"]:
        try:
            if before_network is None:
                result = self.processor.process(candidate, job_id=job_id)
            else:
                result = self.processor.process(
                    candidate,
                    job_id=job_id,
                    before_network=before_network,
                )
        except MediaItemFailure as failure:
            if failure.issue.error_code in _BLOCKING_ISSUE_CODES:
                raise
            self.record_issue(failure.issue)
            return "warning"
        status = getattr(result, "status", None)
        return "existing" if status == "existing" else "saved"

    def _segment(
        self,
        segment: Segment,
        state: SegmentState,
        counts: SegmentCounts,
        total: int | None,
        text: str,
    ) -> None:
        if self.segment_progress is not None:
            self.segment_progress(segment, state, counts, total, text)

    @staticmethod
    def _published_at(candidate: MediaCandidate) -> datetime:
        return candidate.published_at_hint or datetime.min.replace(tzinfo=UTC)

    @staticmethod
    def _unique(
        candidates: tuple[MediaCandidate, ...],
    ) -> tuple[MediaCandidate, ...]:
        unique: dict[str, MediaCandidate] = {}
        for candidate in candidates:
            unique.setdefault(candidate.identity.value, candidate)
        return tuple(unique.values())
