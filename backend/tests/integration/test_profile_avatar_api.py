import os
from datetime import UTC, datetime

import pytest

NOW = datetime(2026, 8, 3, 8, 0, tzinfo=UTC)
JPEG_AVATAR = b"\xff\xd8\xff\xe0avatar-image"
WEBP_AVATAR = b"RIFF\x0c\x00\x00\x00WEBPVP8 \x00\x00\x00\x00"
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


def _seed_profile(client):
    return client.app.state.library_repository.upsert_profile_stub(
        username="mihi_727",
        tracked=True,
        now=NOW,
    )


@pytest.mark.parametrize(
    ("suffix", "media_type", "content"),
    (
        (".jpg", "image/jpeg", JPEG_AVATAR),
        (".webp", "image/webp", WEBP_AVATAR),
    ),
)
async def test_profile_avatar_endpoint_serves_native_stored_format(
    authenticated_client,
    test_settings,
    suffix: str,
    media_type: str,
    content: bytes,
) -> None:
    await _complete_password_change(authenticated_client)
    profile = _seed_profile(authenticated_client)
    avatar = test_settings.media_root / "profile-avatars" / f"{profile.id}{suffix}"
    avatar.parent.mkdir(parents=True)
    avatar.write_bytes(content)

    response = await authenticated_client.get(f"/api/profiles/{profile.id}/avatar")

    assert response.status_code == 200
    assert response.headers["content-type"] == media_type
    assert response.headers["cache-control"] == "no-store"
    assert response.content == content


async def test_profile_avatar_endpoint_uses_newest_file_when_both_formats_exist(
    authenticated_client,
    test_settings,
) -> None:
    await _complete_password_change(authenticated_client)
    profile = _seed_profile(authenticated_client)
    avatar_root = test_settings.media_root / "profile-avatars"
    avatar_root.mkdir(parents=True)
    jpeg = avatar_root / f"{profile.id}.jpg"
    webp = avatar_root / f"{profile.id}.webp"
    jpeg.write_bytes(JPEG_AVATAR)
    webp.write_bytes(WEBP_AVATAR)
    os.utime(jpeg, ns=(1_000_000_000, 1_000_000_000))
    os.utime(webp, ns=(2_000_000_000, 2_000_000_000))

    response = await authenticated_client.get(f"/api/profiles/{profile.id}/avatar")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    assert response.content == WEBP_AVATAR
