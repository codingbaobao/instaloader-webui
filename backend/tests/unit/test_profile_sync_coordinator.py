from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest
from instaloader import BadResponseException

from instaloader_webui.config import Settings
from instaloader_webui.db.library_repositories import (
    LibraryRepository,
    MediaIdentity,
)
from instaloader_webui.db.migrations import run_migrations
from instaloader_webui.instagram.errors import ANONYMOUS_REJECTED, TRANSIENT
from instaloader_webui.instagram.media_types import MediaCandidate, MediaKind
from instaloader_webui.instagram.profile_lookup import ProfileLookupResolver
from instaloader_webui.instagram.profile_sync import (
    ProfileSyncCoordinator,
    ProfileSyncResult,
)
from instaloader_webui.instagram.public_adapter import (
    PublicInstagramAdapterError,
    PublicInstaloaderAdapter,
)
from instaloader_webui.instagram.safe_issues import (
    MediaItemFailure,
    SafeMediaIssue,
)
from instaloader_webui.instagram.worker_runtime import (
    InstagramSessionRevisionError,
)
from instaloader_webui.services.profile_avatars import profile_avatar_path

PROFILE = object()
NOW = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)


def candidate(kind: MediaKind, value: str) -> MediaCandidate:
    identity_type: Literal["shortcode", "story_media_id"] = (
        "story_media_id" if kind == "story" else "shortcode"
    )
    return MediaCandidate(
        identity=MediaIdentity(identity_type, value),
        kind=kind,
        session_configured=True,
        resolve=lambda: (_ for _ in ()).throw(
            AssertionError("coordinator must not resolve candidates")
        ),
    )


def story(value: str) -> MediaCandidate:
    return candidate("story", value)


def reel(value: str) -> MediaCandidate:
    return candidate("reel", value)


def post(value: str) -> MediaCandidate:
    return candidate("post", value)


@dataclass(slots=True)
class RecordingSource:
    stories: Iterable[MediaCandidate] = ()
    reels: Iterable[MediaCandidate] = ()
    posts: Iterable[MediaCandidate] = ()
    events: list[str] = field(default_factory=list)

    def iter_stories(self, profile: object) -> Iterable[MediaCandidate]:
        assert profile is PROFILE
        self.events.append("scan:stories")
        return self.stories

    def iter_reels(self, profile: object) -> Iterable[MediaCandidate]:
        assert profile is PROFILE
        self.events.append("scan:reels")
        return self.reels

    def iter_posts(self, profile: object) -> Iterable[MediaCandidate]:
        assert profile is PROFILE
        self.events.append("scan:posts")
        return self.posts


@dataclass(slots=True)
class RecordingProcessor:
    events: list[str]
    failures: dict[str, BaseException] = field(default_factory=dict)
    processed: list[MediaCandidate] = field(default_factory=list)

    def process(self, item: MediaCandidate, *, job_id: str) -> object:
        assert job_id == "job-1"
        self.events.append(f"process:{item.identity.value}")
        self.processed.append(item)
        failure = self.failures.get(item.identity.value)
        if failure is not None:
            raise failure
        return object()


def item_failure(item: MediaCandidate) -> MediaItemFailure:
    return MediaItemFailure(
        SafeMediaIssue(
            identity=item.identity,
            kind=item.kind,
            error_code="instagram_unavailable",
            safe_message=TRANSIENT,
            exception_class_chain=("BadResponseException",),
        )
    )


def make_coordinator(
    *,
    source: RecordingSource,
    events: list[str],
    failures: dict[str, BaseException] | None = None,
    progress: list[tuple[int, int | None, str, str]] | None = None,
    issues: list[SafeMediaIssue] | None = None,
    processed: list[MediaCandidate] | None = None,
    syncable=lambda: True,
) -> ProfileSyncCoordinator:
    progress_events = progress if progress is not None else []
    recorded_issues = issues if issues is not None else []
    return ProfileSyncCoordinator(
        source=source,
        processor=RecordingProcessor(
            events,
            failures or {},
            processed if processed is not None else [],
        ),
        progress=lambda current, total, phase, text: progress_events.append(
            (current, total, phase, text)
        ),
        record_issue=recorded_issues.append,
        is_syncable=syncable,
    )


def test_sync_saves_stories_before_scanning_reels_and_posts() -> None:
    # Break caught: scanning the long-lived feeds before saving Stories can
    # allow expiring Story items to disappear first.
    events: list[str] = []
    progress: list[tuple[int, int | None, str, str]] = []
    processed: list[MediaCandidate] = []
    source = RecordingSource(
        stories=(story("story-1"), story("story-2")),
        reels=(reel("shared"), reel("reel-only")),
        posts=(post("shared"), post("post-only")),
        events=events,
    )
    coordinator = make_coordinator(
        source=source,
        events=events,
        progress=progress,
        processed=processed,
    )

    result = coordinator.run(profile=PROFILE, job_id="job-1")

    assert events == [
        "scan:stories",
        "process:story-1",
        "process:story-2",
        "scan:reels",
        "scan:posts",
        "process:shared",
        "process:reel-only",
        "process:post-only",
    ]
    assert result.processed == 5
    assert result.total == 5
    assert result.issue_count == 0
    assert result.stopped is False
    assert [(item.kind, item.identity.value) for item in processed] == [
        ("story", "story-1"),
        ("story", "story-2"),
        ("reel", "shared"),
        ("reel", "reel-only"),
        ("post", "post-only"),
    ]
    phase_transitions = [
        event
        for index, event in enumerate(progress)
        if index == 0 or event[2] != progress[index - 1][2]
    ]
    assert phase_transitions == [
        (
            0,
            None,
            "saving_stories",
            "Saving current Instagram stories before they expire…",
        ),
        (
            2,
            None,
            "scanning_media",
            "Scanning Instagram posts and reels…",
        ),
        (2, 5, "processing_reels", "Processing Instagram reels."),
        (4, 5, "processing_posts", "Processing Instagram posts."),
    ]
    assert [current for current, *_rest in progress] == [
        0,
        1,
        2,
        2,
        2,
        3,
        4,
        4,
        5,
    ]
    assert [total for _current, total, *_rest in progress[:4]] == [
        None,
        None,
        None,
        None,
    ]


def test_empty_story_manifest_is_valid_and_scan_sets_an_exact_total() -> None:
    # Break caught: treating no current Stories as a fatal lookup failure
    # prevents ordinary profile syncs when the account has nothing ephemeral.
    events: list[str] = []
    progress: list[tuple[int, int | None, str, str]] = []
    source = RecordingSource(reels=(reel("reel-1"),), events=events)

    result = make_coordinator(
        source=source,
        events=events,
        progress=progress,
    ).run(profile=PROFILE, job_id="job-1")

    assert events == [
        "scan:stories",
        "scan:reels",
        "scan:posts",
        "process:reel-1",
    ]
    assert result.processed == 1
    assert result.total == 1
    assert progress[0][:3] == (0, None, "saving_stories")
    assert progress[1][:3] == (0, None, "scanning_media")
    assert progress[2][:3] == (0, 1, "processing_reels")


def test_duplicate_story_media_ids_produce_one_outcome() -> None:
    # Break caught: omitting Story-manifest deduplication downloads and counts
    # the same ephemeral item twice when upstream repeats it.
    events: list[str] = []
    source = RecordingSource(
        stories=(story("201"), story("201")),
        events=events,
    )

    result = make_coordinator(source=source, events=events).run(
        profile=PROFILE,
        job_id="job-1",
    )

    assert events.count("process:201") == 1
    assert result.processed == 1
    assert result.total == 1


def test_existing_item_outcomes_advance_progress_once() -> None:
    # Break caught: advancing only newly downloaded items leaves progress
    # permanently below the exact manifest total when items already exist.
    events: list[str] = []
    progress: list[tuple[int, int | None, str, str]] = []
    source = RecordingSource(stories=(story("existing"),), events=events)

    result = make_coordinator(
        source=source,
        events=events,
        progress=progress,
    ).run(profile=PROFILE, job_id="job-1")

    assert result.processed == 1
    assert [event[0] for event in progress if event[2] == "saving_stories"] == [
        0,
        1,
    ]


def test_item_failure_records_one_warning_continues_and_advances_once() -> None:
    # Break caught: a broad profile failure or a missing finally-style outcome
    # increment stops later candidates or undercounts a failed item.
    failed = story("101")
    events: list[str] = []
    progress: list[tuple[int, int | None, str, str]] = []
    issues: list[SafeMediaIssue] = []
    source = RecordingSource(
        stories=(failed, story("102")),
        events=events,
    )

    result = make_coordinator(
        source=source,
        events=events,
        failures={"101": item_failure(failed)},
        progress=progress,
        issues=issues,
    ).run(profile=PROFILE, job_id="job-1")

    assert events == [
        "scan:stories",
        "process:101",
        "process:102",
        "scan:reels",
        "scan:posts",
    ]
    assert issues == [item_failure(failed).issue]
    assert result.processed == 2
    assert result.issue_count == 1
    assert result.total == 2
    assert [event[0] for event in progress[:3]] == [0, 1, 2]


def test_non_item_processor_error_remains_fatal() -> None:
    # Break caught: catching Exception around processor calls would downgrade
    # filesystem and database corruption to a routine media warning.
    events: list[str] = []
    source = RecordingSource(stories=(story("broken"),), events=events)

    with pytest.raises(OSError, match="disk unavailable"):
        make_coordinator(
            source=source,
            events=events,
            failures={"broken": OSError("disk unavailable")},
        ).run(profile=PROFILE, job_id="job-1")


def test_iterator_interruption_escapes_before_incomplete_manifest_processing() -> None:
    # Break caught: processing a partially enumerated manifest would publish an
    # invented exact total and silently omit media after an iterator failure.
    events: list[str] = []

    def interrupted_reels() -> Iterable[MediaCandidate]:
        yield reel("partial")
        raise ConnectionError("feed interrupted")

    source = RecordingSource(
        stories=(story("saved-first"),),
        reels=interrupted_reels(),
        events=events,
    )

    with pytest.raises(ConnectionError, match="feed interrupted"):
        make_coordinator(source=source, events=events).run(
            profile=PROFILE,
            job_id="job-1",
        )

    assert events == [
        "scan:stories",
        "process:saved-first",
        "scan:reels",
    ]


def test_stop_is_polled_only_before_processing_manifest_items() -> None:
    # Break caught: polling while enumerating can abandon a manifest midway and
    # violates the rule that Stop Sync takes effect only between item outcomes.
    events: list[str] = []
    checks = iter((True, False))
    source = RecordingSource(
        stories=(story("story-1"),),
        reels=(reel("reel-1"),),
        posts=(post("post-1"),),
        events=events,
    )

    result = make_coordinator(
        source=source,
        events=events,
        syncable=lambda: next(checks),
    ).run(profile=PROFILE, job_id="job-1")

    assert events == [
        "scan:stories",
        "process:story-1",
        "scan:reels",
        "scan:posts",
    ]
    assert result.processed == 1
    assert result.total == 3
    assert result.stopped is True


@dataclass(slots=True)
class AdapterProfile:
    userid: int = 8_642_097_531
    username: str = "katerina.soria"
    full_name: str = "Katerina Soria"
    biography: str = "Biography"
    profile_pic_url: str | None = "https://cdn.example/avatar-standard.jpg"
    is_private: bool = False
    followed_by_viewer: bool = True
    events: list[str] = field(default_factory=list)

    def get_reels(self) -> Iterable[object]:
        self.events.append("scan:reels")
        return ()

    def get_posts(self) -> Iterable[object]:
        self.events.append("scan:posts")
        return ()


class AvatarResponse:
    def __init__(
        self,
        *,
        content_type: str = "image/jpeg",
        content: bytes = b"avatar-image",
    ) -> None:
        self.headers = {"Content-Type": content_type}
        self.raw = BytesIO(content)
        self.closed = False

    def close(self) -> None:
        self.closed = True
        self.raw.close()


class AdapterLoader:
    def __init__(
        self,
        *,
        events: list[str],
        response: AvatarResponse | BaseException,
        logged_in: bool = True,
    ) -> None:
        self.events = events
        self.response = response
        self.context = SimpleNamespace(
            is_logged_in=logged_in,
            get_raw=self.get_raw,
        )
        self.dirname_pattern = ""

    def get_raw(self, url: str) -> AvatarResponse:
        self.events.append(f"avatar:{url}")
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response

    def get_stories(self, *, userids: list[int]) -> Iterable[object]:
        assert userids == [AdapterProfile().userid]
        self.events.append("scan:stories")
        return ()


class AdapterRuntime:
    def __init__(
        self,
        loader: AdapterLoader,
        *,
        configured: bool = True,
    ) -> None:
        self.loader = loader
        self.configured = configured

    def acquire(self, staging_directory: Path) -> tuple[AdapterLoader, bool]:
        self.loader.events.append("session:acquire")
        self.loader.dirname_pattern = str(staging_directory)
        return self.loader, self.configured

    def acquire_required_session(
        self,
        staging_directory: Path,
    ) -> AdapterLoader:
        loader, configured = self.acquire(staging_directory)
        if not configured or not loader.context.is_logged_in:
            raise InstagramSessionRevisionError(
                "session required",
                session_configured=configured,
            )
        return loader


class RecordingProfileLookupResolver:
    def __init__(
        self,
        result: AdapterProfile | BaseException,
        *,
        events: list[str],
    ) -> None:
        self._result = result
        self._events = events
        self.calls: list[tuple[object, str]] = []

    def resolve(self, context: object, username: str) -> AdapterProfile:
        self.calls.append((context, username))
        self._events.append(f"resolve:{username}")
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result


@pytest.fixture
def profile_repository(
    session_factory,
    test_settings: Settings,
) -> LibraryRepository:
    run_migrations(test_settings)
    return LibraryRepository(session_factory)


@pytest.fixture
def tracked_profile(profile_repository: LibraryRepository):
    return profile_repository.upsert_profile_stub(
        username=AdapterProfile().username,
        tracked=True,
        now=NOW,
    )


def make_profile_adapter(
    *,
    test_settings: Settings,
    repository: LibraryRepository,
    loader: AdapterLoader,
    profile_lookup_resolver: RecordingProfileLookupResolver,
    configured: bool = True,
    issues: list[SafeMediaIssue] | None = None,
    progress: list[tuple[Any, ...]] | None = None,
) -> PublicInstaloaderAdapter:
    return PublicInstaloaderAdapter(
        data_root=test_settings.data_root,
        media_root=test_settings.media_root,
        jobs_root=test_settings.jobs_root,
        library=repository,
        progress=lambda *args: (progress if progress is not None else []).append(
            args
        ),
        issue=(issues if issues is not None else []).append,
        loader_runtime=AdapterRuntime(  # type: ignore[arg-type]
            loader,
            configured=configured,
        ),
        profile_lookup_resolver=cast(
            ProfileLookupResolver,
            profile_lookup_resolver,
        ),
    )


def test_fetch_profile_uses_injected_resolver_and_normalizes_result(
    monkeypatch: pytest.MonkeyPatch,
    profile_repository: LibraryRepository,
    test_settings: Settings,
) -> None:
    # Break caught: bypassing the injected lookup resolver would restore the
    # rate-limited native Profile.from_username boundary in fetch_profile().
    events: list[str] = []
    upstream = AdapterProfile(events=events)
    resolver = RecordingProfileLookupResolver(upstream, events=events)
    monkeypatch.setattr(
        "instaloader_webui.instagram.public_adapter.Profile.from_username",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("native lookup must stay inside the resolver")
        ),
    )
    loader = AdapterLoader(events=events, response=AvatarResponse())
    adapter = make_profile_adapter(
        test_settings=test_settings,
        repository=profile_repository,
        loader=loader,
        profile_lookup_resolver=resolver,
    )

    profile = adapter.fetch_profile(upstream.username)

    assert profile.instagram_user_id == upstream.userid
    assert profile.username == upstream.username
    assert profile.full_name == upstream.full_name
    assert profile.biography == upstream.biography
    assert profile.profile_pic_url == upstream.profile_pic_url
    assert resolver.calls == [(loader.context, upstream.username)]
    assert events == ["session:acquire", f"resolve:{upstream.username}"]


def test_profile_sync_accepts_standard_avatar_before_story_enumeration(
    profile_repository: LibraryRepository,
    tracked_profile,
    test_settings: Settings,
) -> None:
    # Break caught: requiring a non-portable HD-only Profile attribute rejects
    # the standard profile_pic_url that Instaloader exposes and delays Stories.
    events: list[str] = []
    upstream = AdapterProfile(events=events)
    resolver = RecordingProfileLookupResolver(upstream, events=events)
    response = AvatarResponse()
    progress: list[tuple[Any, ...]] = []
    loader = AdapterLoader(events=events, response=response)
    adapter = make_profile_adapter(
        test_settings=test_settings,
        repository=profile_repository,
        loader=loader,
        profile_lookup_resolver=resolver,
        progress=progress,
    )

    result = adapter.sync_profile(tracked_profile.id, "job-avatar")

    assert isinstance(result, ProfileSyncResult)
    assert result == ProfileSyncResult(
        processed=0,
        total=0,
        issue_count=0,
        stopped=False,
    )
    assert events == [
        "session:acquire",
        f"resolve:{upstream.username}",
        "avatar:https://cdn.example/avatar-standard.jpg",
        "scan:stories",
        "scan:reels",
        "scan:posts",
    ]
    assert [event[2] for event in progress] == [
        "profile_preflight",
        "saving_stories",
        "scanning_media",
        "processing_reels",
    ]
    assert resolver.calls == [(loader.context, upstream.username)]
    assert profile_avatar_path(
        test_settings.media_root,
        tracked_profile.id,
    ).read_bytes() == b"avatar-image"
    refreshed = profile_repository.get_profile(tracked_profile.id)
    assert refreshed is not None
    assert refreshed.instagram_user_id == str(upstream.userid)
    assert refreshed.last_sync_attempted_at is not None
    assert refreshed.last_sync_succeeded_at is not None
    assert response.closed is True


def test_profile_sync_requires_session_before_profile_or_avatar_lookup(
    profile_repository: LibraryRepository,
    tracked_profile,
    test_settings: Settings,
) -> None:
    # Break caught: anonymous profile metadata can appear readable, but profile
    # sync cannot enumerate Stories and must reject the session at preflight.
    events: list[str] = []
    resolver = RecordingProfileLookupResolver(
        AdapterProfile(events=events),
        events=events,
    )
    loader = AdapterLoader(
        events=events,
        response=AvatarResponse(),
        logged_in=False,
    )
    adapter = make_profile_adapter(
        test_settings=test_settings,
        repository=profile_repository,
        loader=loader,
        profile_lookup_resolver=resolver,
        configured=False,
    )

    with pytest.raises(PublicInstagramAdapterError, match=ANONYMOUS_REJECTED):
        adapter.sync_profile(tracked_profile.id, "job-no-session")

    assert resolver.calls == []
    assert events == ["session:acquire"]
    failed = profile_repository.get_profile(tracked_profile.id)
    assert failed is not None
    assert failed.last_sync_attempted_at is not None
    assert failed.last_sync_succeeded_at is None


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (
            BadResponseException("avatar transport failed"),
            "Instagram could not be reached",
        ),
        (
            AvatarResponse(content_type="text/html"),
            "invalid profile avatar image",
        ),
    ],
)
def test_profile_sync_avatar_failure_is_fatal_before_story_enumeration(
    profile_repository: LibraryRepository,
    tracked_profile,
    test_settings: Settings,
    response: AvatarResponse | BaseException,
    message: str,
) -> None:
    # Break caught: downgrading strict profile-sync avatar failures would scan
    # and save media for a Profile whose required preflight is incomplete.
    events: list[str] = []
    resolver = RecordingProfileLookupResolver(
        AdapterProfile(events=events),
        events=events,
    )
    adapter = make_profile_adapter(
        test_settings=test_settings,
        repository=profile_repository,
        loader=AdapterLoader(events=events, response=response),
        profile_lookup_resolver=resolver,
    )

    with pytest.raises(PublicInstagramAdapterError, match=message):
        adapter.sync_profile(tracked_profile.id, "job-bad-avatar")

    assert "scan:stories" not in events
    failed = profile_repository.get_profile(tracked_profile.id)
    assert failed is not None
    assert failed.last_sync_attempted_at is not None
    assert failed.last_sync_succeeded_at is None


def test_profile_sync_classifies_resolver_failure_before_avatar_or_coordinator(
    profile_repository: LibraryRepository,
    tracked_profile,
    test_settings: Settings,
) -> None:
    # Break caught: resolving outside the existing adapter boundary can leak a
    # terminal lookup exception or begin avatar/Story work after lookup fails.
    events: list[str] = []
    resolver = RecordingProfileLookupResolver(
        BadResponseException("sensitive upstream response"),
        events=events,
    )
    loader = AdapterLoader(events=events, response=AvatarResponse())
    adapter = make_profile_adapter(
        test_settings=test_settings,
        repository=profile_repository,
        loader=loader,
        profile_lookup_resolver=resolver,
    )

    with pytest.raises(PublicInstagramAdapterError, match=TRANSIENT):
        adapter.sync_profile(tracked_profile.id, "job-lookup-failure")

    assert resolver.calls == [(loader.context, tracked_profile.username)]
    assert events == [
        "session:acquire",
        f"resolve:{tracked_profile.username}",
    ]


def test_profile_sync_warning_result_still_records_success(
    monkeypatch: pytest.MonkeyPatch,
    profile_repository: LibraryRepository,
    tracked_profile,
    test_settings: Settings,
) -> None:
    # Break caught: equating item warnings with a fatal sync loses the last
    # successful sync timestamp even though the coordinator finished.
    events: list[str] = []
    issues: list[SafeMediaIssue] = []
    resolver = RecordingProfileLookupResolver(
        AdapterProfile(events=events),
        events=events,
    )
    warning = SafeMediaIssue(
        identity=MediaIdentity("shortcode", "warning-item"),
        kind="reel",
        error_code="instagram_unavailable",
        safe_message=TRANSIENT,
        exception_class_chain=("BadResponseException",),
    )
    monkeypatch.setattr(
        ProfileSyncCoordinator,
        "run",
        lambda self, *, profile, job_id: (
            self.record_issue(warning),
            ProfileSyncResult(
                processed=1,
                total=1,
                issue_count=1,
                stopped=False,
            ),
        )[1],
    )
    adapter = make_profile_adapter(
        test_settings=test_settings,
        repository=profile_repository,
        loader=AdapterLoader(events=events, response=AvatarResponse()),
        profile_lookup_resolver=resolver,
        issues=issues,
    )

    result = adapter.sync_profile(tracked_profile.id, "job-warning")

    assert result.issue_count == 1
    assert issues == [warning]
    synced = profile_repository.get_profile(tracked_profile.id)
    assert synced is not None
    assert synced.last_sync_succeeded_at is not None
