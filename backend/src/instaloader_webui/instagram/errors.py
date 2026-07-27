"""Convert Instagram failures into concise, non-disclosing messages."""

from __future__ import annotations

import re
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

_CHALLENGE_MARKERS = ("challenge", "checkpoint", "account verification")
_RATE_LIMIT_MARKERS = ("rate limit", "too many requests", "rate_limited")
_LOGIN_MARKERS = (
    "login required",
    "log in required",
    "not logged in",
    "authentication required",
    "unauthorized",
    "forbidden",
)
_NOT_FOUND_MARKERS = (
    "not found",
    "does not exist",
    "no profile found",
    "private",
    "inaccessible",
)
_TRANSIENT_MARKERS = (
    "connection",
    "network",
    "timeout",
    "temporarily unavailable",
    "service unavailable",
)
_CHALLENGE_CLASSES = frozenset({"TwoFactorAuthRequiredException"})
_RATE_LIMIT_CLASSES = frozenset({"TooManyRequestsException"})
_LOGIN_CLASSES = frozenset(
    {
        "BadCredentialsException",
        "LoginException",
        "LoginRequiredException",
        "QueryReturnedForbiddenException",
        "QueryReturnedUnauthorizedException",
    }
)
_NOT_FOUND_CLASSES = frozenset(
    {
        "PrivateProfileNotFollowedException",
        "ProfileNotExistsException",
        "QueryReturnedNotFoundException",
    }
)
_TRANSIENT_CLASSES = frozenset(
    {
        "BadResponseException",
        "ConnectionError",
        "ConnectionException",
        "RequestException",
        "Timeout",
        "TimeoutError",
    }
)
_HTTP_REJECTION = re.compile(r"(?<!\d)(?:401|403)(?!\d)")
_HTTP_RATE_LIMIT = re.compile(r"(?<!\d)429(?!\d)")


def classify_instaloader_error(
    error: BaseException,
    *,
    session_configured: bool,
    target: Literal["media", "profile"],
) -> str:
    """Return a fixed message for an upstream Instagram exception chain."""
    details = tuple(_exception_details(error))
    class_names = {class_name for class_name, _message in details}
    messages = tuple(message for _class_name, message in details)

    if _matches(class_names, messages, _CHALLENGE_CLASSES, _CHALLENGE_MARKERS):
        return CHALLENGE
    if _matches(
        class_names,
        messages,
        _RATE_LIMIT_CLASSES,
        _RATE_LIMIT_MARKERS,
        _HTTP_RATE_LIMIT,
    ):
        return RATE_LIMITED
    if _matches(
        class_names,
        messages,
        _LOGIN_CLASSES,
        _LOGIN_MARKERS,
        _HTTP_REJECTION,
    ):
        return SESSION_REJECTED if session_configured else ANONYMOUS_REJECTED
    if _matches(class_names, messages, _NOT_FOUND_CLASSES, _NOT_FOUND_MARKERS):
        return PROFILE_NOT_FOUND if target == "profile" else MEDIA_NOT_FOUND
    if _matches(class_names, messages, _TRANSIENT_CLASSES, _TRANSIENT_MARKERS):
        return TRANSIENT
    return TRANSIENT


def _exception_details(error: BaseException) -> tuple[tuple[str, str], ...]:
    seen: set[int] = set()
    pending: list[BaseException] = [error]
    details: list[tuple[str, str]] = []
    while pending:
        current = pending.pop()
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)
        details.append((_class_name(current), _message(current)))
        for related in (_related_exception(current, "__cause__"), _related_exception(current, "__context__")):
            if related is not None:
                pending.append(related)
    return tuple(details)


def _related_exception(error: BaseException, attribute: str) -> BaseException | None:
    try:
        related = getattr(error, attribute, None)
    except Exception:
        return None
    return related if isinstance(related, BaseException) else None


def _class_name(error: BaseException) -> str:
    try:
        return error.__class__.__name__
    except Exception:
        return ""


def _message(error: BaseException) -> str:
    try:
        return str(error).casefold()
    except Exception:
        return ""


def _matches(
    class_names: set[str],
    messages: tuple[str, ...],
    exception_names: frozenset[str],
    markers: tuple[str, ...],
    status_code: re.Pattern[str] | None = None,
) -> bool:
    return (
        bool(class_names & exception_names)
        or any(marker in message for message in messages for marker in markers)
        or (
            status_code is not None
            and any(status_code.search(message) is not None for message in messages)
        )
    )
