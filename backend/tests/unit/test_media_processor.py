from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from instaloader import ConnectionException, Instaloader
from sqlalchemy.exc import IntegrityError

from instaloader_webui.config import Settings
from instaloader_webui.db.library_repositories import (
    LibraryRepository,
    MediaIdentity,
    NormalizedAsset,
    NormalizedMedia,
    ProfileSnapshot,
)
from instaloader_webui.db.migrations import run_migrations
from instaloader_webui.instagram.media_processor import MediaProcessor
from instaloader_webui.instagram.media_types import MediaCandidate, ResolvedMedia
from instaloader_webui.instagram.public_adapter import PublicInstaloaderAdapter
from instaloader_webui.instagram.safe_issues import MediaItemFailure
from instaloader_webui.services.profile_avatars import profile_avatar_path

NOW = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)
SHORTCODE = "DOqEJyxCRGJ"
STORY_MEDIA_ID = "3952742051065980676"
DownloadAction = Callable[[Instaloader, str], None]


@pytest.fixture
def repository(session_factory, test_settings: Settings) -> LibraryRepository:
    run_migrations(test_settings)
    return LibraryRepository(session_factory)


@pytest.fixture
def profile(repository: LibraryRepository) -> ProfileSnapshot:
    stub = repository.upsert_profile_stub(
        username="katerina.soria",
        tracked=True,
        now=NOW,
    )
    return repository.update_profile_metadata(
        profile_id=stub.id,
        instagram_user_id="8642097531",
        username=stub.username,
        full_name="Katerina Soria",
        biography="",
        profile_pic_url=None,
        now=NOW,
    )


@pytest.fixture
def loader(tmp_path: Path):
    instance = Instaloader(
        dirname_pattern=str(tmp_path / "initial"),
        filename_pattern="{shortcode}",
        download_pictures=True,
        download_videos=True,
        download_video_thumbnails=True,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        post_metadata_txt_pattern="",
        quiet=True,
    )
    yield instance
    instance.close()


@pytest.fixture
def processor(
    test_settings: Settings,
    repository: LibraryRepository,
    loader: Instaloader,
) -> MediaProcessor:
    return MediaProcessor(
        data_root=test_settings.data_root,
        media_root=test_settings.media_root,
        jobs_root=test_settings.jobs_root,
        library=repository,
        loader=loader,
    )


def _download_files(*files: tuple[str, bytes]) -> DownloadAction:
    def download(loader: Instaloader, _target: str) -> None:
        directory = Path(loader.dirname_pattern)
        for name, content in files:
            (directory / name).write_bytes(content)

    return download


def _resolved_media(
    *,
    profile: ProfileSnapshot,
    identity: MediaIdentity | None = None,
    kind: str = "reel",
    content_kinds: tuple[str, ...] = ("video",),
    download: DownloadAction | None = None,
    profile_id: str | None = None,
) -> ResolvedMedia:
    selected_identity = identity or MediaIdentity("shortcode", SHORTCODE)
    shortcode = (
        selected_identity.value
        if selected_identity.identity_type == "shortcode"
        else None
    )
    return ResolvedMedia(
        identity=selected_identity,
        kind=kind,
        instagram_media_id=(
            "17800000000000001"
            if shortcode is not None
            else selected_identity.value
        ),
        shortcode=shortcode,
        profile_id=profile_id or profile.id,
        instagram_user_id=profile.instagram_user_id or "",
        owner_username=profile.username,
        caption="Caption",
        accessibility_caption="Accessibility caption",
        published_at=NOW,
        story_expires_at=None,
        original_url=(
            f"https://www.instagram.com/reel/{shortcode}/"
            if shortcode is not None
            else (
                "https://www.instagram.com/stories/"
                f"{profile.username}/{selected_identity.value}/"
            )
        ),
        content_kinds=content_kinds,
        download=download or _download_files(),
    )


def _candidate(
    resolved: ResolvedMedia,
    *,
    resolve: Callable[[], ResolvedMedia] | None = None,
) -> MediaCandidate:
    return MediaCandidate(
        identity=resolved.identity,
        kind=resolved.kind,
        session_configured=True,
        resolve=resolve or (lambda: resolved),
    )


def _persist_existing(
    *,
    repository: LibraryRepository,
    profile: ProfileSnapshot,
    media_root: Path,
    kind: str,
    assets: tuple[tuple[str, str, int, str, bytes], ...],
):
    final_directory = (
        media_root
        / "profiles"
        / (profile.instagram_user_id or "")
        / SHORTCODE
    )
    final_directory.mkdir(parents=True)
    normalized_assets = []
    for filename, asset_kind, position, role, content in assets:
        path = final_directory / filename
        path.write_bytes(content)
        normalized_assets.append(
            NormalizedAsset(
                relative_path=path.relative_to(media_root).as_posix(),
                mime_type=(
                    "video/mp4" if asset_kind == "video" else "image/jpeg"
                ),
                kind=asset_kind,
                role=role,
                position=position,
                file_size=len(content),
            )
        )
    media = repository.upsert_media(
        normalized=NormalizedMedia(
            identity=MediaIdentity("shortcode", SHORTCODE),
            instagram_media_id="17800000000000001",
            shortcode=SHORTCODE,
            kind=kind,
            caption="Old caption",
            accessibility_caption="",
            published_at=NOW,
            story_expires_at=None,
            original_url=f"https://www.instagram.com/p/{SHORTCODE}/",
        ),
        profile_id=profile.id,
        assets=tuple(normalized_assets),
        now=NOW,
    )
    return media, final_directory


def test_image_candidate_saves_one_content_asset_at_position_zero(
    processor: MediaProcessor,
    repository: LibraryRepository,
    profile: ProfileSnapshot,
    test_settings: Settings,
) -> None:
    # Break caught: treating every JPG as a poster would leave image posts empty.
    resolved = _resolved_media(
        profile=profile,
        kind="post",
        content_kinds=("image",),
        download=_download_files((f"{SHORTCODE}.jpg", b"jpeg")),
    )

    result = processor.process(_candidate(resolved), job_id="job-image")

    assert result.status == "saved"
    assert [(asset.kind, asset.role, asset.position) for asset in result.media.assets] == [
        ("image", "content", 0)
    ]
    assert repository.find_media_by_identity(resolved.identity) == result.media
    assert not (
        test_settings.jobs_root / "job-image" / resolved.identity.value
    ).exists()


def test_video_candidate_maps_jpg_to_poster_not_content(
    processor: MediaProcessor,
    profile: ProfileSnapshot,
) -> None:
    # Break caught: assigning roles by extension would count the thumbnail as content.
    resolved = _resolved_media(
        profile=profile,
        content_kinds=("video",),
        download=_download_files(
            (f"{SHORTCODE}.jpg", b"jpeg"),
            (f"{SHORTCODE}.mp4", b"video"),
        ),
    )

    result = processor.process(_candidate(resolved), job_id="job-video")

    assert result.status == "saved"
    assert [(asset.kind, asset.role, asset.position) for asset in result.media.assets] == [
        ("video", "content", 0),
        ("image", "poster", 0),
    ]


def test_sidecar_sequence_maps_content_and_posters_to_logical_positions(
    processor: MediaProcessor,
    profile: ProfileSnapshot,
) -> None:
    # Break caught: enumerating physical files would give a poster its own position.
    resolved = _resolved_media(
        profile=profile,
        kind="post",
        content_kinds=("image", "video", "image"),
        download=_download_files(
            (f"{SHORTCODE}_1.jpg", b"image-one"),
            (f"{SHORTCODE}_2.jpg", b"poster-two"),
            (f"{SHORTCODE}_2.mp4", b"video-two"),
            (f"{SHORTCODE}_3.jpg", b"image-three"),
        ),
    )

    result = processor.process(_candidate(resolved), job_id="job-sidecar")

    assert [(asset.kind, asset.role, asset.position) for asset in result.media.assets] == [
        ("image", "content", 0),
        ("video", "content", 1),
        ("image", "poster", 1),
        ("image", "content", 2),
    ]


def test_complete_existing_media_with_valid_files_and_roles_skips_resolution(
    processor: MediaProcessor,
    repository: LibraryRepository,
    profile: ProfileSnapshot,
    test_settings: Settings,
) -> None:
    # Break caught: resolving complete items would make metadata/network failures fatal.
    existing, _final_directory = _persist_existing(
        repository=repository,
        profile=profile,
        media_root=test_settings.media_root,
        kind="reel",
        assets=(
            (f"{SHORTCODE}.mp4", "video", 0, "content", b"old-video"),
            (f"{SHORTCODE}.jpg", "image", 0, "poster", b"old-poster"),
        ),
    )
    resolved = _resolved_media(profile=profile)

    result = processor.process(
        _candidate(
            resolved,
            resolve=lambda: (_ for _ in ()).throw(
                AssertionError("complete media must not resolve")
            ),
        ),
        job_id="job-existing",
    )

    assert result.status == "existing"
    assert result.media == existing


def test_legacy_reel_with_jpg_and_mp4_both_content_is_reprocessed(
    processor: MediaProcessor,
    repository: LibraryRepository,
    profile: ProfileSnapshot,
    test_settings: Settings,
) -> None:
    # Break caught: checking files only would preserve the migrated poster as content.
    legacy, final_directory = _persist_existing(
        repository=repository,
        profile=profile,
        media_root=test_settings.media_root,
        kind="reel",
        assets=(
            (f"{SHORTCODE}.jpg", "image", 0, "content", b"legacy-jpeg"),
            (f"{SHORTCODE}.mp4", "video", 1, "content", b"legacy-video"),
        ),
    )
    resolved = _resolved_media(
        profile=profile,
        download=_download_files(
            (f"{SHORTCODE}.jpg", b"new-jpeg"),
            (f"{SHORTCODE}.mp4", b"new-video"),
        ),
    )

    result = processor.process(_candidate(resolved), job_id="job-repair")

    assert result.status == "saved"
    assert result.media.id == legacy.id
    assert [(asset.kind, asset.role, asset.position) for asset in result.media.assets] == [
        ("video", "content", 0),
        ("image", "poster", 0),
    ]
    assert (final_directory / f"{SHORTCODE}.jpg").read_bytes() == b"new-jpeg"
    assert (final_directory / f"{SHORTCODE}.mp4").read_bytes() == b"new-video"


def test_no_content_output_is_an_item_failure_and_leaves_no_record_or_staging(
    processor: MediaProcessor,
    repository: LibraryRepository,
    profile: ProfileSnapshot,
    test_settings: Settings,
) -> None:
    # Break caught: accepting a video thumbnail without its video creates empty media.
    resolved = _resolved_media(
        profile=profile,
        content_kinds=("video",),
        download=_download_files((f"{SHORTCODE}.jpg", b"poster-only")),
    )

    with pytest.raises(MediaItemFailure) as raised:
        processor.process(_candidate(resolved), job_id="job-no-content")

    assert raised.value.issue.error_code == "asset_validation_failed"
    assert raised.value.issue.identity == resolved.identity
    assert repository.find_media_by_identity(resolved.identity) is None
    assert not (
        test_settings.jobs_root / "job-no-content" / resolved.identity.value
    ).exists()
    assert not (
        test_settings.media_root
        / "profiles"
        / (profile.instagram_user_id or "")
        / resolved.identity.value
    ).exists()


def test_unmatched_supported_sidecar_file_is_an_item_failure(
    processor: MediaProcessor,
    repository: LibraryRepository,
    profile: ProfileSnapshot,
) -> None:
    # Break caught: silently ignoring an extra supported file loses carousel content.
    resolved = _resolved_media(
        profile=profile,
        kind="post",
        content_kinds=("image",),
        download=_download_files(
            (f"{SHORTCODE}_1.jpg", b"expected"),
            (f"{SHORTCODE}_2.jpg", b"unexpected"),
        ),
    )

    with pytest.raises(MediaItemFailure) as raised:
        processor.process(_candidate(resolved), job_id="job-unmatched")

    assert raised.value.issue.error_code == "asset_validation_failed"
    assert repository.find_media_by_identity(resolved.identity) is None


def test_each_process_recreates_and_removes_its_identity_staging_directory(
    processor: MediaProcessor,
    profile: ProfileSnapshot,
    test_settings: Settings,
) -> None:
    # Break caught: stale supported output could be persisted with a later item.
    staging = test_settings.jobs_root / "job-clean" / SHORTCODE
    staging.mkdir(parents=True)
    (staging / f"{SHORTCODE}.mp4").write_bytes(b"stale-video")
    resolved = _resolved_media(
        profile=profile,
        kind="post",
        content_kinds=("image",),
        download=_download_files((f"{SHORTCODE}.jpg", b"fresh-image")),
    )

    result = processor.process(_candidate(resolved), job_id="job-clean")

    assert [(asset.kind, asset.role) for asset in result.media.assets] == [
        ("image", "content")
    ]
    assert not staging.exists()


@pytest.mark.parametrize("failure_phase", ["resolve", "download"])
def test_instaloader_resolution_and_download_errors_become_item_failures(
    processor: MediaProcessor,
    repository: LibraryRepository,
    profile: ProfileSnapshot,
    failure_phase: str,
) -> None:
    # Break caught: leaking Instaloader errors would turn item warnings into fatal jobs.
    upstream = ConnectionException("upstream details")
    resolved = _resolved_media(
        profile=profile,
        download=(
            (lambda _loader, _target: (_ for _ in ()).throw(upstream))
            if failure_phase == "download"
            else _download_files((f"{SHORTCODE}.jpg", b"unused"))
        ),
    )
    candidate = _candidate(
        resolved,
        resolve=(
            (lambda: (_ for _ in ()).throw(upstream))
            if failure_phase == "resolve"
            else None
        ),
    )

    with pytest.raises(MediaItemFailure) as raised:
        processor.process(candidate, job_id=f"job-{failure_phase}")

    assert raised.value.issue.identity == resolved.identity
    assert raised.value.issue.kind == "reel"
    assert raised.value.issue.error_code == "instagram_unavailable"
    assert repository.find_media_by_identity(resolved.identity) is None


def test_download_filesystem_error_remains_fatal_infrastructure_failure(
    processor: MediaProcessor,
    repository: LibraryRepository,
    profile: ProfileSnapshot,
    test_settings: Settings,
) -> None:
    # Break caught: broad exception translation would downgrade disk failures to warnings.
    disk_error = OSError("disk unavailable")
    resolved = _resolved_media(
        profile=profile,
        download=lambda _loader, _target: (_ for _ in ()).throw(disk_error),
    )

    with pytest.raises(OSError) as raised:
        processor.process(_candidate(resolved), job_id="job-disk")

    assert raised.value is disk_error
    assert repository.find_media_by_identity(resolved.identity) is None
    assert not (
        test_settings.jobs_root / "job-disk" / resolved.identity.value
    ).exists()


def test_database_failure_restores_previous_final_directory(
    processor: MediaProcessor,
    repository: LibraryRepository,
    profile: ProfileSnapshot,
    test_settings: Settings,
) -> None:
    # Break caught: installing files before DB commit could destroy the prior good copy.
    legacy, final_directory = _persist_existing(
        repository=repository,
        profile=profile,
        media_root=test_settings.media_root,
        kind="reel",
        assets=(
            (f"{SHORTCODE}.jpg", "image", 0, "content", b"legacy-jpeg"),
            (f"{SHORTCODE}.mp4", "video", 1, "content", b"legacy-video"),
        ),
    )
    resolved = _resolved_media(
        profile=profile,
        profile_id="missing-profile",
        download=_download_files(
            (f"{SHORTCODE}.jpg", b"replacement-jpeg"),
            (f"{SHORTCODE}.mp4", b"replacement-video"),
        ),
    )

    with pytest.raises(IntegrityError):
        processor.process(_candidate(resolved), job_id="job-db-failure")

    assert (final_directory / f"{SHORTCODE}.jpg").read_bytes() == b"legacy-jpeg"
    assert (final_directory / f"{SHORTCODE}.mp4").read_bytes() == b"legacy-video"
    assert repository.find_media_by_identity(resolved.identity) == legacy
    assert not (
        test_settings.jobs_root / "job-db-failure" / resolved.identity.value
    ).exists()


def test_story_final_directory_and_asset_paths_use_identity_value(
    processor: MediaProcessor,
    profile: ProfileSnapshot,
    test_settings: Settings,
) -> None:
    # Break caught: requiring a shortcode would reject or misplace Story files.
    identity = MediaIdentity("story_media_id", STORY_MEDIA_ID)
    resolved = _resolved_media(
        profile=profile,
        identity=identity,
        kind="story",
        content_kinds=("image",),
        download=_download_files(("story-image.jpg", b"story-image")),
    )

    result = processor.process(_candidate(resolved), job_id="job-story")

    expected = (
        Path("profiles")
        / (profile.instagram_user_id or "")
        / STORY_MEDIA_ID
        / "story-image.jpg"
    ).as_posix()
    assert [asset.relative_path for asset in result.media.assets] == [expected]
    assert (test_settings.media_root / expected).read_bytes() == b"story-image"


def test_download_shortcode_compatibility_resolves_sidecar_kinds_for_processor(
    monkeypatch: pytest.MonkeyPatch,
    repository: LibraryRepository,
    profile: ProfileSnapshot,
    loader: Instaloader,
    test_settings: Settings,
) -> None:
    # Break caught: retaining the old physical-file finalizer loses poster roles.
    avatar = profile_avatar_path(test_settings.media_root, profile.id)
    avatar.parent.mkdir(parents=True)
    avatar.write_bytes(b"avatar")
    owner = SimpleNamespace(
        userid=int(profile.instagram_user_id or "0"),
        username=profile.username,
        full_name=profile.full_name,
        biography=profile.biography,
        profile_pic_url=None,
        is_private=False,
        followed_by_viewer=False,
    )
    sidecar_nodes = (
        SimpleNamespace(is_video=False),
        SimpleNamespace(is_video=True),
    )
    post = SimpleNamespace(
        owner_profile=owner,
        shortcode=SHORTCODE,
        mediaid=17800000000000001,
        caption="Sidecar caption",
        accessibility_caption="Sidecar accessibility caption",
        date_utc=NOW,
        typename="GraphSidecar",
        is_video=False,
        get_sidecar_nodes=lambda: iter(sidecar_nodes),
    )

    def download_post(_post, *, target: str) -> None:
        assert target == profile.username
        directory = Path(loader.dirname_pattern)
        for name, content in (
            (f"{SHORTCODE}_1.jpg", b"image"),
            (f"{SHORTCODE}_2.jpg", b"poster"),
            (f"{SHORTCODE}_2.mp4", b"video"),
        ):
            (directory / name).write_bytes(content)

    monkeypatch.setattr(
        "instaloader_webui.instagram.public_adapter.Post.from_shortcode",
        lambda _context, _shortcode: post,
    )
    monkeypatch.setattr(loader, "download_post", download_post)

    class StaticRuntime:
        def acquire(self, staging_directory: Path):
            loader.dirname_pattern = str(staging_directory)
            return loader, False

    adapter = PublicInstaloaderAdapter(
        data_root=test_settings.data_root,
        media_root=test_settings.media_root,
        jobs_root=test_settings.jobs_root,
        library=repository,
        progress=lambda _current, _total, _status: None,
        loader_runtime=StaticRuntime(),  # type: ignore[arg-type]
    )

    saved = adapter.download_shortcode(
        SHORTCODE,
        "job-compat",
        expected_kind="post",
    )

    assert [(asset.kind, asset.role, asset.position) for asset in saved.assets] == [
        ("image", "content", 0),
        ("video", "content", 1),
        ("image", "poster", 1),
    ]
