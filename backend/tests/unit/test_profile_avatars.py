from pathlib import Path

from instaloader_webui.services.profile_avatars import stored_profile_avatar


def test_stored_profile_avatar_ignores_candidate_removed_during_selection(
    monkeypatch,
    test_settings,
) -> None:
    avatar_root = test_settings.media_root / "profile-avatars"
    avatar_root.mkdir(parents=True)
    jpeg = avatar_root / "profile-id.jpg"
    webp = avatar_root / "profile-id.webp"
    jpeg.write_bytes(b"\xff\xd8\xff\xe0avatar-image")
    webp.write_bytes(b"RIFF\x0c\x00\x00\x00WEBPVP8 \x00\x00\x00\x00")
    original_is_file = Path.is_file

    def remove_jpeg_after_existence_check(path: Path) -> bool:
        exists = original_is_file(path)
        if path == jpeg and exists:
            path.unlink()
        return exists

    monkeypatch.setattr(Path, "is_file", remove_jpeg_after_existence_check)

    stored = stored_profile_avatar(test_settings.media_root, "profile-id")

    assert stored is not None
    assert stored.path == webp
    assert stored.media_type == "image/webp"
