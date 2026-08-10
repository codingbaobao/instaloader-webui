from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest
from instaloader import (
    AbortDownloadException,
    BadResponseException,
    LoginRequiredException,
    Profile,
    TooManyRequestsException,
)
from requests import Response
from requests.exceptions import HTTPError

from instaloader_webui.config import Settings
from instaloader_webui.db.library_repositories import (
    LibraryRepository,
    MediaIdentity,
)
from instaloader_webui.db.schema import initialize_database
from instaloader_webui.instagram.errors import (
    ANONYMOUS_REJECTED,
    PROFILE_NOT_FOUND,
    RATE_LIMITED,
    SESSION_REJECTED,
    TRANSIENT,
)
from instaloader_webui.instagram.media_types import MediaCandidate, MediaKind
from instaloader_webui.instagram.profile_lookup import (
    ProfileLookupMode,
    ProfileLookupResolver,
)
from instaloader_webui.instagram.profile_sync import (
    ProfileSyncCoordinator,
    ProfileSyncResult,
)
from instaloader_webui.instagram.public_adapter import (
    PublicInstagramAdapterError,
    PublicInstaloaderAdapter,
    _avatar_prefix_diagnostic,
    _avatar_prefix_kind,
    _safe_avatar_diagnostic_value,
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
JPEG_AVATAR = b"\xff\xd8\xff\xe0avatar-image"
WEBP_AVATAR = b"RIFF\x0c\x00\x00\x00WEBPVP8 \x00\x00\x00\x00"


def candidate(
    kind: MediaKind,
    value: str,
    *,
    published_at: datetime | None = None,
) -> MediaCandidate:
    identity_type: Literal["shortcode", "story_media_id"] = (
        "story_media_id" if kind == "story" else "shortcode"
    )
    return MediaCandidate(
        identity=MediaIdentity(identity_type, value),
        kind=kind,
        session_configured=True,
        published_at_hint=published_at,
        resolve=lambda: (_ for _ in ()).throw(
            AssertionError("coordinator must not resolve candidates")
        ),
    )


def story(value: str) -> MediaCandidate:
    return candidate("story", value)


def reel(
    value: str,
    *,
    published_at: datetime | None = None,
) -> MediaCandidate:
    return candidate("reel", value, published_at=published_at)


def post(
    value: str,
    *,
    published_at: datetime | None = None,
) -> MediaCandidate:
    return candidate("post", value, published_at=published_at)


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
    statuses: dict[str, Literal["saved", "existing"]] = field(
        default_factory=dict
    )

    def process(self, item: MediaCandidate, *, job_id: str) -> object:
        assert job_id == "job-1"
        self.events.append(f"process:{item.identity.value}")
        self.processed.append(item)
        failure = self.failures.get(item.identity.value)
        if failure is not None:
            raise failure
        return SimpleNamespace(status=self.statuses.get(item.identity.value, "saved"))


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
    statuses: dict[str, Literal["saved", "existing"]] | None = None,
    syncable=lambda: True,
    monotonic=lambda: 0.0,
    pause_between_new_media=lambda: None,
    time_slice_seconds: float = 300,
) -> ProfileSyncCoordinator:
    progress_events = progress if progress is not None else []
    recorded_issues = issues if issues is not None else []
    return ProfileSyncCoordinator(
        source=source,
        processor=RecordingProcessor(
            events,
            failures or {},
            processed if processed is not None else [],
            statuses or {},
        ),
        progress=lambda current, total, phase, text: progress_events.append(
            (current, total, phase, text)
        ),
        record_issue=recorded_issues.append,
        is_syncable=syncable,
        monotonic=monotonic,
        pause_between_new_media=pause_between_new_media,
        time_slice_seconds=time_slice_seconds,
    )


def rate_limited_failure(item: MediaCandidate) -> MediaItemFailure:
    return MediaItemFailure(
        SafeMediaIssue(
            identity=item.identity,
            kind=item.kind,
            error_code="instagram_rate_limited",
            safe_message=RATE_LIMITED,
            exception_class_chain=("TooManyRequestsException",),
        )
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
        (2, None, "processing_reels", "Processing Instagram reels."),
        (4, None, "processing_posts", "Processing Instagram posts."),
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


def test_empty_story_manifest_is_valid_and_finishes_with_an_exact_total() -> None:
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
    assert progress[2][:3] == (0, None, "processing_reels")


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


def test_blocking_media_failure_stops_before_the_next_candidate() -> None:
    # Break caught: treating a 429 as an item warning keeps issuing Instagram
    # requests and deepens a temporary server-side rate limit.
    blocked = reel("rate-limited", published_at=NOW)
    later = post("must-not-run", published_at=NOW - timedelta(minutes=1))
    events: list[str] = []
    issues: list[SafeMediaIssue] = []
    source = RecordingSource(
        reels=(blocked,),
        posts=(later,),
        events=events,
    )

    with pytest.raises(MediaItemFailure) as raised:
        make_coordinator(
            source=source,
            events=events,
            failures={blocked.identity.value: rate_limited_failure(blocked)},
            issues=issues,
        ).run(profile=PROFILE, job_id="job-1")

    assert raised.value.issue.error_code == "instagram_rate_limited"
    assert "process:must-not-run" not in events
    # The job runner owns persistence of the one terminal issue.
    assert issues == []


def test_posts_and_reels_are_lazily_interleaved_newest_first() -> None:
    # Break caught: draining every Reel before Posts starves recent Posts on
    # profiles with a large Reel history.
    events: list[str] = []
    processed: list[MediaCandidate] = []
    source = RecordingSource(
        reels=(
            reel("shared", published_at=NOW),
            reel("reel-older", published_at=NOW - timedelta(minutes=3)),
        ),
        posts=(
            post("shared", published_at=NOW),
            post("post-middle", published_at=NOW - timedelta(minutes=1)),
            post("post-oldest", published_at=NOW - timedelta(minutes=4)),
        ),
        events=events,
    )

    result = make_coordinator(
        source=source,
        events=events,
        processed=processed,
    ).run(profile=PROFILE, job_id="job-1")

    assert [(item.kind, item.identity.value) for item in processed] == [
        ("reel", "shared"),
        ("post", "post-middle"),
        ("reel", "reel-older"),
        ("post", "post-oldest"),
    ]
    assert result.processed == 4
    assert result.backfill_pending is False


def test_fast_backfill_is_not_limited_to_25_new_saves() -> None:
    # Break caught: retaining the per-profile item cap stops a fast, well-paced
    # sync even though its fair worker time slice has not expired.
    events: list[str] = []
    processed: list[MediaCandidate] = []
    reels = tuple(
        reel(
            f"reel-{index:02d}",
            published_at=NOW - timedelta(minutes=index),
        )
        for index in range(30)
    )

    result = make_coordinator(
        source=RecordingSource(reels=reels, events=events),
        events=events,
        processed=processed,
    ).run(profile=PROFILE, job_id="job-1")

    assert [item.identity.value for item in processed] == [
        f"reel-{index:02d}" for index in range(30)
    ]
    assert result.processed == 30
    assert result.total == 30
    assert result.backfill_pending is False


def test_long_lived_backfill_pauses_before_item_after_time_slice_expires() -> None:
    # Break caught: omitting elapsed-time checks lets one large profile occupy
    # the single worker indefinitely.
    times = iter((0.0, 0.0, 301.0))
    events: list[str] = []
    progress: list[tuple[int, int | None, str, str]] = []
    processed: list[MediaCandidate] = []
    source = RecordingSource(
        reels=(
            reel("first", published_at=NOW),
            reel("deferred", published_at=NOW - timedelta(minutes=1)),
        ),
        events=events,
    )

    result = make_coordinator(
        source=source,
        events=events,
        monotonic=lambda: next(times),
        processed=processed,
        progress=progress,
    ).run(profile=PROFILE, job_id="job-1")

    assert [item.identity.value for item in processed] == ["first"]
    assert result.processed == 1
    assert result.total is None
    assert result.backfill_pending is True
    assert progress[-1][3] == (
        "Profile sync time slice ended. More profile history will continue on "
        "the next scheduled sync."
    )


def test_stories_are_saved_before_long_lived_time_slice_starts() -> None:
    # Break caught: starting the five-minute slice before Stories can defer
    # expiring content when Story enumeration or download is slow.
    times = iter((1000.0, 1301.0))
    events: list[str] = []
    source = RecordingSource(
        stories=(story("story-first"),),
        reels=(reel("deferred", published_at=NOW),),
        events=events,
    )

    result = make_coordinator(
        source=source,
        events=events,
        monotonic=lambda: next(times),
    ).run(profile=PROFILE, job_id="job-1")

    assert events == [
        "scan:stories",
        "process:story-first",
        "scan:reels",
        "scan:posts",
    ]
    assert result.processed == 1
    assert result.backfill_pending is True


def test_new_media_pause_occurs_only_before_a_following_candidate() -> None:
    # Break caught: sleeping for existing checkpoints or after the final item
    # adds latency without smoothing a subsequent Instagram download.
    events: list[str] = []
    pauses: list[str] = []
    source = RecordingSource(
        reels=(
            reel("saved", published_at=NOW),
            reel("existing", published_at=NOW - timedelta(minutes=1)),
            reel("saved-last", published_at=NOW - timedelta(minutes=2)),
        ),
        events=events,
    )

    result = make_coordinator(
        source=source,
        events=events,
        pause_between_new_media=lambda: pauses.append("pause"),
        statuses={"existing": "existing"},
    ).run(profile=PROFILE, job_id="job-1")

    assert result.processed == 3
    assert pauses == ["pause"]


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


def test_iterator_interruption_escapes_after_durable_lazy_item_processing() -> None:
    # Break caught: eagerly materializing a large manifest delays every save and
    # discards all prior iterator work when a later page fails.
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
        "scan:posts",
        "process:partial",
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
    assert result.total is None
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
        content: bytes = JPEG_AVATAR,
        status_code: int = 200,
        url: str = "https://cdn.example/avatar-standard.jpg",
        history: tuple[object, ...] = (),
        additional_headers: Mapping[str, str] | None = None,
        raw: BytesIO | None = None,
    ) -> None:
        self.headers = {"Content-Type": content_type}
        if additional_headers is not None:
            self.headers.update(additional_headers)
        self.status_code = status_code
        self.url = url
        self.history = history
        self.raw = raw if raw is not None else BytesIO(content)
        self.closed = False

    def close(self) -> None:
        self.closed = True
        self.raw.close()


class UnreadableAvatarBody(BytesIO):
    def read(self, size: int = -1, /) -> bytes:
        del size
        raise OSError("Cookie: sessionid=body-read-secret")


@pytest.mark.parametrize(
    ("prefix", "expected_kind"),
    [
        (b"", "empty"),
        (b"\xff\xd8\xff\xe0", "jpeg"),
        (b"\x89PNG\r\n\x1a\n", "png"),
        (b"RIFF\x00\x00\x00\x00WEBP", "webp"),
        (b"GIF87a", "gif"),
        (b"GIF89a", "gif"),
        (b" \n<!doctype html>", "html"),
        (b'{"error":"rate limited"}', "json"),
        (b"upstream response", "other"),
    ],
)
def test_avatar_prefix_kind_identifies_bounded_response_evidence(
    prefix: bytes,
    expected_kind: str,
) -> None:
    assert _avatar_prefix_kind(prefix) == expected_kind


def test_avatar_diagnostics_bound_text_and_redact_sensitive_body_prefix() -> None:
    bounded = _safe_avatar_diagnostic_value("line one\n" + "x" * 200)
    prefix_hex, prefix_kind = _avatar_prefix_diagnostic(
        SimpleNamespace(raw=BytesIO(b"<html>Cookie: sessionid=secret"))
    )

    assert len(bounded) == 160
    assert bounded.startswith("line one ")
    assert bounded.endswith("…")
    assert prefix_hex == "[redacted]"
    assert prefix_kind == "html"


class AdapterLoader:
    def __init__(
        self,
        *,
        events: list[str],
        response: AvatarResponse | BaseException,
        logged_in: bool = True,
        legacy_profile_node: dict[str, object] | None = None,
    ) -> None:
        self.events = events
        self.response = response
        self.legacy_profile_node = legacy_profile_node
        self.legacy_query_calls: list[
            tuple[str, Mapping[str, object]]
        ] = []
        self.context = SimpleNamespace(
            is_logged_in=logged_in,
            iphone_support=False,
            get_raw=self.get_raw,
            doc_id_graphql_query=self.doc_id_graphql_query,
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

    def doc_id_graphql_query(
        self,
        doc_id: str,
        variables: Mapping[str, object],
    ) -> dict[str, object]:
        self.legacy_query_calls.append((doc_id, variables))
        self.events.append("lookup:legacy")
        if self.legacy_profile_node is None:
            raise AssertionError("legacy Profile lookup was not expected")
        return {
            "data": {
                "xdt_api__v1__fbsearch__non_profiled_serp": {
                    "users": [self.legacy_profile_node],
                }
            }
        }


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
    initialize_database(test_settings)
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
    profile_lookup_resolver: (
        ProfileLookupResolver | RecordingProfileLookupResolver
    ),
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


@pytest.mark.parametrize(
    ("legacy_result", "expected_message"),
    [
        (
            {
                "data": {
                    "xdt_api__v1__fbsearch__non_profiled_serp": {
                        "users": [],
                    }
                }
            },
            PROFILE_NOT_FOUND,
        ),
        ({}, TRANSIENT),
        (
            LoginRequiredException("legacy-login-secret"),
            SESSION_REJECTED,
        ),
    ],
)
def test_fetch_profile_fallback_classifies_the_legacy_terminal_outcome(
    monkeypatch: pytest.MonkeyPatch,
    profile_repository: LibraryRepository,
    test_settings: Settings,
    legacy_result: object,
    expected_message: str,
) -> None:
    # Break caught: traversing the native 429 cause when classifying the final
    # legacy failure misreports every terminal legacy outcome as rate limited.
    native_error = TooManyRequestsException("native-rate-limit-secret")
    native_calls: list[tuple[object, str]] = []
    legacy_calls: list[tuple[str, Mapping[str, object]]] = []

    def native_lookup(context: object, username: str) -> Profile:
        native_calls.append((context, username))
        raise native_error

    def legacy_lookup(
        doc_id: str,
        variables: Mapping[str, object],
    ) -> object:
        legacy_calls.append((doc_id, variables))
        if isinstance(legacy_result, BaseException):
            raise legacy_result
        return legacy_result

    monkeypatch.setattr(Profile, "from_username", staticmethod(native_lookup))
    loader = AdapterLoader(events=[], response=AvatarResponse())
    loader.context.doc_id_graphql_query = legacy_lookup
    adapter = make_profile_adapter(
        test_settings=test_settings,
        repository=profile_repository,
        loader=loader,
        profile_lookup_resolver=ProfileLookupResolver(
            "fallback",
            logging.getLogger(__name__),
        ),
    )

    with pytest.raises(PublicInstagramAdapterError) as raised:
        adapter.fetch_profile("Target")

    assert str(raised.value) == expected_message
    assert "secret" not in str(raised.value)
    assert native_calls == [(loader.context, "Target")]
    assert legacy_calls == [
        (
            "26347858941511777",
            {"hasQuery": True, "query": "Target"},
        )
    ]
    terminal_failure = raised.value.__cause__
    assert terminal_failure is not None
    assert terminal_failure.__cause__ is native_error


@pytest.mark.parametrize(
    ("mode", "status_code", "expected_message"),
    [
        ("native", 503, TRANSIENT),
        ("legacy", 404, PROFILE_NOT_FOUND),
    ],
)
def test_fetch_profile_classifies_raw_requests_http_failures_safely(
    monkeypatch: pytest.MonkeyPatch,
    profile_repository: LibraryRepository,
    test_settings: Settings,
    mode: ProfileLookupMode,
    status_code: int,
    expected_message: str,
) -> None:
    # Break caught: requests.HTTPError is not an InstaloaderException, so a raw
    # transport message can otherwise cross the public adapter boundary.
    response = Response()
    response.status_code = status_code
    raw_error = HTTPError(
        "raw-http-secret Cookie: sessionid=transport-secret",
        response=response,
    )
    native_calls: list[str] = []

    def native_lookup(_context: object, username: str) -> Profile:
        native_calls.append(username)
        raise raw_error

    monkeypatch.setattr(Profile, "from_username", staticmethod(native_lookup))
    loader = AdapterLoader(events=[], response=AvatarResponse())
    loader.context.doc_id_graphql_query = lambda *_args: (_ for _ in ()).throw(
        raw_error
    )
    adapter = make_profile_adapter(
        test_settings=test_settings,
        repository=profile_repository,
        loader=loader,
        profile_lookup_resolver=ProfileLookupResolver(
            mode,
            logging.getLogger(__name__),
        ),
    )

    with pytest.raises(PublicInstagramAdapterError) as raised:
        adapter.fetch_profile("Target")

    assert str(raised.value) == expected_message
    assert "raw-http-secret" not in str(raised.value)
    assert "transport-secret" not in str(raised.value)
    assert len(native_calls) == (1 if mode == "native" else 0)


def test_profile_sync_accepts_standard_avatar_before_story_enumeration(
    caplog: pytest.LogCaptureFixture,
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
    stale_webp = (
        test_settings.media_root
        / "profile-avatars"
        / f"{tracked_profile.id}.webp"
    )
    stale_webp.parent.mkdir(parents=True)
    stale_webp.write_bytes(WEBP_AVATAR)
    progress: list[tuple[Any, ...]] = []
    loader = AdapterLoader(events=events, response=response)
    adapter = make_profile_adapter(
        test_settings=test_settings,
        repository=profile_repository,
        loader=loader,
        profile_lookup_resolver=resolver,
        progress=progress,
    )

    with caplog.at_level(
        logging.WARNING,
        logger="instaloader_webui.instagram.public_adapter",
    ):
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
    ]
    assert resolver.calls == [(loader.context, upstream.username)]
    assert profile_avatar_path(
        test_settings.media_root,
        tracked_profile.id,
    ).read_bytes() == JPEG_AVATAR
    assert stale_webp.exists() is False
    refreshed = profile_repository.get_profile(tracked_profile.id)
    assert refreshed is not None
    assert refreshed.instagram_user_id == str(upstream.userid)
    assert refreshed.last_sync_attempted_at is not None
    assert refreshed.last_sync_succeeded_at is not None
    assert response.closed is True
    assert "instagram_profile_avatar_invalid_response" not in caplog.text


def test_profile_sync_safely_classifies_abort_during_reel_enumeration(
    monkeypatch: pytest.MonkeyPatch,
    profile_repository: LibraryRepository,
    tracked_profile,
    test_settings: Settings,
) -> None:
    # Break caught: AbortDownloadException is outside InstaloaderException's
    # hierarchy and used to become the unhelpful "Instagram operation failed."
    events: list[str] = []
    upstream = AdapterProfile(events=events)

    def interrupted_reels(profile: AdapterProfile) -> Iterable[object]:
        profile.events.append("scan:reels")

        def items() -> Iterable[object]:
            raise AbortDownloadException(
                "Instagram worker request was rate limited."
            )
            yield  # pragma: no cover - preserves generator typing.

        return items()

    monkeypatch.setattr(AdapterProfile, "get_reels", interrupted_reels)
    loader = AdapterLoader(events=events, response=AvatarResponse())
    adapter = make_profile_adapter(
        test_settings=test_settings,
        repository=profile_repository,
        loader=loader,
        profile_lookup_resolver=RecordingProfileLookupResolver(
            upstream,
            events=events,
        ),
    )

    with pytest.raises(PublicInstagramAdapterError) as raised:
        adapter.sync_profile(tracked_profile.id, "job-aborted-reels")

    assert str(raised.value) == RATE_LIMITED
    refreshed = profile_repository.get_profile(tracked_profile.id)
    assert refreshed is not None
    assert refreshed.last_sync_attempted_at is not None
    assert refreshed.last_sync_succeeded_at is None


def test_profile_sync_preserves_webp_avatar_and_removes_stale_jpeg(
    caplog: pytest.LogCaptureFixture,
    profile_repository: LibraryRepository,
    tracked_profile,
    test_settings: Settings,
) -> None:
    # Break caught: restricting avatar persistence to JPEG rejects a valid CDN
    # WebP response or leaves the previous JPEG shadowing the refreshed image.
    events: list[str] = []
    upstream = AdapterProfile(events=events)
    resolver = RecordingProfileLookupResolver(upstream, events=events)
    response = AvatarResponse(
        content_type="image/webp",
        content=WEBP_AVATAR,
    )
    stale_jpeg = profile_avatar_path(
        test_settings.media_root,
        tracked_profile.id,
    )
    stale_jpeg.parent.mkdir(parents=True)
    stale_jpeg.write_bytes(JPEG_AVATAR)
    adapter = make_profile_adapter(
        test_settings=test_settings,
        repository=profile_repository,
        loader=AdapterLoader(events=events, response=response),
        profile_lookup_resolver=resolver,
    )

    with caplog.at_level(
        logging.WARNING,
        logger="instaloader_webui.instagram.public_adapter",
    ):
        result = adapter.sync_profile(tracked_profile.id, "job-webp-avatar")

    webp_path = (
        test_settings.media_root
        / "profile-avatars"
        / f"{tracked_profile.id}.webp"
    )
    assert result.processed == 0
    assert webp_path.read_bytes() == WEBP_AVATAR
    assert stale_jpeg.exists() is False
    assert response.closed is True
    assert "instagram_profile_avatar_invalid_response" not in caplog.text


def test_profile_sync_real_fallback_continues_through_profile_flow(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    profile_repository: LibraryRepository,
    tracked_profile,
    test_settings: Settings,
) -> None:
    # Break caught: testing sync only with a recording resolver can hide a
    # failure between the real typed-429 legacy result and normal profile work.
    events: list[str] = []
    progress: list[tuple[Any, ...]] = []
    native_calls: list[tuple[object, str]] = []
    legacy_node = {
        "id": AdapterProfile().userid,
        "username": AdapterProfile().username,
        "is_private": False,
        "followed_by_viewer": True,
        "full_name": "Fallback Profile",
        "biography": "Fallback biography",
        "profile_pic_url_hd": "https://cdn.example/fallback-avatar.jpg",
    }

    def native_lookup(context: object, username: str) -> Profile:
        native_calls.append((context, username))
        events.append("lookup:native")
        raise TooManyRequestsException("private native rate-limit detail")

    def get_reels(_profile: Profile) -> tuple[object, ...]:
        events.append("scan:reels")
        return ()

    def get_posts(_profile: Profile) -> tuple[object, ...]:
        events.append("scan:posts")
        return ()

    monkeypatch.setattr(Profile, "from_username", staticmethod(native_lookup))
    monkeypatch.setattr(Profile, "get_reels", get_reels)
    monkeypatch.setattr(Profile, "get_posts", get_posts)
    loader = AdapterLoader(
        events=events,
        response=AvatarResponse(),
        legacy_profile_node=legacy_node,
    )
    logger_name = "test.profile_sync.real_fallback"
    adapter = make_profile_adapter(
        test_settings=test_settings,
        repository=profile_repository,
        loader=loader,
        profile_lookup_resolver=ProfileLookupResolver(
            "fallback",
            logging.getLogger(logger_name),
        ),
        progress=progress,
    )

    with caplog.at_level(logging.INFO, logger=logger_name):
        result = adapter.sync_profile(tracked_profile.id, "job-real-fallback")

    assert result == ProfileSyncResult(
        processed=0,
        total=0,
        issue_count=0,
        stopped=False,
    )
    assert native_calls == [(loader.context, tracked_profile.username)]
    assert loader.legacy_query_calls == [
        (
            "26347858941511777",
            {"hasQuery": True, "query": tracked_profile.username},
        )
    ]
    assert events == [
        "session:acquire",
        "lookup:native",
        "lookup:legacy",
        "avatar:https://cdn.example/fallback-avatar.jpg",
        "scan:stories",
        "scan:reels",
        "scan:posts",
    ]
    assert [event[2] for event in progress] == [
        "profile_preflight",
        "saving_stories",
        "scanning_media",
    ]
    refreshed = profile_repository.get_profile(tracked_profile.id)
    assert refreshed is not None
    assert refreshed.instagram_user_id == str(legacy_node["id"])
    assert refreshed.full_name == "Fallback Profile"
    assert profile_avatar_path(
        test_settings.media_root,
        tracked_profile.id,
    ).read_bytes() == JPEG_AVATAR
    lookup_records = [
        record for record in caplog.records if record.name == logger_name
    ]
    assert [
        (
            record.__dict__["mode"],
            record.__dict__["path"],
            record.__dict__["outcome"],
            record.__dict__["status_class"],
        )
        for record in lookup_records
    ] == [
        ("fallback", "native", "fallback", "rate_limited"),
        ("fallback", "legacy", "success", "success"),
    ]
    assert all(
        record.getMessage() == "instagram_profile_lookup"
        for record in lookup_records
    )
    assert tracked_profile.username not in caplog.text


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


def test_profile_sync_rejects_avatar_when_media_type_and_magic_disagree(
    caplog: pytest.LogCaptureFixture,
    profile_repository: LibraryRepository,
    tracked_profile,
    test_settings: Settings,
) -> None:
    # Break caught: trusting only the response header can persist JPEG bytes as
    # WebP and make the avatar endpoint return a false Content-Type.
    response = AvatarResponse(
        content_type="image/webp",
        content=JPEG_AVATAR,
    )
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

    with (
        caplog.at_level(
            logging.WARNING,
            logger="instaloader_webui.instagram.public_adapter",
        ),
        pytest.raises(
            PublicInstagramAdapterError,
            match="invalid profile avatar image",
        ),
    ):
        adapter.sync_profile(tracked_profile.id, "job-mismatched-avatar")

    assert "normalized_content_type='image/webp'" in caplog.text
    assert "prefix_kind='jpeg'" in caplog.text
    assert response.closed is True
    assert (
        test_settings.media_root
        / "profile-avatars"
        / f"{tracked_profile.id}.webp"
    ).exists() is False


def test_profile_sync_supported_avatar_read_failure_preserves_error(
    caplog: pytest.LogCaptureFixture,
    profile_repository: LibraryRepository,
    tracked_profile,
    test_settings: Settings,
) -> None:
    # Break caught: a body read failure after MIME acceptance must not leak an
    # OSError or bypass the bounded invalid-avatar diagnostic.
    response = AvatarResponse(
        content_type="image/webp",
        raw=UnreadableAvatarBody(),
    )
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

    with (
        caplog.at_level(
            logging.WARNING,
            logger="instaloader_webui.instagram.public_adapter",
        ),
        pytest.raises(PublicInstagramAdapterError) as raised,
    ):
        adapter.sync_profile(tracked_profile.id, "job-unreadable-webp")

    assert str(raised.value) == "Instagram returned an invalid profile avatar image."
    assert "prefix_kind='unreadable'" in caplog.text
    assert "body-read-secret" not in caplog.text
    assert response.closed is True


def test_invalid_profile_avatar_logs_bounded_response_diagnostics(
    caplog: pytest.LogCaptureFixture,
    profile_repository: LibraryRepository,
    tracked_profile,
    test_settings: Settings,
) -> None:
    # Break caught: rejecting a non-JPEG response without bounded evidence
    # makes an upstream HTML or rate-limit response impossible to diagnose.
    avatar_url = (
        "https://scontent-lax3-1.cdninstagram.com/profile/avatar.jpg"
        "?token=signed-secret"
    )
    body = (
        b"<!doctype html><html>temporary upstream page</html>"
        + b"X" * 32
        + b"body-secret-beyond-prefix"
    )
    response = AvatarResponse(
        content_type="text/html; charset=utf-8",
        content=body,
        status_code=200,
        url=avatar_url,
        history=(object(), object()),
        additional_headers={
            "Content-Length": str(len(body)),
            "Content-Encoding": "gzip Cookie=session-secret",
            "Transfer-Encoding": "chunked",
        },
    )
    events: list[str] = []
    resolver = RecordingProfileLookupResolver(
        AdapterProfile(profile_pic_url=avatar_url, events=events),
        events=events,
    )
    adapter = make_profile_adapter(
        test_settings=test_settings,
        repository=profile_repository,
        loader=AdapterLoader(events=events, response=response),
        profile_lookup_resolver=resolver,
    )

    with (
        caplog.at_level(
            logging.WARNING,
            logger="instaloader_webui.instagram.public_adapter",
        ),
        pytest.raises(
            PublicInstagramAdapterError,
            match="invalid profile avatar image",
        ),
    ):
        adapter.sync_profile(tracked_profile.id, "job-invalid-avatar")

    diagnostics = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("instagram_profile_avatar_invalid_response")
    ]
    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert f"profile_id={tracked_profile.id!r}" in diagnostic
    assert "status=200" in diagnostic
    assert "raw_content_type='text/html; charset=utf-8'" in diagnostic
    assert "normalized_content_type='text/html'" in diagnostic
    assert f"content_length='{len(body)}'" in diagnostic
    assert "content_encoding='[redacted]'" in diagnostic
    assert "transfer_encoding='chunked'" in diagnostic
    assert "final_host='scontent-lax3-1.cdninstagram.com'" in diagnostic
    assert "path_suffix='.jpg'" in diagnostic
    assert "redirect_count=2" in diagnostic
    assert (
        "url_sha256='afefb043b707665ae7412411eb39ef6d5e71fb9214ee12c8b813e90ce5333302'"
    ) in diagnostic
    assert (
        "prefix_hex="
        "'3c21646f63747970652068746d6c3e3c68746d6c3e74656d706f726172792075"
        "7073747265616d20706167653c2f68746d6c3e58585858585858585858585858'"
    ) in diagnostic
    assert "prefix_kind='html'" in diagnostic
    assert avatar_url not in diagnostic
    assert "signed-secret" not in diagnostic
    assert "session-secret" not in diagnostic
    assert "body-secret-beyond-prefix" not in diagnostic
    assert response.closed is True


def test_invalid_profile_avatar_diagnostic_read_failure_preserves_error(
    caplog: pytest.LogCaptureFixture,
    profile_repository: LibraryRepository,
    tracked_profile,
    test_settings: Settings,
) -> None:
    # Break caught: best-effort diagnostics must never replace the stable
    # adapter error or prevent response cleanup when the body cannot be read.
    response = AvatarResponse(
        content_type="text/html",
        raw=UnreadableAvatarBody(),
    )
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

    with (
        caplog.at_level(
            logging.WARNING,
            logger="instaloader_webui.instagram.public_adapter",
        ),
        pytest.raises(PublicInstagramAdapterError) as raised,
    ):
        adapter.sync_profile(tracked_profile.id, "job-unreadable-avatar")

    assert str(raised.value) == "Instagram returned an invalid profile avatar image."
    diagnostics = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("instagram_profile_avatar_invalid_response")
    ]
    assert len(diagnostics) == 1
    assert "prefix_hex='[unavailable]'" in diagnostics[0]
    assert "prefix_kind='unreadable'" in diagnostics[0]
    assert "body-read-secret" not in diagnostics[0]
    assert response.closed is True


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
