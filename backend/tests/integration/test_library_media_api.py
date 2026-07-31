from datetime import UTC, datetime, timedelta

import pytest

from instaloader_webui.db.library_repositories import (
    LibraryRepository,
    MediaIdentity,
    NormalizedAsset,
    NormalizedMedia,
)

NOW = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)
STORY_MEDIA_ID = "3952742051065980676"
pytestmark = pytest.mark.anyio


async def _complete_password_change(client) -> None:
    session = await client.get("/api/auth/session")
    response = await client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": session.json()["data"]["csrf_token"]},
        json={
            "current_password": "correct-horse-battery-staple",
            "new_password": "correct-horse-battery-staple",
        },
    )
    assert response.status_code == 200


def _seed_story(library: LibraryRepository):
    profile = library.upsert_profile_stub(
        username="katerina.soria",
        tracked=True,
        now=NOW,
    )
    library.upsert_media(
        normalized=NormalizedMedia(
            identity=MediaIdentity("shortcode", "DOqEJyxCRGJ"),
            instagram_media_id="17800000000000001",
            shortcode="DOqEJyxCRGJ",
            kind="post",
            caption="A post",
            accessibility_caption="A still image",
            published_at=NOW - timedelta(hours=1),
            story_expires_at=None,
            original_url="https://www.instagram.com/p/DOqEJyxCRGJ/",
        ),
        profile_id=profile.id,
        assets=(
            NormalizedAsset(
                relative_path="profiles/katerina.soria/posts/DOqEJyxCRGJ.jpg",
                mime_type="image/jpeg",
                kind="image",
                role="content",
                position=0,
                file_size=101,
            ),
        ),
        now=NOW,
    )
    story = library.upsert_media(
        normalized=NormalizedMedia(
            identity=MediaIdentity("story_media_id", STORY_MEDIA_ID),
            instagram_media_id=STORY_MEDIA_ID,
            shortcode=None,
            kind="story",
            caption="",
            accessibility_caption="",
            published_at=NOW,
            story_expires_at=NOW + timedelta(hours=24),
            original_url=(
                f"https://www.instagram.com/stories/katerina.soria/{STORY_MEDIA_ID}/"
            ),
        ),
        profile_id=profile.id,
        assets=(
            NormalizedAsset(
                relative_path="profiles/katerina.soria/stories/story-poster.jpg",
                mime_type="image/jpeg",
                kind="image",
                role="poster",
                position=0,
                file_size=202,
            ),
            NormalizedAsset(
                relative_path="profiles/katerina.soria/stories/story-video.mp4",
                mime_type="video/mp4",
                kind="video",
                role="content",
                position=0,
                file_size=303,
            ),
        ),
        now=NOW,
    )
    return profile, story


async def test_story_filter_returns_story_identity_and_asset_roles(
    authenticated_client,
) -> None:
    await _complete_password_change(authenticated_client)
    profile, story = _seed_story(authenticated_client.app.state.library_repository)

    response = await authenticated_client.get(
        "/api/media",
        params={"profile_id": profile.id, "kind": "story"},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    [serialized] = response.json()["data"]
    assert serialized["id"] == story.id
    assert serialized["kind"] == "story"
    assert serialized["shortcode"] is None
    assert serialized["story_media_id"] == STORY_MEDIA_ID
    assert serialized["identity_type"] == "story_media_id"
    assert serialized["identity_value"] == STORY_MEDIA_ID
    assert serialized["story_expires_at"] == "2026-08-01T08:00:00Z"
    assert [
        (asset["kind"], asset["role"], asset["position"])
        for asset in serialized["assets"]
    ] == [
        ("video", "content", 0),
        ("image", "poster", 0),
    ]


async def test_story_detail_serializes_nullable_shortcode_and_identity(
    authenticated_client,
) -> None:
    await _complete_password_change(authenticated_client)
    profile, story = _seed_story(authenticated_client.app.state.library_repository)

    response = await authenticated_client.get(f"/api/media/{story.id}")

    assert response.status_code == 200
    serialized = response.json()["data"]
    assert serialized["owner_profile_id"] == profile.id
    assert serialized["kind"] == "story"
    assert serialized["shortcode"] is None
    assert serialized["story_media_id"] == STORY_MEDIA_ID
    assert serialized["identity_type"] == "story_media_id"
    assert serialized["identity_value"] == STORY_MEDIA_ID
    assert serialized["story_expires_at"] == "2026-08-01T08:00:00Z"
    assert serialized["assets"][1]["role"] == "poster"
