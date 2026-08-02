"""Deterministic, contained filesystem paths for stored profile avatars."""

import re
from dataclasses import dataclass
from pathlib import Path

PROFILE_AVATAR_MEDIA_TYPE = "image/jpeg"
PROFILE_AVATAR_WEBP_MEDIA_TYPE = "image/webp"
PROFILE_AVATAR_MEDIA_TYPES = (
    PROFILE_AVATAR_MEDIA_TYPE,
    PROFILE_AVATAR_WEBP_MEDIA_TYPE,
)

_PROFILE_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")
_PROFILE_AVATAR_SUFFIXES = {
    PROFILE_AVATAR_MEDIA_TYPE: ".jpg",
    PROFILE_AVATAR_WEBP_MEDIA_TYPE: ".webp",
}


@dataclass(frozen=True, slots=True)
class StoredProfileAvatar:
    path: Path
    media_type: str


def profile_avatar_path(
    media_root: Path,
    profile_id: str,
    media_type: str = PROFILE_AVATAR_MEDIA_TYPE,
) -> Path:
    """Return the deterministic avatar path after validating root containment."""
    if _PROFILE_ID_PATTERN.fullmatch(profile_id) is None:
        raise ValueError("Invalid profile identifier for avatar storage.")
    try:
        suffix = _PROFILE_AVATAR_SUFFIXES[media_type]
    except KeyError as error:
        raise ValueError("Unsupported profile avatar media type.") from error

    root = media_root.resolve()
    candidate = root / "profile-avatars" / f"{profile_id}{suffix}"
    if not candidate.resolve().is_relative_to(root):
        raise ValueError("Profile avatar path is outside the media library.")
    return candidate


def profile_avatar_candidates(
    media_root: Path,
    profile_id: str,
) -> tuple[StoredProfileAvatar, ...]:
    return tuple(
        StoredProfileAvatar(
            path=profile_avatar_path(media_root, profile_id, media_type),
            media_type=media_type,
        )
        for media_type in PROFILE_AVATAR_MEDIA_TYPES
    )


def stored_profile_avatar(
    media_root: Path,
    profile_id: str,
) -> StoredProfileAvatar | None:
    existing: list[tuple[int, StoredProfileAvatar]] = []
    for avatar in profile_avatar_candidates(media_root, profile_id):
        if not avatar.path.is_file():
            continue
        try:
            modified_at = avatar.path.stat().st_mtime_ns
        except FileNotFoundError:
            continue
        existing.append((modified_at, avatar))
    if not existing:
        return None
    return max(existing, key=lambda item: item[0])[1]
