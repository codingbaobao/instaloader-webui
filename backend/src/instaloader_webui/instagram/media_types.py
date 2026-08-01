"""Shared media-domain inputs and outcomes for Instagram processing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from instaloader import Instaloader

from instaloader_webui.db.library_repositories import MediaIdentity, MediaSnapshot

MediaKind = Literal["post", "reel", "story"]
ContentKind = Literal["image", "video"]
DownloadAction = Callable[[Instaloader, str], None]
ResolveAction = Callable[[], "ResolvedMedia"]


@dataclass(frozen=True, slots=True)
class MediaCandidate:
    """A lightweight identity whose fragile metadata is resolved per item."""

    identity: MediaIdentity
    kind: MediaKind
    session_configured: bool
    resolve: ResolveAction = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ResolvedMedia:
    """Complete normalized metadata and the adapter-specific download action."""

    identity: MediaIdentity
    kind: MediaKind
    instagram_media_id: str
    shortcode: str | None
    profile_id: str
    instagram_user_id: str
    owner_username: str
    caption: str
    accessibility_caption: str
    published_at: datetime
    story_expires_at: datetime | None
    original_url: str
    content_kinds: tuple[ContentKind, ...]
    download: DownloadAction = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class MediaProcessResult:
    """The persisted media and whether processing downloaded new output."""

    status: Literal["saved", "existing"]
    media: MediaSnapshot
