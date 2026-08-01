from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from instaloader_webui.db.library_repositories import (
    LibraryRepository,
    MediaIdentity,
    NormalizedAsset,
    NormalizedMedia,
)
from instaloader_webui.db.migrations import run_migrations

NOW = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)


@pytest.fixture
def repository(session_factory, test_settings) -> LibraryRepository:
    run_migrations(test_settings)
    return LibraryRepository(session_factory)


@pytest.fixture
def profile(repository: LibraryRepository):
    return repository.upsert_profile_stub(
        username="katerina.soria",
        tracked=True,
        now=NOW,
    )


def _shortcode_media(shortcode: str, *, kind: str) -> NormalizedMedia:
    return NormalizedMedia(
        identity=MediaIdentity("shortcode", shortcode),
        instagram_media_id="17800000000000001",
        shortcode=shortcode,
        kind=kind,
        caption="Caption",
        accessibility_caption="Accessibility caption",
        published_at=NOW,
        story_expires_at=None,
        original_url=f"https://www.instagram.com/p/{shortcode}/",
    )


def _asset(
    relative_path: str,
    *,
    kind: str = "image",
    role: str = "content",
    position: int = 0,
) -> NormalizedAsset:
    return NormalizedAsset(
        relative_path=relative_path,
        mime_type="video/mp4" if kind == "video" else "image/jpeg",
        kind=kind,
        role=role,
        position=position,
        file_size=10,
    )


def test_upsert_story_uses_story_identity_and_orders_poster_after_content(
    repository: LibraryRepository, profile
) -> None:
    identity = MediaIdentity("story_media_id", "3952742051065980676")

    saved = repository.upsert_media(
        normalized=NormalizedMedia(
            identity=identity,
            instagram_media_id="3952742051065980676",
            shortcode=None,
            kind="story",
            caption="",
            accessibility_caption="",
            published_at=NOW,
            story_expires_at=NOW + timedelta(hours=24),
            original_url=(
                "https://www.instagram.com/stories/"
                "katerina.soria/3952742051065980676/"
            ),
        ),
        profile_id=profile.id,
        assets=(
            _asset(
                "profiles/1/story/poster.jpg",
                kind="image",
                role="poster",
            ),
            _asset(
                "profiles/1/story/video.mp4",
                kind="video",
                role="content",
            ),
        ),
        now=NOW,
    )

    assert saved.identity_type == "story_media_id"
    assert saved.identity_value == "3952742051065980676"
    assert saved.shortcode is None
    assert saved.story_media_id == "3952742051065980676"
    assert saved.story_expires_at == NOW + timedelta(hours=24)
    assert [(asset.kind, asset.role, asset.position) for asset in saved.assets] == [
        ("video", "content", 0),
        ("image", "poster", 0),
    ]
    assert repository.find_media_by_identity(identity) == saved


def test_upsert_shortcode_repairs_post_to_reel_without_duplication(
    repository: LibraryRepository, profile
) -> None:
    shortcode = "DOqEJyxCRGJ"
    created = repository.upsert_media(
        normalized=_shortcode_media(shortcode, kind="post"),
        profile_id=profile.id,
        assets=(_asset("profiles/1/post/content.jpg"),),
        now=NOW,
    )

    repaired = repository.upsert_media(
        normalized=_shortcode_media(shortcode, kind="reel"),
        profile_id=profile.id,
        assets=(
            _asset("profiles/1/reel/content.mp4", kind="video"),
            _asset("profiles/1/reel/poster.jpg", role="poster"),
        ),
        now=NOW + timedelta(minutes=1),
    )

    assert repaired.id == created.id
    assert repaired.kind == "reel"
    assert repository.count_media(profile_id=profile.id) == 1
    assert repository.find_media_by_shortcode(shortcode) == repaired


def test_asset_replacement_removes_prior_role_rows(
    repository: LibraryRepository, profile
) -> None:
    shortcode = "DOqEJyxCRGJ"
    created = repository.upsert_media(
        normalized=_shortcode_media(shortcode, kind="reel"),
        profile_id=profile.id,
        assets=(
            _asset("profiles/1/old/content.mp4", kind="video"),
            _asset("profiles/1/old/poster.jpg", role="poster"),
        ),
        now=NOW,
    )

    replaced = repository.upsert_media(
        normalized=_shortcode_media(shortcode, kind="reel"),
        profile_id=profile.id,
        assets=(_asset("profiles/1/new/content.mp4", kind="video"),),
        now=NOW + timedelta(minutes=1),
    )

    assert replaced.id == created.id
    assert [
        (asset.relative_path, asset.role) for asset in replaced.assets
    ] == [("profiles/1/new/content.mp4", "content")]
    assert repository.get_media(created.id) == replaced


def test_failed_asset_replacement_rolls_back_prior_assets(
    repository: LibraryRepository, profile
) -> None:
    first = repository.upsert_media(
        normalized=_shortcode_media("DOqEJyxCRGJ", kind="reel"),
        profile_id=profile.id,
        assets=(_asset("profiles/1/first/content.jpg"),),
        now=NOW,
    )
    repository.upsert_media(
        normalized=NormalizedMedia(
            identity=MediaIdentity("shortcode", "CmzV2H-rrlI"),
            instagram_media_id="17800000000000002",
            shortcode="CmzV2H-rrlI",
            kind="post",
            caption="",
            accessibility_caption="",
            published_at=NOW,
            story_expires_at=None,
            original_url="https://www.instagram.com/p/CmzV2H-rrlI/",
        ),
        profile_id=profile.id,
        assets=(_asset("profiles/1/second/content.jpg"),),
        now=NOW,
    )

    with pytest.raises(IntegrityError):
        repository.upsert_media(
            normalized=_shortcode_media("DOqEJyxCRGJ", kind="reel"),
            profile_id=profile.id,
            assets=(_asset("profiles/1/second/content.jpg"),),
            now=NOW + timedelta(minutes=1),
        )

    unchanged = repository.get_media(first.id)
    assert unchanged is not None
    assert [asset.relative_path for asset in unchanged.assets] == [
        "profiles/1/first/content.jpg"
    ]
