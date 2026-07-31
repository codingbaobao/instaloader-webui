"""Compatibility messages for safe Instagram failure classification."""

from __future__ import annotations

from typing import Literal

RATE_LIMITED = "Instagram rate limited this server. Try again later."
CHALLENGE = (
    "Instagram requires account verification. Refresh the imported Cookie after "
    "resolving the challenge in a browser."
)
SESSION_REJECTED = (
    "The imported Instagram session expired or was rejected. Import a fresh Cookie file."
)
ANONYMOUS_REJECTED = (
    "Instagram denied anonymous access. Import an Instagram Cookie file in Settings."
)
PROFILE_NOT_FOUND = "This Instagram profile was not found or is inaccessible."
MEDIA_NOT_FOUND = "This Instagram media item was not found or is inaccessible."
TRANSIENT = "Instagram could not be reached. Try again later."


def classify_instaloader_error(
    error: BaseException,
    *,
    session_configured: bool,
    target: Literal["media", "profile"],
) -> str:
    """Return the legacy fixed message for an upstream Instagram exception."""
    from instaloader_webui.db.library_repositories import MediaIdentity
    from instaloader_webui.instagram.safe_issues import classify_media_issue

    return classify_media_issue(
        error,
        session_configured=session_configured,
        target=target,
        identity=MediaIdentity("shortcode", "legacy"),
        kind="media",
    ).safe_message
