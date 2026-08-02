from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from instaloader import BadResponseException, QueryReturnedNotFoundException

from instaloader_webui.config import Settings
from instaloader_webui.db.library_repositories import (
    LibraryRepository,
    MediaIdentity,
    NormalizedMedia,
    ProfileSnapshot,
)
from instaloader_webui.db.schema import initialize_database
from instaloader_webui.instagram.profile_lookup import ProfileLookupResolver
from instaloader_webui.instagram.public_adapter import PublicInstaloaderAdapter
from instaloader_webui.instagram.safe_issues import MediaItemFailure
from instaloader_webui.instagram.worker_runtime import (
    InstagramSessionRevisionError,
    WorkerInstaloaderRuntime,
)
from instaloader_webui.services.instagram_inputs import (
    PostInput,
    ReelInput,
    StoryInput,
)

NOW = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)
# Instaloader 4.15.3 returns naive UTC values for these two Story properties.
PUBLISHED_AT = datetime(2026, 7, 31, 6, 0)  # noqa: DTZ001
EXPIRES_AT = datetime(2036, 8, 1, 6, 0)  # noqa: DTZ001
STORY_MEDIA_ID = "3952742051065980676"
SHORTCODE = "DOqEJyxCRGJ"
USERNAME = "katerina.soria"
CANONICAL_STORY_URL = (
    f"https://www.instagram.com/stories/{USERNAME}/{STORY_MEDIA_ID}/"
)


@dataclass(slots=True)
class FakeProfile:
    userid: int = 8_642_097_531
    username: str = USERNAME
    full_name: str = "Katerina Soria"
    biography: str = ""
    profile_pic_url: str | None = None
    is_private: bool = False
    followed_by_viewer: bool = False


@dataclass(slots=True)
class FakeStoryItem:
    owner_profile: FakeProfile
    mediaid: int = int(STORY_MEDIA_ID)
    date_utc: datetime = PUBLISHED_AT
    expiring_utc: datetime = EXPIRES_AT
    is_video: bool = False
    caption: str | None = "Story caption"
    download_calls: int = 0
    shared_reel_download_calls: int = 0

    @property
    def shared_reel(self) -> object:
        raise AssertionError("Story reshare metadata must not be inspected.")


@dataclass(slots=True)
class FakePost:
    owner_profile: FakeProfile
    shortcode: str = SHORTCODE
    mediaid: int = 17_800_000_000_000_001
    caption: str | None = "Post caption"
    accessibility_caption: str | None = "Post accessibility caption"
    date_utc: datetime = PUBLISHED_AT
    typename: str = "GraphImage"
    is_video: bool = False


class FakeStory:
    def __init__(self, items: tuple[FakeStoryItem, ...]) -> None:
        self._items = items

    def get_items(self):
        yield from self._items


class FakeLoader:
    def __init__(
        self,
        *,
        logged_in: bool = True,
        story_files: tuple[tuple[str, bytes], ...] = (
            ("story.jpg", b"story-image"),
        ),
    ) -> None:
        self.context = SimpleNamespace(is_logged_in=logged_in)
        self.dirname_pattern = ""
        self.story_files = story_files
        self.stories: tuple[FakeStory, ...] = ()
        self.story_userids: list[list[int]] = []
        self.downloaded_posts: list[FakePost] = []

    def download_storyitem(
        self,
        item: FakeStoryItem,
        *,
        target: str,
    ) -> bool:
        assert target == item.owner_profile.username
        item.download_calls += 1
        directory = Path(self.dirname_pattern)
        for filename, content in self.story_files:
            (directory / filename).write_bytes(content)
        return True

    def download_post(self, post: FakePost, *, target: str) -> bool:
        assert target == post.owner_profile.username
        self.downloaded_posts.append(post)
        (Path(self.dirname_pattern) / f"{post.shortcode}.jpg").write_bytes(
            b"post-image"
        )
        return True

    def get_stories(self, *, userids: list[int]):
        self.story_userids.append(userids)
        yield from self.stories


class FakeRuntime:
    def __init__(self, loader: FakeLoader, *, configured: bool = True) -> None:
        self.loader = loader
        self.configured = configured

    def acquire(self, staging_directory: Path) -> tuple[FakeLoader, bool]:
        self.loader.dirname_pattern = str(staging_directory)
        return self.loader, self.configured

    def acquire_required_session(self, staging_directory: Path) -> FakeLoader:
        return cast(
            FakeLoader,
            WorkerInstaloaderRuntime.acquire_required_session(
                cast(Any, self),
                staging_directory,
            ),
        )


class RejectingProfileLookupResolver:
    def resolve(self, _context: object, _username: str) -> FakeProfile:
        raise AssertionError("direct media must not resolve a Profile username")


@pytest.fixture
def repository(
    session_factory,
    test_settings: Settings,
) -> LibraryRepository:
    initialize_database(test_settings)
    return LibraryRepository(session_factory)


@pytest.fixture
def stored_profile(repository: LibraryRepository) -> ProfileSnapshot:
    stub = repository.upsert_profile_stub(
        username=USERNAME,
        tracked=True,
        now=NOW,
    )
    return repository.update_profile_metadata(
        profile_id=stub.id,
        instagram_user_id=str(FakeProfile().userid),
        username=USERNAME,
        full_name="Katerina Soria",
        biography="",
        profile_pic_url=None,
        now=NOW,
    )


def make_adapter(
    *,
    test_settings: Settings,
    repository: LibraryRepository,
    loader: FakeLoader,
    configured: bool = True,
) -> PublicInstaloaderAdapter:
    return PublicInstaloaderAdapter(
        data_root=test_settings.data_root,
        media_root=test_settings.media_root,
        jobs_root=test_settings.jobs_root,
        library=repository,
        progress=lambda _current, _total, _status: None,
        loader_runtime=FakeRuntime(  # type: ignore[arg-type]
            loader,
            configured=configured,
        ),
        profile_lookup_resolver=cast(
            ProfileLookupResolver,
            RejectingProfileLookupResolver(),
        ),
    )


def patch_story_lookup(
    monkeypatch: pytest.MonkeyPatch,
    item_or_error: FakeStoryItem | BaseException,
) -> list[tuple[Any, int]]:
    calls: list[tuple[Any, int]] = []

    def from_mediaid(context: Any, mediaid: int) -> FakeStoryItem:
        calls.append((context, mediaid))
        if isinstance(item_or_error, BaseException):
            raise item_or_error
        return item_or_error

    monkeypatch.setattr(
        "instaloader_webui.instagram.public_adapter.StoryItem.from_mediaid",
        from_mediaid,
    )
    return calls


def patch_post_lookup(
    monkeypatch: pytest.MonkeyPatch,
    post_or_error: FakePost | BaseException,
) -> list[tuple[Any, str]]:
    calls: list[tuple[Any, str]] = []

    def from_shortcode(context: Any, shortcode: str) -> FakePost:
        calls.append((context, shortcode))
        if isinstance(post_or_error, BaseException):
            raise post_or_error
        return post_or_error

    monkeypatch.setattr(
        "instaloader_webui.instagram.public_adapter.Post.from_shortcode",
        from_shortcode,
    )
    return calls


def story_input(*, username: str = USERNAME) -> StoryInput:
    return StoryInput(
        username=username,
        story_media_id=STORY_MEDIA_ID,
        canonical_url=(
            "https://www.instagram.com/stories/"
            f"{username}/{STORY_MEDIA_ID}/"
        ),
    )


def persist_incomplete_media(
    *,
    repository: LibraryRepository,
    profile: ProfileSnapshot,
    kind: str,
) -> MediaIdentity:
    identity = MediaIdentity(
        "story_media_id" if kind == "story" else "shortcode",
        STORY_MEDIA_ID if kind == "story" else SHORTCODE,
    )
    repository.upsert_media(
        normalized=NormalizedMedia(
            identity=identity,
            instagram_media_id=identity.value,
            shortcode=None if kind == "story" else SHORTCODE,
            kind=kind,
            caption="Incomplete local media",
            accessibility_caption="",
            published_at=NOW,
            story_expires_at=EXPIRES_AT.replace(tzinfo=UTC)
            if kind == "story"
            else None,
            original_url=(
                CANONICAL_STORY_URL
                if kind == "story"
                else f"https://www.instagram.com/{'reel' if kind == 'reel' else 'p'}/{SHORTCODE}/"
            ),
        ),
        profile_id=profile.id,
        assets=(),
        now=NOW,
    )
    return identity


def test_direct_story_validates_owner_and_downloads_only_story(
    monkeypatch: pytest.MonkeyPatch,
    repository: LibraryRepository,
    test_settings: Settings,
) -> None:
    # Break caught: resolving a Story URL through its shared Reel downloads
    # extra media and persists the wrong identity.
    item = FakeStoryItem(owner_profile=FakeProfile())
    loader = FakeLoader()
    lookup_calls = patch_story_lookup(monkeypatch, item)
    adapter = make_adapter(
        test_settings=test_settings,
        repository=repository,
        loader=loader,
    )

    saved = adapter.download_input(story_input(), "job-story")

    assert saved.kind == "story"
    assert saved.story_media_id == STORY_MEDIA_ID
    assert lookup_calls == [(loader.context, int(STORY_MEDIA_ID))]
    assert item.download_calls == 1
    assert item.shared_reel_download_calls == 0


def test_direct_story_owner_mismatch_is_a_safe_item_failure(
    monkeypatch: pytest.MonkeyPatch,
    repository: LibraryRepository,
    test_settings: Settings,
) -> None:
    # Break caught: omitting URL-owner validation lets one user's Story be
    # stored under another user's canonical Story URL.
    item = FakeStoryItem(owner_profile=FakeProfile(username="another.owner"))
    patch_story_lookup(monkeypatch, item)
    adapter = make_adapter(
        test_settings=test_settings,
        repository=repository,
        loader=FakeLoader(),
    )

    with pytest.raises(MediaItemFailure) as caught:
        adapter.download_input(story_input(), "job-owner-mismatch")

    assert caught.value.issue.error_code == "instagram_not_found"
    assert caught.value.issue.identity == MediaIdentity(
        "story_media_id",
        STORY_MEDIA_ID,
    )
    assert USERNAME not in str(caught.value)
    assert "another.owner" not in str(caught.value)
    assert item.download_calls == 0
    assert repository.find_media_by_identity(caught.value.issue.identity) is None


@pytest.mark.parametrize(
    ("configured", "logged_in", "expected_code"),
    (
        (False, False, "instagram_access_denied"),
        (True, False, "instagram_session_rejected"),
    ),
)
def test_direct_story_requires_a_logged_in_session_with_safe_failure(
    monkeypatch: pytest.MonkeyPatch,
    repository: LibraryRepository,
    test_settings: Settings,
    configured: bool,
    logged_in: bool,
    expected_code: str,
) -> None:
    # Break caught: Story resolution attempted anonymously either leaks the
    # imported account or collapses missing and rejected sessions together.
    item = FakeStoryItem(owner_profile=FakeProfile())
    lookup_calls = patch_story_lookup(monkeypatch, item)
    adapter = make_adapter(
        test_settings=test_settings,
        repository=repository,
        loader=FakeLoader(logged_in=logged_in),
        configured=configured,
    )

    with pytest.raises(MediaItemFailure) as caught:
        adapter.download_input(story_input(), "job-session")

    assert caught.value.issue.error_code == expected_code
    assert "katerina" not in str(caught.value).casefold()
    assert "cookie=value" not in str(caught.value).casefold()
    assert lookup_calls == []
    assert item.download_calls == 0


def test_required_session_runtime_rejects_unconfigured_or_logged_out_loader(
    tmp_path: Path,
) -> None:
    # Break caught: trusting only the encrypted-file presence permits Story
    # operations with an anonymous Instaloader context.
    runtime = FakeRuntime(FakeLoader(logged_in=False), configured=True)

    with pytest.raises(InstagramSessionRevisionError):
        WorkerInstaloaderRuntime.acquire_required_session(
            cast(Any, runtime),
            tmp_path,
        )


def test_direct_story_not_found_is_a_safe_retryable_item_failure(
    monkeypatch: pytest.MonkeyPatch,
    repository: LibraryRepository,
    test_settings: Settings,
) -> None:
    # Break caught: retaining the raw upstream exception can persist a
    # query-bearing URL or Cookie value in Activity.
    patch_story_lookup(
        monkeypatch,
        QueryReturnedNotFoundException(
            "not found: https://instagram.example/item?cookie=value"
        ),
    )
    adapter = make_adapter(
        test_settings=test_settings,
        repository=repository,
        loader=FakeLoader(),
    )

    with pytest.raises(MediaItemFailure) as caught:
        adapter.download_input(story_input(), "job-not-found")

    assert caught.value.issue.error_code == "instagram_not_found"
    assert str(caught.value) == (
        "This Instagram media item was not found or is inaccessible."
    )
    assert "cookie=value" not in str(caught.value)


def test_storyitem_missing_metadata_is_classified_as_not_found(
    monkeypatch: pytest.MonkeyPatch,
    repository: LibraryRepository,
    test_settings: Settings,
) -> None:
    # Break caught: Instaloader's pinned missing-Story exception has no generic
    # not-found marker, so the shared classifier otherwise reports unavailable.
    patch_story_lookup(
        monkeypatch,
        BadResponseException("Fetching StoryItem metadata failed."),
    )
    adapter = make_adapter(
        test_settings=test_settings,
        repository=repository,
        loader=FakeLoader(),
    )

    with pytest.raises(MediaItemFailure) as caught:
        adapter.download_input(story_input(), "job-missing-metadata")

    assert caught.value.issue.error_code == "instagram_not_found"
    assert caught.value.issue.exception_class_chain == ("BadResponseException",)


def test_other_story_bad_responses_remain_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    repository: LibraryRepository,
    test_settings: Settings,
) -> None:
    # Break caught: treating every malformed Instagram response as deletion
    # hides transient service failures that should remain retryable.
    patch_story_lookup(
        monkeypatch,
        BadResponseException("JSON decode failed."),
    )
    adapter = make_adapter(
        test_settings=test_settings,
        repository=repository,
        loader=FakeLoader(),
    )

    with pytest.raises(MediaItemFailure) as caught:
        adapter.download_input(story_input(), "job-bad-response")

    assert caught.value.issue.error_code == "instagram_unavailable"


def test_direct_expired_story_is_not_downloaded(
    monkeypatch: pytest.MonkeyPatch,
    repository: LibraryRepository,
    test_settings: Settings,
) -> None:
    # Break caught: downloading an already-expired manifest item can persist
    # partial or unrelated output after Instagram invalidates its URLs.
    item = FakeStoryItem(
        owner_profile=FakeProfile(),
        expiring_utc=datetime(2020, 1, 1, tzinfo=UTC),
    )
    patch_story_lookup(monkeypatch, item)
    adapter = make_adapter(
        test_settings=test_settings,
        repository=repository,
        loader=FakeLoader(),
    )

    with pytest.raises(MediaItemFailure) as caught:
        adapter.download_input(story_input(), "job-expired")

    assert caught.value.issue.error_code == "instagram_not_found"
    assert item.download_calls == 0
    assert repository.find_media_by_identity(caught.value.issue.identity) is None


def test_direct_image_story_persists_canonical_url_and_timestamps(
    monkeypatch: pytest.MonkeyPatch,
    repository: LibraryRepository,
    test_settings: Settings,
) -> None:
    # Break caught: treating Story media IDs like shortcodes loses the canonical
    # owner URL and Story expiry metadata.
    item = FakeStoryItem(owner_profile=FakeProfile())
    patch_story_lookup(monkeypatch, item)
    adapter = make_adapter(
        test_settings=test_settings,
        repository=repository,
        loader=FakeLoader(),
    )

    saved = adapter.download_input(story_input(), "job-image")

    assert saved.original_url == CANONICAL_STORY_URL
    assert saved.published_at == PUBLISHED_AT.replace(tzinfo=UTC)
    assert saved.story_expires_at == EXPIRES_AT.replace(tzinfo=UTC)
    assert [(asset.kind, asset.role, asset.position) for asset in saved.assets] == [
        ("image", "content", 0)
    ]


def test_direct_video_story_persists_video_content_and_image_poster(
    monkeypatch: pytest.MonkeyPatch,
    repository: LibraryRepository,
    test_settings: Settings,
) -> None:
    # Break caught: deriving roles from suffix alone counts a Story thumbnail
    # as carousel content instead of a poster.
    item = FakeStoryItem(owner_profile=FakeProfile(), is_video=True)
    patch_story_lookup(monkeypatch, item)
    adapter = make_adapter(
        test_settings=test_settings,
        repository=repository,
        loader=FakeLoader(
            story_files=(
                ("story.jpg", b"story-poster"),
                ("story.mp4", b"story-video"),
            )
        ),
    )

    saved = adapter.download_input(story_input(), "job-video")

    assert [(asset.kind, asset.role, asset.position) for asset in saved.assets] == [
        ("video", "content", 0),
        ("image", "poster", 0),
    ]


@pytest.mark.parametrize(
    ("parsed", "expected_kind", "expected_url"),
    (
        (
            PostInput(
                shortcode=SHORTCODE,
                canonical_url=f"https://www.instagram.com/p/{SHORTCODE}/",
            ),
            "post",
            f"https://www.instagram.com/p/{SHORTCODE}/",
        ),
        (
            ReelInput(
                shortcode=SHORTCODE,
                canonical_url=f"https://www.instagram.com/reel/{SHORTCODE}/",
            ),
            "reel",
            f"https://www.instagram.com/reel/{SHORTCODE}/",
        ),
    ),
)
def test_typed_post_and_reel_inputs_use_shortcode_resolution(
    monkeypatch: pytest.MonkeyPatch,
    repository: LibraryRepository,
    test_settings: Settings,
    parsed: PostInput | ReelInput,
    expected_kind: str,
    expected_url: str,
) -> None:
    # Break caught: keeping URL-shape inference in the worker can classify a
    # typed Reel as a Post or persist a noncanonical URL.
    post = FakePost(owner_profile=FakeProfile())
    loader = FakeLoader(logged_in=False)
    calls: list[tuple[Any, str]] = []

    def from_shortcode(context: Any, shortcode: str) -> FakePost:
        calls.append((context, shortcode))
        return post

    monkeypatch.setattr(
        "instaloader_webui.instagram.public_adapter.Post.from_shortcode",
        from_shortcode,
    )
    adapter = make_adapter(
        test_settings=test_settings,
        repository=repository,
        loader=loader,
        configured=False,
    )

    saved = adapter.download_input(parsed, f"job-{expected_kind}")

    assert saved.kind == expected_kind
    assert saved.original_url == expected_url
    assert calls == [(loader.context, SHORTCODE)]
    assert loader.downloaded_posts == [post]


@pytest.mark.parametrize(
    "parsed",
    (
        PostInput(
            shortcode=SHORTCODE,
            canonical_url=f"https://www.instagram.com/p/{SHORTCODE}/",
        ),
        ReelInput(
            shortcode=SHORTCODE,
            canonical_url=f"https://www.instagram.com/reel/{SHORTCODE}/",
        ),
    ),
)
def test_complete_local_post_or_reel_skips_shortcode_lookup(
    monkeypatch: pytest.MonkeyPatch,
    repository: LibraryRepository,
    test_settings: Settings,
    parsed: PostInput | ReelInput,
) -> None:
    # Break caught: eager owner refresh makes a complete local item fail when
    # Instagram is unavailable instead of taking the processor's existing skip.
    post = FakePost(owner_profile=FakeProfile())
    loader = FakeLoader(logged_in=False)
    patch_post_lookup(monkeypatch, post)
    adapter = make_adapter(
        test_settings=test_settings,
        repository=repository,
        loader=loader,
        configured=False,
    )
    first = adapter.download_input(parsed, f"job-seed-{parsed.kind}")

    lookup_calls = patch_post_lookup(
        monkeypatch,
        AssertionError("complete local media must not reach Instagram"),
    )
    second = adapter.download_input(parsed, f"job-skip-{parsed.kind}")

    assert second == first
    assert lookup_calls == []
    assert loader.downloaded_posts == [post]


def test_new_direct_media_reuses_local_profile_without_avatar_refresh(
    monkeypatch: pytest.MonkeyPatch,
    repository: LibraryRepository,
    stored_profile: ProfileSnapshot,
    test_settings: Settings,
) -> None:
    # Break caught: finding the media owner locally but still upserting it
    # rewrites metadata and performs an unnecessary avatar network request.
    remote_owner = FakeProfile(
        full_name="Remote name must not replace local metadata",
        profile_pic_url="https://cdn.example/avatar.jpg",
    )
    loader = FakeLoader(logged_in=False)
    loader.context.get_raw = lambda _url: (_ for _ in ()).throw(  # type: ignore[attr-defined]
        AssertionError("local Profile reuse must not download an avatar")
    )
    lookup_calls = patch_post_lookup(
        monkeypatch,
        FakePost(owner_profile=remote_owner),
    )
    adapter = make_adapter(
        test_settings=test_settings,
        repository=repository,
        loader=loader,
        configured=False,
    )

    saved = adapter.download_input(
        ReelInput(
            shortcode=SHORTCODE,
            canonical_url=f"https://www.instagram.com/reel/{SHORTCODE}/",
        ),
        "job-local-owner",
    )

    assert lookup_calls == [(loader.context, SHORTCODE)]
    assert saved.owner_profile_id == stored_profile.id
    assert repository.get_profile(stored_profile.id) == stored_profile


def test_direct_story_reuses_profile_found_by_story_username(
    monkeypatch: pytest.MonkeyPatch,
    repository: LibraryRepository,
    stored_profile: ProfileSnapshot,
    test_settings: Settings,
) -> None:
    # Break caught: Story URLs already identify the local owner by username;
    # refreshing that Profile needlessly rewrites it and fetches its avatar.
    remote_owner = FakeProfile(
        full_name="Remote name must not replace local metadata",
        profile_pic_url="https://cdn.example/story-avatar.jpg",
    )
    item = FakeStoryItem(owner_profile=remote_owner)
    loader = FakeLoader()
    loader.context.get_raw = lambda _url: (_ for _ in ()).throw(  # type: ignore[attr-defined]
        AssertionError("local Story owner must not download an avatar")
    )
    lookup_calls = patch_story_lookup(monkeypatch, item)
    adapter = make_adapter(
        test_settings=test_settings,
        repository=repository,
        loader=loader,
    )

    saved = adapter.download_input(story_input(), "job-local-story-owner")

    assert lookup_calls == [(loader.context, int(STORY_MEDIA_ID))]
    assert saved.owner_profile_id == stored_profile.id
    assert repository.get_profile(stored_profile.id) == stored_profile
    assert item.download_calls == 1


def test_absent_profile_is_created_before_direct_media_persistence(
    monkeypatch: pytest.MonkeyPatch,
    repository: LibraryRepository,
    test_settings: Settings,
) -> None:
    # Break caught: persisting media before its newly-resolved owner exists
    # either violates the foreign key or leaves the item detached.
    owner = FakeProfile()
    loader = FakeLoader(logged_in=False)
    patch_post_lookup(monkeypatch, FakePost(owner_profile=owner))
    persisted_after_profile_creation: list[str] = []
    original_upsert_media = repository.upsert_media

    def upsert_media(**kwargs: Any):
        local_owner = repository.find_profile_by_instagram_user_id(
            str(owner.userid)
        )
        assert local_owner is not None
        persisted_after_profile_creation.append(local_owner.id)
        return original_upsert_media(**kwargs)

    monkeypatch.setattr(repository, "upsert_media", upsert_media)
    adapter = make_adapter(
        test_settings=test_settings,
        repository=repository,
        loader=loader,
        configured=False,
    )

    saved = adapter.download_input(
        PostInput(
            shortcode=SHORTCODE,
            canonical_url=f"https://www.instagram.com/p/{SHORTCODE}/",
        ),
        "job-create-owner",
    )

    created = repository.find_profile_by_instagram_user_id(str(owner.userid))
    assert created is not None
    assert persisted_after_profile_creation == [created.id]
    assert saved.owner_profile_id == created.id


@pytest.mark.parametrize("kind", ("post", "reel", "story"))
def test_stub_linked_incomplete_media_recovers_when_username_matches(
    monkeypatch: pytest.MonkeyPatch,
    repository: LibraryRepository,
    test_settings: Settings,
    kind: str,
) -> None:
    # Break caught: requiring a stable ID on a local username-only owner turns
    # an incomplete retry into a false not-found instead of repairing media.
    stub = repository.upsert_profile_stub(
        username=USERNAME,
        tracked=False,
        now=NOW,
    )
    assert stub.instagram_user_id is None
    persist_incomplete_media(
        repository=repository,
        profile=stub,
        kind=kind,
    )
    owner = FakeProfile()
    loader = FakeLoader(logged_in=kind == "story")
    if kind == "story":
        patch_story_lookup(
            monkeypatch,
            FakeStoryItem(owner_profile=owner),
        )
        parsed: PostInput | ReelInput | StoryInput = story_input()
    else:
        patch_post_lookup(
            monkeypatch,
            FakePost(owner_profile=owner),
        )
        route = "reel" if kind == "reel" else "p"
        parsed = (
            ReelInput(
                shortcode=SHORTCODE,
                canonical_url=f"https://www.instagram.com/{route}/{SHORTCODE}/",
            )
            if kind == "reel"
            else PostInput(
                shortcode=SHORTCODE,
                canonical_url=f"https://www.instagram.com/{route}/{SHORTCODE}/",
            )
        )
    adapter = make_adapter(
        test_settings=test_settings,
        repository=repository,
        loader=loader,
        configured=kind == "story",
    )

    saved = adapter.download_input(parsed, f"job-stub-retry-{kind}")

    assert saved.owner_profile_id == stub.id
    assert saved.assets
    assert repository.get_profile(stub.id) == stub


@pytest.mark.parametrize("mismatch", ("username", "stable_id"))
def test_incomplete_media_still_rejects_mismatched_local_owner(
    monkeypatch: pytest.MonkeyPatch,
    repository: LibraryRepository,
    test_settings: Settings,
    mismatch: str,
) -> None:
    # Break caught: unconditional stub reuse can attach downloaded media to a
    # different local account when username or stable identity disagrees.
    if mismatch == "username":
        local_owner = repository.upsert_profile_stub(
            username="local.owner",
            tracked=False,
            now=NOW,
        )
        remote_owner = FakeProfile(username="remote.owner")
    else:
        stub = repository.upsert_profile_stub(
            username=USERNAME,
            tracked=False,
            now=NOW,
        )
        local_owner = repository.update_profile_metadata(
            profile_id=stub.id,
            instagram_user_id="111111111",
            username=USERNAME,
            full_name="Local owner",
            biography="",
            profile_pic_url=None,
            now=NOW,
        )
        remote_owner = FakeProfile()
    persist_incomplete_media(
        repository=repository,
        profile=local_owner,
        kind="reel",
    )
    loader = FakeLoader(logged_in=False)
    patch_post_lookup(
        monkeypatch,
        FakePost(owner_profile=remote_owner),
    )
    adapter = make_adapter(
        test_settings=test_settings,
        repository=repository,
        loader=loader,
        configured=False,
    )

    with pytest.raises(MediaItemFailure) as caught:
        adapter.download_input(
            ReelInput(
                shortcode=SHORTCODE,
                canonical_url=f"https://www.instagram.com/reel/{SHORTCODE}/",
            ),
            f"job-owner-{mismatch}",
        )

    assert caught.value.issue.error_code == "instagram_not_found"
    assert loader.downloaded_posts == []


@pytest.mark.parametrize("kind", ("reel", "story"))
def test_username_only_local_profile_never_refreshes_avatar(
    monkeypatch: pytest.MonkeyPatch,
    repository: LibraryRepository,
    test_settings: Settings,
    kind: str,
) -> None:
    # Break caught: a local username-only Profile is still a reusable owner;
    # upgrading it or fetching its avatar violates local-first direct media.
    stub = repository.upsert_profile_stub(
        username=USERNAME.upper(),
        tracked=False,
        now=NOW,
    )
    remote_owner = FakeProfile(
        profile_pic_url="https://cdn.example/username-only-avatar.jpg",
    )
    loader = FakeLoader(logged_in=kind == "story")

    def reject_avatar(_url: str) -> None:
        raise AssertionError("username-only local Profile must skip avatar refresh")

    loader.context.get_raw = reject_avatar  # type: ignore[attr-defined]
    if kind == "story":
        patch_story_lookup(
            monkeypatch,
            FakeStoryItem(owner_profile=remote_owner),
        )
        parsed: ReelInput | StoryInput = story_input()
    else:
        patch_post_lookup(
            monkeypatch,
            FakePost(owner_profile=remote_owner),
        )
        parsed = ReelInput(
            shortcode=SHORTCODE,
            canonical_url=f"https://www.instagram.com/reel/{SHORTCODE}/",
        )
    adapter = make_adapter(
        test_settings=test_settings,
        repository=repository,
        loader=loader,
        configured=kind == "story",
    )

    saved = adapter.download_input(parsed, f"job-username-only-{kind}")

    assert saved.owner_profile_id == stub.id
    assert saved.instagram_media_id is not None
    assert repository.get_profile(stub.id) == stub


@pytest.mark.parametrize(
    ("kind", "local_username", "inactive_status"),
    (
        ("post", USERNAME, "deletion_pending"),
        ("reel", USERNAME.upper(), "deletion_failed"),
        ("story", USERNAME.upper(), "deletion_pending"),
    ),
)
def test_inactive_local_profile_is_not_reused_for_new_direct_media(
    monkeypatch: pytest.MonkeyPatch,
    repository: LibraryRepository,
    test_settings: Settings,
    kind: str,
    local_username: str,
    inactive_status: str,
) -> None:
    # Break caught: exact and casefold local lookup must not attach new media
    # to a Profile whose deletion workflow is already in progress or failed.
    stub = repository.upsert_profile_stub(
        username=local_username,
        tracked=False,
        now=NOW,
    )
    if inactive_status == "deletion_pending":
        inactive = repository.mark_profile_for_deletion(stub.id, NOW)
    else:
        inactive = repository.mark_profile_deletion_failed(stub.id, NOW)
    assert inactive is not None
    owner = FakeProfile(
        profile_pic_url="https://cdn.example/inactive-avatar.jpg",
    )
    loader = FakeLoader(logged_in=kind == "story")

    def reject_avatar(_url: str) -> None:
        raise AssertionError("inactive Profile must not refresh its avatar")

    loader.context.get_raw = reject_avatar  # type: ignore[attr-defined]
    if kind == "story":
        patch_story_lookup(
            monkeypatch,
            FakeStoryItem(owner_profile=owner),
        )
        parsed: PostInput | ReelInput | StoryInput = story_input()
        identity = MediaIdentity("story_media_id", STORY_MEDIA_ID)
    else:
        patch_post_lookup(
            monkeypatch,
            FakePost(owner_profile=owner),
        )
        route = "reel" if kind == "reel" else "p"
        parsed = (
            ReelInput(
                shortcode=SHORTCODE,
                canonical_url=f"https://www.instagram.com/{route}/{SHORTCODE}/",
            )
            if kind == "reel"
            else PostInput(
                shortcode=SHORTCODE,
                canonical_url=f"https://www.instagram.com/{route}/{SHORTCODE}/",
            )
        )
        identity = MediaIdentity("shortcode", SHORTCODE)
    adapter = make_adapter(
        test_settings=test_settings,
        repository=repository,
        loader=loader,
        configured=kind == "story",
    )

    with pytest.raises(MediaItemFailure) as caught:
        adapter.download_input(parsed, f"job-inactive-{kind}")

    assert caught.value.issue.error_code == "instagram_not_found"
    assert repository.find_media_by_identity(identity) is None
    assert repository.get_profile(stub.id) == inactive
    assert loader.downloaded_posts == []


def test_incomplete_media_linked_to_inactive_profile_is_not_repaired(
    monkeypatch: pytest.MonkeyPatch,
    repository: LibraryRepository,
    test_settings: Settings,
) -> None:
    # Break caught: an existing media owner hint must not bypass Profile status
    # and repair content into a Profile that is pending deletion.
    stub = repository.upsert_profile_stub(
        username=USERNAME,
        tracked=False,
        now=NOW,
    )
    identity = persist_incomplete_media(
        repository=repository,
        profile=stub,
        kind="post",
    )
    inactive = repository.mark_profile_for_deletion(stub.id, NOW)
    assert inactive is not None
    loader = FakeLoader(logged_in=False)
    patch_post_lookup(
        monkeypatch,
        FakePost(owner_profile=FakeProfile()),
    )
    adapter = make_adapter(
        test_settings=test_settings,
        repository=repository,
        loader=loader,
        configured=False,
    )

    with pytest.raises(MediaItemFailure) as caught:
        adapter.download_input(
            PostInput(
                shortcode=SHORTCODE,
                canonical_url=f"https://www.instagram.com/p/{SHORTCODE}/",
            ),
            "job-inactive-existing",
        )

    remaining = repository.find_media_by_identity(identity)
    assert caught.value.issue.error_code == "instagram_not_found"
    assert remaining is not None
    assert remaining.assets == ()
    assert repository.get_profile(stub.id) == inactive
    assert loader.downloaded_posts == []


def test_direct_media_avatar_failure_remains_nonfatal(
    monkeypatch: pytest.MonkeyPatch,
    repository: LibraryRepository,
    test_settings: Settings,
) -> None:
    # Break caught: applying strict profile-sync avatar preflight to direct
    # media makes an otherwise downloadable item fail.
    item = FakeStoryItem(
        owner_profile=FakeProfile(profile_pic_url=None),
    )
    patch_story_lookup(monkeypatch, item)
    adapter = make_adapter(
        test_settings=test_settings,
        repository=repository,
        loader=FakeLoader(),
    )

    saved = adapter.download_input(story_input(), "job-no-avatar")

    assert saved.story_media_id == STORY_MEDIA_ID
    assert item.download_calls == 1


def test_owner_upsert_reuses_existing_webp_avatar_without_network_refresh(
    repository: LibraryRepository,
    stored_profile: ProfileSnapshot,
    test_settings: Settings,
) -> None:
    avatar = (
        test_settings.media_root
        / "profile-avatars"
        / f"{stored_profile.id}.webp"
    )
    avatar.parent.mkdir(parents=True)
    avatar.write_bytes(b"RIFF\x0c\x00\x00\x00WEBPVP8 \x00\x00\x00\x00")
    loader = FakeLoader(logged_in=False)
    loader.context.get_raw = lambda _url: (  # type: ignore[attr-defined]
        _ for _ in ()
    ).throw(AssertionError("existing WebP avatar must skip network refresh"))
    adapter = make_adapter(
        test_settings=test_settings,
        repository=repository,
        loader=loader,
        configured=False,
    )
    staging_directory = test_settings.jobs_root / "owner-upsert-webp"
    staging_directory.mkdir(parents=True)

    owner = adapter._upsert_owner(
        loader=cast(Any, loader),
        profile=cast(
            Any,
            FakeProfile(profile_pic_url="https://cdn.example/avatar"),
        ),
        staging_directory=staging_directory,
    )

    assert owner.id == stored_profile.id
    assert avatar.is_file()


def test_profile_story_candidates_are_lightweight_and_resolve_lazily(
    repository: LibraryRepository,
    stored_profile: ProfileSnapshot,
    test_settings: Settings,
) -> None:
    # Break caught: reading fragile Story metadata during enumeration turns one
    # bad item into a fatal incomplete-manifest failure.
    accesses: list[str] = []
    owner = FakeProfile()

    class LazyStoryItem:
        download_calls = 0

        @property
        def mediaid(self) -> int:
            accesses.append("mediaid")
            return int(STORY_MEDIA_ID)

        @property
        def owner_profile(self) -> FakeProfile:
            accesses.append("owner_profile")
            return owner

        @property
        def date_utc(self) -> datetime:
            accesses.append("date_utc")
            return PUBLISHED_AT

        @property
        def expiring_utc(self) -> datetime:
            accesses.append("expiring_utc")
            return EXPIRES_AT

        @property
        def is_video(self) -> bool:
            accesses.append("is_video")
            return False

        @property
        def caption(self) -> str:
            accesses.append("caption")
            return "Lazy caption"

    item = LazyStoryItem()
    loader = FakeLoader()
    loader.stories = (FakeStory((item,)),)  # type: ignore[arg-type]
    adapter = make_adapter(
        test_settings=test_settings,
        repository=repository,
        loader=loader,
    )

    candidates = tuple(
        adapter.iter_story_candidates(
            cast(Any, FakeProfile()),
            stored_profile.id,
        )
    )

    assert len(candidates) == 1
    assert candidates[0].identity == MediaIdentity(
        "story_media_id",
        STORY_MEDIA_ID,
    )
    assert candidates[0].kind == "story"
    assert accesses == ["mediaid"]
    assert loader.story_userids == [[FakeProfile().userid]]

    resolved = candidates[0].resolve()

    assert accesses == [
        "mediaid",
        "owner_profile",
        "date_utc",
        "expiring_utc",
        "is_video",
        "caption",
    ]
    assert resolved.profile_id == stored_profile.id
    assert resolved.original_url == CANONICAL_STORY_URL
