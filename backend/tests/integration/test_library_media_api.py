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


def _seed_feed_media(
    library: LibraryRepository,
    *,
    profile_id: str,
    shortcode: str,
    published_at: datetime,
    kind: str = "post",
):
    return library.upsert_media(
        normalized=NormalizedMedia(
            identity=MediaIdentity("shortcode", shortcode),
            instagram_media_id=None,
            shortcode=shortcode,
            kind=kind,
            caption=shortcode,
            accessibility_caption="",
            published_at=published_at,
            story_expires_at=None,
            original_url=f"https://www.instagram.com/p/{shortcode}/",
        ),
        profile_id=profile_id,
        assets=(),
        now=NOW,
    )


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


async def test_feed_anchor_returns_adjacent_media_and_bidirectional_cursors(
    authenticated_client,
) -> None:
    await _complete_password_change(authenticated_client)
    library = authenticated_client.app.state.library_repository
    profile = library.upsert_profile_stub(
        username="feed.owner",
        tracked=True,
        now=NOW,
    )
    media = [
        _seed_feed_media(
            library,
            profile_id=profile.id,
            shortcode=f"ITEM{index}",
            published_at=NOW + timedelta(minutes=index),
        )
        for index in range(1, 6)
    ]

    response = await authenticated_client.get(
        "/api/media/feed",
        params={"anchor_id": media[2].id, "profile_id": profile.id, "limit": 3},
    )

    assert response.status_code == 200
    page = response.json()["data"]
    assert [item["shortcode"] for item in page["items"]] == [
        "ITEM4",
        "ITEM3",
        "ITEM2",
    ]
    assert page["newer_cursor"] is not None
    assert page["older_cursor"] is not None


async def test_feed_cursors_traverse_both_directions_without_duplicates(
    authenticated_client,
) -> None:
    await _complete_password_change(authenticated_client)
    library = authenticated_client.app.state.library_repository
    profile = library.upsert_profile_stub(
        username="cursor.owner",
        tracked=True,
        now=NOW,
    )
    media = [
        _seed_feed_media(
            library,
            profile_id=profile.id,
            shortcode=f"PAGE{index}",
            published_at=NOW + timedelta(minutes=index),
        )
        for index in range(1, 6)
    ]
    initial_response = await authenticated_client.get(
        "/api/media/feed",
        params={"anchor_id": media[2].id, "profile_id": profile.id, "limit": 3},
    )
    assert initial_response.status_code == 200
    initial = initial_response.json()["data"]

    newer_response = await authenticated_client.get(
        "/api/media/feed",
        params={
            "cursor": initial["newer_cursor"],
            "profile_id": profile.id,
            "limit": 3,
        },
    )
    older_response = await authenticated_client.get(
        "/api/media/feed",
        params={
            "cursor": initial["older_cursor"],
            "profile_id": profile.id,
            "limit": 3,
        },
    )

    assert newer_response.status_code == 200
    assert older_response.status_code == 200
    newer = newer_response.json()["data"]
    older = older_response.json()["data"]
    assert [item["shortcode"] for item in newer["items"]] == ["PAGE5"]
    assert newer["newer_cursor"] is None
    assert [item["shortcode"] for item in older["items"]] == ["PAGE1"]
    assert older["older_cursor"] is None
    assert {
        item["id"] for page in (newer, initial, older) for item in page["items"]
    } == {item.id for item in media}


async def test_feed_cursor_rejects_different_filter_context(
    authenticated_client,
) -> None:
    await _complete_password_change(authenticated_client)
    library = authenticated_client.app.state.library_repository
    first_profile = library.upsert_profile_stub(
        username="first.owner",
        tracked=True,
        now=NOW,
    )
    second_profile = library.upsert_profile_stub(
        username="second.owner",
        tracked=True,
        now=NOW,
    )
    anchor = _seed_feed_media(
        library,
        profile_id=first_profile.id,
        shortcode="FILTER1",
        published_at=NOW,
        kind="reel",
    )
    _seed_feed_media(
        library,
        profile_id=first_profile.id,
        shortcode="FILTER2",
        published_at=NOW - timedelta(minutes=1),
        kind="reel",
    )
    initial_response = await authenticated_client.get(
        "/api/media/feed",
        params={
            "anchor_id": anchor.id,
            "profile_id": first_profile.id,
            "kind": "reel",
            "limit": 1,
        },
    )
    assert initial_response.status_code == 200
    cursor = initial_response.json()["data"]["older_cursor"]

    response = await authenticated_client.get(
        "/api/media/feed",
        params={
            "cursor": cursor,
            "profile_id": second_profile.id,
            "kind": "reel",
            "limit": 1,
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_media_feed_cursor"


async def test_feed_rejects_malformed_cursor_payload(
    authenticated_client,
) -> None:
    await _complete_password_change(authenticated_client)

    response = await authenticated_client.get(
        "/api/media/feed",
        params={"cursor": "not-a-valid-cursor"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_media_feed_cursor"


async def test_feed_returns_media_with_tied_timestamps_in_stable_id_order(
    authenticated_client,
) -> None:
    await _complete_password_change(authenticated_client)
    library = authenticated_client.app.state.library_repository
    profile = library.upsert_profile_stub(
        username="ties.owner",
        tracked=True,
        now=NOW,
    )
    media = [
        _seed_feed_media(
            library,
            profile_id=profile.id,
            shortcode=f"TIE{index}",
            published_at=NOW,
        )
        for index in range(4)
    ]
    expected_ids = sorted((item.id for item in media), reverse=True)

    initial_response = await authenticated_client.get(
        "/api/media/feed",
        params={
            "anchor_id": expected_ids[1],
            "profile_id": profile.id,
            "limit": 3,
        },
    )
    assert initial_response.status_code == 200
    initial = initial_response.json()["data"]
    older_response = await authenticated_client.get(
        "/api/media/feed",
        params={
            "cursor": initial["older_cursor"],
            "profile_id": profile.id,
            "limit": 3,
        },
    )

    assert [item["id"] for item in initial["items"]] == expected_ids[:3]
    assert older_response.status_code == 200
    assert [item["id"] for item in older_response.json()["data"]["items"]] == [
        expected_ids[3]
    ]


async def test_feed_returns_not_found_for_missing_anchor(
    authenticated_client,
) -> None:
    await _complete_password_change(authenticated_client)
    missing_id = "00000000-0000-0000-0000-000000000000"

    response = await authenticated_client.get(
        "/api/media/feed",
        params={"anchor_id": missing_id},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "media_not_found"
