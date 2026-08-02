import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from instaloader import Profile, TooManyRequestsException
from requests import Response
from requests.exceptions import ConnectionError, HTTPError

from instaloader_webui.db.followee_import_repositories import (
    FolloweeImportRepository,
)
from instaloader_webui.db.library_repositories import (
    JobRepository,
    JobSnapshot,
    LibraryRepository,
    MediaIdentity,
    NormalizedAsset,
    NormalizedMedia,
)
from instaloader_webui.db.schema import initialize_database
from instaloader_webui.instagram.errors import SESSION_REJECTED, TRANSIENT
from instaloader_webui.instagram.media_types import MediaCandidate
from instaloader_webui.instagram.profile_lookup import (
    ProfileLookupMode,
    ProfileLookupResolver,
)
from instaloader_webui.instagram.profile_sync import (
    ProfileSyncCoordinator,
    ProfileSyncResult,
)
from instaloader_webui.instagram.public_adapter import PublicInstaloaderAdapter
from instaloader_webui.instagram.safe_issues import (
    MediaItemFailure,
    SafeMediaIssue,
)
from instaloader_webui.instagram.worker_runtime import WorkerInstaloaderRuntime
from instaloader_webui.services import job_runner as job_runner_module
from instaloader_webui.services.instagram_inputs import (
    PostInput,
    ReelInput,
    StoryInput,
)
from instaloader_webui.services.job_runner import JobRunner

NOW = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)


@pytest.fixture
def library(session_factory, test_settings) -> LibraryRepository:
    initialize_database(test_settings)
    return LibraryRepository(session_factory)


@pytest.fixture
def jobs(session_factory) -> JobRepository:
    return JobRepository(session_factory)


@pytest.fixture
def followee_imports(session_factory) -> FolloweeImportRepository:
    return FolloweeImportRepository(session_factory)


@pytest.fixture
def profile_lookup_resolver() -> ProfileLookupResolver:
    return cast(ProfileLookupResolver, object())


@pytest.fixture
def runner(
    test_settings,
    library: LibraryRepository,
    jobs: JobRepository,
    followee_imports: FolloweeImportRepository,
    session_factory,
    profile_lookup_resolver: ProfileLookupResolver,
) -> JobRunner:
    return JobRunner(
        data_root=test_settings.data_root,
        media_root=test_settings.media_root,
        jobs_root=test_settings.jobs_root,
        library=library,
        jobs=jobs,
        followee_imports=followee_imports,
        loader_runtime=cast(WorkerInstaloaderRuntime, object()),
        profile_lookup_resolver=profile_lookup_resolver,
    )


def _claimed_profile_sync(
    library: LibraryRepository,
    jobs: JobRepository,
) -> tuple[JobSnapshot, str]:
    profile = library.upsert_profile_stub(
        username="katerina.soria",
        tracked=True,
        now=NOW,
    )
    queued = jobs.enqueue(
        job_type="profile_sync",
        payload={"profile_id": profile.id},
        status_text="Queued profile synchronization.",
        now=NOW,
    )
    claimed = jobs.claim_next(NOW + timedelta(seconds=1))
    assert claimed is not None
    assert claimed.id == queued.id
    return claimed, profile.id


def _claimed_single_media(
    jobs: JobRepository,
    payload: dict[str, object],
) -> JobSnapshot:
    queued = jobs.enqueue(
        job_type="single_media",
        payload=payload,
        status_text="Queued media download.",
        now=NOW,
    )
    claimed = jobs.claim_next(NOW + timedelta(seconds=1))
    assert claimed is not None
    assert claimed.id == queued.id
    return claimed


def _claimed_followee_discovery(
    followee_imports: FolloweeImportRepository,
    jobs: JobRepository,
) -> tuple[JobSnapshot, str]:
    batch = followee_imports.create_or_get_active(
        source_username="source.account",
        session_imported_at=NOW,
        now=NOW,
    )
    claimed = jobs.claim_next(NOW + timedelta(seconds=1))
    assert claimed is not None
    assert claimed.id == batch.job_id
    return claimed, batch.id


def _raw_http_error(status_code: int) -> HTTPError:
    response = Response()
    response.status_code = status_code
    return HTTPError(
        "raw-http-secret Cookie: sessionid=transport-secret",
        response=response,
    )


@pytest.mark.parametrize(
    ("mode", "native_error", "legacy_error", "expected_error"),
    [
        ("native", _raw_http_error(503), None, TRANSIENT),
        (
            "native",
            ConnectionError("native-transport-secret"),
            None,
            TRANSIENT,
        ),
        (
            "legacy",
            None,
            ConnectionError("legacy-transport-secret"),
            TRANSIENT,
        ),
        (
            "fallback",
            TooManyRequestsException("native-rate-limit-secret"),
            _raw_http_error(401),
            SESSION_REJECTED,
        ),
    ],
)
def test_profile_lookup_transport_failures_persist_only_safe_job_errors(
    runner: JobRunner,
    library: LibraryRepository,
    jobs: JobRepository,
    test_settings,
    monkeypatch: pytest.MonkeyPatch,
    mode: ProfileLookupMode,
    native_error: BaseException | None,
    legacy_error: BaseException | None,
    expected_error: str,
) -> None:
    # Break caught: a raw Requests exception escaping the adapter reaches the
    # generic job branch, which persists str(error) and its transport secrets.
    job, _profile_id = _claimed_profile_sync(library, jobs)
    native_calls: list[str] = []
    legacy_calls: list[tuple[str, object]] = []

    def native_lookup(_context: object, username: str) -> Profile:
        native_calls.append(username)
        if native_error is None:
            raise AssertionError("native lookup was not expected")
        raise native_error

    def legacy_lookup(doc_id: str, variables: object) -> object:
        legacy_calls.append((doc_id, variables))
        if legacy_error is None:
            raise AssertionError("legacy lookup was not expected")
        raise legacy_error

    class LookupFailureLoader:
        def __init__(self) -> None:
            self.dirname_pattern = ""
            self.context = SimpleNamespace(
                is_logged_in=True,
                doc_id_graphql_query=legacy_lookup,
            )

    class LookupFailureRuntime:
        def __init__(self, loader: LookupFailureLoader) -> None:
            self.loader = loader

        def acquire_required_session(
            self,
            staging_directory: Path,
        ) -> LookupFailureLoader:
            self.loader.dirname_pattern = str(staging_directory)
            return self.loader

    monkeypatch.setattr(Profile, "from_username", staticmethod(native_lookup))
    loader = LookupFailureLoader()
    adapter = PublicInstaloaderAdapter(
        data_root=test_settings.data_root,
        media_root=test_settings.media_root,
        jobs_root=test_settings.jobs_root,
        library=library,
        progress=lambda *_args: None,
        loader_runtime=cast(
            WorkerInstaloaderRuntime,
            cast(object, LookupFailureRuntime(loader)),
        ),
        profile_lookup_resolver=ProfileLookupResolver(
            mode,
            logging.getLogger(__name__),
        ),
    )
    monkeypatch.setattr(runner, "_adapter", lambda _job: adapter)

    runner.run(job)

    failed = jobs.get(job.id)
    assert failed is not None
    assert failed.state == "failed"
    persisted_error = failed.error
    assert persisted_error == expected_error
    for forbidden in (
        "raw-http-secret",
        "transport-secret",
        "native-rate-limit-secret",
    ):
        assert forbidden not in persisted_error
    assert len(native_calls) == (0 if mode == "legacy" else 1)
    assert len(legacy_calls) == (0 if mode == "native" else 1)


def test_job_runner_passes_same_resolver_to_every_created_adapter(
    runner: JobRunner,
    jobs: JobRepository,
    profile_lookup_resolver: ProfileLookupResolver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Break caught: constructing resolvers per job or omitting the shared
    # composition dependency makes adapter behavior diverge across jobs.
    injected: list[ProfileLookupResolver] = []

    class RecordingAdapter:
        def download_input(self, _parsed: object, _job_id: str) -> None:
            return None

    def fake_adapter(**kwargs):
        injected.append(kwargs["profile_lookup_resolver"])
        return RecordingAdapter()

    monkeypatch.setattr(
        job_runner_module,
        "PublicInstaloaderAdapter",
        fake_adapter,
    )
    payload: dict[str, object] = {
        "kind": "post",
        "shortcode": "CmzV2H-rrlI",
        "canonical_url": "https://www.instagram.com/p/CmzV2H-rrlI/",
    }

    runner.run(_claimed_single_media(jobs, payload))
    runner.run(_claimed_single_media(jobs, payload))

    assert len(injected) == 2
    assert all(resolver is profile_lookup_resolver for resolver in injected)


def test_job_runner_passes_shared_resolver_to_followee_discovery(
    runner: JobRunner,
    jobs: JobRepository,
    followee_imports: FolloweeImportRepository,
    profile_lookup_resolver: ProfileLookupResolver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Break caught: omitting the shared resolver makes followee imports bypass
    # the application-owned Profile lookup gateway.
    class GatewayRequiredAdapter:
        def __init__(
            self,
            *,
            profile_lookup_resolver: ProfileLookupResolver,
            **_kwargs: object,
        ) -> None:
            if profile_lookup_resolver is not profile_lookup_resolver_fixture:
                raise AssertionError("followee discovery received another resolver")

        def discover(self, **_kwargs: object) -> tuple[object, ...]:
            return ()

    profile_lookup_resolver_fixture = profile_lookup_resolver
    monkeypatch.setattr(
        job_runner_module,
        "FolloweeDiscoveryAdapter",
        GatewayRequiredAdapter,
    )
    job, batch_id = _claimed_followee_discovery(followee_imports, jobs)

    runner.run(job)

    completed_job = jobs.get(job.id)
    completed_batch = followee_imports.get(batch_id)
    assert completed_job is not None
    assert completed_job.state == "succeeded"
    assert completed_batch is not None
    assert completed_batch.state == "ready"


def test_profile_sync_with_item_issues_completes_with_warnings(
    runner: JobRunner,
    library: LibraryRepository,
    jobs: JobRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Break caught: discarding ProfileSyncResult would mark a warning-bearing
    # profile synchronization as an ordinary success.
    job, _profile_id = _claimed_profile_sync(library, jobs)

    class WarningAdapter:
        def sync_profile(self, _profile_id: str, _job_id: str) -> ProfileSyncResult:
            return ProfileSyncResult(
                processed=3,
                total=3,
                issue_count=1,
                stopped=False,
            )

    monkeypatch.setattr(runner, "_adapter", lambda _job: WarningAdapter())

    runner.run(job)

    completed = jobs.get(job.id, include_issues=True)
    assert completed is not None
    assert completed.state == "completed_with_warnings"
    assert completed.status_text == "Completed with 1 warning(s)."


def test_profile_sync_without_item_issues_succeeds(
    runner: JobRunner,
    library: LibraryRepository,
    jobs: JobRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Break caught: routing every completed profile result to the warning
    # terminal state would mislabel a clean synchronization.
    job, _profile_id = _claimed_profile_sync(library, jobs)

    class CleanAdapter:
        def sync_profile(self, _profile_id: str, _job_id: str) -> ProfileSyncResult:
            return ProfileSyncResult(
                processed=3,
                total=3,
                issue_count=0,
                stopped=False,
            )

    monkeypatch.setattr(runner, "_adapter", lambda _job: CleanAdapter())

    runner.run(job)

    completed = jobs.get(job.id)
    assert completed is not None
    assert completed.state == "succeeded"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {
                "media_kind": "post",
                "identity_type": "shortcode",
                "identity_value": "post-shortcode",
                "shortcode": "post-shortcode",
                "original_url": "https://input.example/not-a-kind-hint",
            },
            PostInput(
                shortcode="post-shortcode",
                canonical_url="https://input.example/not-a-kind-hint",
            ),
        ),
        (
            {
                "media_kind": "reel",
                "identity_type": "shortcode",
                "identity_value": "reel-shortcode",
                "shortcode": "reel-shortcode",
                "original_url": "https://input.example/not-a-kind-hint",
            },
            ReelInput(
                shortcode="reel-shortcode",
                canonical_url="https://input.example/not-a-kind-hint",
            ),
        ),
        (
            {
                "media_kind": "story",
                "identity_type": "story_media_id",
                "identity_value": "3952742051065980676",
                "story_media_id": "3952742051065980676",
                "username": "katerina.soria",
                "original_url": "https://input.example/not-a-kind-hint",
            },
            StoryInput(
                username="katerina.soria",
                story_media_id="3952742051065980676",
                canonical_url="https://input.example/not-a-kind-hint",
            ),
        ),
    ],
)
def test_single_media_payload_dispatches_as_typed_input_without_url_inference(
    runner: JobRunner,
    jobs: JobRepository,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
    expected: PostInput | ReelInput | StoryInput,
) -> None:
    # Break caught: reconstructing the media kind from original_url loses
    # Story identity and makes the canonical typed payload non-authoritative.
    job = _claimed_single_media(jobs, payload)
    received: list[PostInput | ReelInput | StoryInput] = []

    class TypedAdapter:
        def download_input(
            self,
            parsed: PostInput | ReelInput | StoryInput,
            _job_id: str,
        ) -> None:
            received.append(parsed)

    monkeypatch.setattr(runner, "_adapter", lambda _job: TypedAdapter())

    runner.run(job)

    completed = jobs.get(job.id)
    assert completed is not None
    assert completed.state == "succeeded"
    assert received == [expected]


@pytest.mark.parametrize(
    "payload",
    [
        {
            "media_kind": "post",
            "identity_type": "story_media_id",
            "identity_value": "post-shortcode",
            "shortcode": "post-shortcode",
            "original_url": "https://www.instagram.com/p/post-shortcode/",
        },
        {
            "media_kind": "story",
            "identity_type": "story_media_id",
            "identity_value": "different-story-id",
            "story_media_id": "3952742051065980676",
            "username": "katerina.soria",
            "original_url": (
                "https://www.instagram.com/stories/"
                "katerina.soria/3952742051065980676/"
            ),
        },
    ],
)
def test_inconsistent_media_identity_fails_before_adapter_dispatch(
    runner: JobRunner,
    jobs: JobRepository,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> None:
    # Break caught: trusting only media_kind can dispatch one identity under
    # another identity type or value and corrupt deduplication.
    job = _claimed_single_media(jobs, payload)

    class ForbiddenAdapter:
        def download_input(self, _parsed: object, _job_id: str) -> None:
            raise AssertionError("adapter must not receive inconsistent payloads")

    monkeypatch.setattr(runner, "_adapter", lambda _job: ForbiddenAdapter())

    runner.run(job)

    failed = jobs.get(job.id)
    assert failed is not None
    assert failed.state == "failed"
    assert failed.error == "Worker job has inconsistent media identity."


def test_adapter_callbacks_persist_phase_nullable_total_issue_and_one_log(
    runner: JobRunner,
    library: LibraryRepository,
    jobs: JobRepository,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Break caught: a three-argument progress adapter or missing issue callback
    # drops Task 7's phase and warning boundary at the worker layer.
    job, _profile_id = _claimed_profile_sync(library, jobs)
    issue = SafeMediaIssue(
        identity=MediaIdentity("story_media_id", "3952742051065980676"),
        kind="story",
        error_code="instagram_unavailable",
        safe_message="Instagram could not be reached. Try again later.",
        exception_class_chain=("BadResponseException",),
    )

    class CallbackAdapter:
        def __init__(self, *, progress, issue_callback) -> None:
            self._progress = progress
            self._issue = issue_callback

        def sync_profile(self, _profile_id: str, _job_id: str) -> ProfileSyncResult:
            self._progress(
                2,
                None,
                "saving_stories",
                "Saving current Instagram stories before they expire…",
            )
            self._issue(issue)
            return ProfileSyncResult(
                processed=2,
                total=None,
                issue_count=1,
                stopped=False,
            )

    def fake_adapter(**kwargs):
        return CallbackAdapter(
            progress=kwargs["progress"],
            issue_callback=kwargs["issue"],
        )

    monkeypatch.setattr(
        job_runner_module,
        "PublicInstaloaderAdapter",
        fake_adapter,
    )
    caplog.set_level(logging.WARNING, logger=job_runner_module.__name__)

    runner.run(job)

    completed = jobs.get(job.id, include_issues=True)
    assert completed is not None
    assert completed.state == "completed_with_warnings"
    assert completed.progress_current == 2
    assert completed.progress_total is None
    assert completed.phase == "saving_stories"
    assert (
        completed.status_text == "Completed with 1 warning(s)."
    )
    assert completed.issue_count == 1
    assert completed.issues[0].identity_type == "story_media_id"
    assert completed.issues[0].identity_value == "3952742051065980676"
    assert completed.issues[0].media_kind == "story"
    issue_logs = [
        record
        for record in caplog.records
        if record.message.startswith("instagram_media_issue ")
    ]
    assert len(issue_logs) == 1


def test_direct_media_item_failure_records_issue_then_fails_with_safe_message(
    runner: JobRunner,
    jobs: JobRepository,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Break caught: routing item-local direct failures through the generic
    # exception branch loses the structured issue even if its message is safe.
    job = _claimed_single_media(
        jobs,
        {
            "media_kind": "reel",
            "identity_type": "shortcode",
            "identity_value": "DOqEJyxCRGJ",
            "shortcode": "DOqEJyxCRGJ",
            "original_url": "https://www.instagram.com/reel/DOqEJyxCRGJ/",
        },
    )
    issue = SafeMediaIssue(
        identity=MediaIdentity("shortcode", "DOqEJyxCRGJ"),
        kind="reel",
        error_code="instagram_unavailable",
        safe_message="Instagram could not be reached. Try again later.",
        exception_class_chain=("ConnectionException",),
    )

    class FailingAdapter:
        def download_input(self, _parsed: ReelInput, _job_id: str) -> None:
            raise MediaItemFailure(issue)

    monkeypatch.setattr(runner, "_adapter", lambda _job: FailingAdapter())
    caplog.set_level(logging.WARNING, logger=job_runner_module.__name__)

    runner.run(job)

    failed = jobs.get(job.id, include_issues=True)
    assert failed is not None
    assert failed.state == "failed"
    assert failed.error == issue.safe_message
    assert failed.issue_count == 1
    assert failed.issues[0].error_code == "instagram_unavailable"
    issue_logs = [
        record
        for record in caplog.records
        if record.message.startswith("instagram_media_issue ")
    ]
    assert len(issue_logs) == 1


def test_direct_issue_repository_failure_ends_job_with_bounded_fatal_error(
    runner: JobRunner,
    jobs: JobRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Break caught: an exception raised while persisting the direct issue must
    # not escape run() and strand the already-claimed job in running.
    job = _claimed_single_media(
        jobs,
        {
            "media_kind": "reel",
            "identity_type": "shortcode",
            "identity_value": "repository-failure",
            "shortcode": "repository-failure",
            "original_url": (
                "https://www.instagram.com/reel/repository-failure/"
            ),
        },
    )
    issue = SafeMediaIssue(
        identity=MediaIdentity("shortcode", "repository-failure"),
        kind="reel",
        error_code="instagram_unavailable",
        safe_message="Instagram could not be reached. Try again later.",
        exception_class_chain=("ConnectionException",),
    )

    class FailingAdapter:
        def download_input(self, _parsed: ReelInput, _job_id: str) -> None:
            raise MediaItemFailure(issue)

    def reject_issue_write(**_kwargs) -> None:
        raise RuntimeError("postgresql://admin:raw-secret@private.example/jobs")

    monkeypatch.setattr(runner, "_adapter", lambda _job: FailingAdapter())
    monkeypatch.setattr(jobs, "record_issue", reject_issue_write)

    runner.run(job)

    failed = jobs.get(job.id, include_issues=True)
    assert failed is not None
    assert failed.state == "failed"
    assert failed.error == "Media issue reporting failed."
    assert failed.issue_count == 0


def test_direct_issue_logging_failure_ends_job_with_bounded_fatal_error(
    runner: JobRunner,
    jobs: JobRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Break caught: a logging handler failure after issue persistence must not
    # escape run() or expose its raw internal message in the terminal error.
    job = _claimed_single_media(
        jobs,
        {
            "media_kind": "story",
            "identity_type": "story_media_id",
            "identity_value": "3952742051065980676",
            "story_media_id": "3952742051065980676",
            "username": "katerina.soria",
            "original_url": (
                "https://www.instagram.com/stories/"
                "katerina.soria/3952742051065980676/"
            ),
        },
    )
    issue = SafeMediaIssue(
        identity=MediaIdentity("story_media_id", "3952742051065980676"),
        kind="story",
        error_code="instagram_unavailable",
        safe_message="Instagram could not be reached. Try again later.",
        exception_class_chain=("BadResponseException",),
    )

    class FailingAdapter:
        def download_input(self, _parsed: StoryInput, _job_id: str) -> None:
            raise MediaItemFailure(issue)

    def reject_issue_log(*_args, **_kwargs) -> None:
        raise RuntimeError("raw log handler secret at /private/log.sock")

    monkeypatch.setattr(runner, "_adapter", lambda _job: FailingAdapter())
    monkeypatch.setattr(
        job_runner_module,
        "log_media_issue",
        reject_issue_log,
    )

    runner.run(job)

    failed = jobs.get(job.id, include_issues=True)
    assert failed is not None
    assert failed.state == "failed"
    assert failed.error == "Media issue reporting failed."
    assert failed.issue_count == 1


def test_fatal_profile_scan_preserves_story_and_records_failed_attempt(
    runner: JobRunner,
    library: LibraryRepository,
    jobs: JobRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Break caught: wrapping the full profile sync in one transaction or
    # treating iterator failures as warnings would lose the Story-first commit
    # or incorrectly record a successful sync.
    job, profile_id = _claimed_profile_sync(library, jobs)
    story_identity = MediaIdentity(
        "story_media_id",
        "3952742051065980676",
    )
    story = MediaCandidate(
        identity=story_identity,
        kind="story",
        session_configured=True,
        resolve=lambda: raise_unexpected_resolution(),
    )

    class StoryThenFatalSource:
        def iter_stories(self, _profile: object):
            return (story,)

        def iter_reels(self, _profile: object):
            raise RuntimeError("fatal profile iterator")

        def iter_posts(self, _profile: object):
            raise AssertionError("posts must not scan after a fatal Reel iterator")

    class PersistingProcessor:
        def process(self, candidate: MediaCandidate, *, job_id: str) -> object:
            assert candidate is story
            return library.upsert_media(
                normalized=NormalizedMedia(
                    identity=story_identity,
                    instagram_media_id=story_identity.value,
                    shortcode=None,
                    kind="story",
                    caption="Current Story",
                    accessibility_caption="",
                    published_at=NOW,
                    story_expires_at=NOW + timedelta(hours=24),
                    original_url=(
                        "https://www.instagram.com/stories/"
                        "katerina.soria/3952742051065980676/"
                    ),
                ),
                profile_id=profile_id,
                assets=(
                    NormalizedAsset(
                        relative_path=(
                            "profiles/8642097531/3952742051065980676/"
                            "3952742051065980676.jpg"
                        ),
                        mime_type="image/jpeg",
                        kind="image",
                        role="content",
                        position=0,
                        file_size=4,
                    ),
                ),
                now=NOW + timedelta(seconds=2),
            )

    class CoordinatorAdapter:
        def __init__(self, *, progress, issue_callback) -> None:
            self._progress = progress
            self._issue = issue_callback

        def sync_profile(self, _profile_id: str, job_id: str) -> ProfileSyncResult:
            return ProfileSyncCoordinator(
                source=StoryThenFatalSource(),
                processor=PersistingProcessor(),
                progress=self._progress,
                record_issue=self._issue,
                is_syncable=lambda: True,
            ).run(profile=object(), job_id=job_id)

    def fake_adapter(**kwargs):
        return CoordinatorAdapter(
            progress=kwargs["progress"],
            issue_callback=kwargs["issue"],
        )

    monkeypatch.setattr(
        job_runner_module,
        "PublicInstaloaderAdapter",
        fake_adapter,
    )

    runner.run(job)

    failed = jobs.get(job.id)
    assert failed is not None
    assert failed.state == "failed"
    assert failed.error == "fatal profile iterator"
    assert failed.progress_current == 1
    assert failed.progress_total is None
    assert failed.phase == "scanning_media"
    stored_profile = library.get_profile(profile_id)
    assert stored_profile is not None
    assert stored_profile.last_sync_attempted_at is not None
    assert stored_profile.last_sync_succeeded_at is None
    saved_story = library.find_media_by_identity(story_identity)
    assert saved_story is not None
    assert saved_story.kind == "story"


def raise_unexpected_resolution():
    raise AssertionError("the coordinator must not resolve media candidates")
