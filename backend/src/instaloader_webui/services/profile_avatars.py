"""Deterministic, contained filesystem paths for stored profile avatars."""

from pathlib import Path
import re

PROFILE_AVATAR_MEDIA_TYPE = "image/jpeg"

_PROFILE_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")


def profile_avatar_path(media_root: Path, profile_id: str) -> Path:
    """Return the deterministic avatar path after validating root containment."""
    if _PROFILE_ID_PATTERN.fullmatch(profile_id) is None:
        raise ValueError("Invalid profile identifier for avatar storage.")

    root = media_root.resolve()
    candidate = root / "profile-avatars" / f"{profile_id}.jpg"
    if not candidate.resolve().is_relative_to(root):
        raise ValueError("Profile avatar path is outside the media library.")
    return candidate
