"""Create non-disclosing media issues from upstream Instagram failures."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

from instaloader_webui.db.library_repositories import MediaIdentity
from instaloader_webui.instagram.errors import (
    ANONYMOUS_REJECTED,
    CHALLENGE,
    MEDIA_NOT_FOUND,
    PROFILE_NOT_FOUND,
    RATE_LIMITED,
    SESSION_REJECTED,
    TRANSIENT,
)

IssueCode = Literal[
    "challenge_required",
    "instagram_rate_limited",
    "instagram_session_rejected",
    "instagram_access_denied",
    "instagram_not_found",
    "instagram_unavailable",
    "asset_validation_failed",
]

ASSET_VALIDATION_FAILED = "Downloaded media files could not be validated."

_MAX_EXCEPTION_CLASS_CHAIN_LENGTH = 8
_MAX_EXCEPTION_CLASS_NAME_LENGTH = 128
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
_URL_QUERY_OR_FRAGMENT = re.compile(r"([^\s?#]+)[?#][^\s]*")
_SENSITIVE_VALUE = re.compile(
    r"\b(?:cookie|sessionid|csrftoken)\b(?:\s*(?:=|:)\s*[^\s,;]+)?",
    flags=re.IGNORECASE,
)
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class SafeMediaIssue:
    """The complete, safe-to-persist description of one failed media item."""

    identity: MediaIdentity
    kind: str
    error_code: IssueCode
    safe_message: str
    exception_class_chain: tuple[str, ...]


class MediaItemFailure(RuntimeError):
    """Signal an item-local failure without retaining its raw upstream error."""

    def __init__(self, issue: SafeMediaIssue) -> None:
        super().__init__(issue.safe_message)
        self.issue = issue


def classify_media_issue(
    error: BaseException,
    *,
    session_configured: bool,
    target: Literal["media", "profile"],
    identity: MediaIdentity,
    kind: str,
) -> SafeMediaIssue:
    """Classify an upstream exception without exposing its string value."""
    error_code, safe_message = _classify_error(
        error,
        session_configured=session_configured,
        target=target,
    )
    return SafeMediaIssue(
        identity=identity,
        kind=kind,
        error_code=error_code,
        safe_message=safe_message,
        exception_class_chain=_exception_class_chain(error),
    )


def log_media_issue(
    logger: logging.Logger,
    *,
    job_id: str,
    issue: SafeMediaIssue,
) -> None:
    """Emit one controlled warning without an exception object or traceback."""
    logger.warning(
        "instagram_media_issue job_id=%s identity_type=%s identity_value=%s "
        "kind=%s error_code=%s safe_message=%s exception_classes=%s",
        _sanitize_log_field(job_id),
        _sanitize_log_field(issue.identity.identity_type),
        _sanitize_log_field(issue.identity.value),
        _sanitize_log_field(issue.kind),
        _sanitize_log_field(issue.error_code),
        _sanitize_log_field(issue.safe_message),
        _sanitize_log_field(",".join(issue.exception_class_chain)),
    )


def _classify_error(
    error: BaseException,
    *,
    session_configured: bool,
    target: Literal["media", "profile"],
) -> tuple[IssueCode, str]:
    details = tuple(_exception_details(error))
    class_names = {class_name for class_name, _message in details}
    messages = tuple(message for _class_name, message in details)

    if _matches(class_names, messages, _CHALLENGE_CLASSES, _CHALLENGE_MARKERS):
        return "challenge_required", CHALLENGE
    if _matches(
        class_names,
        messages,
        _RATE_LIMIT_CLASSES,
        _RATE_LIMIT_MARKERS,
        _HTTP_RATE_LIMIT,
    ):
        return "instagram_rate_limited", RATE_LIMITED
    if _matches(
        class_names,
        messages,
        _LOGIN_CLASSES,
        _LOGIN_MARKERS,
        _HTTP_REJECTION,
    ):
        return (
            ("instagram_session_rejected", SESSION_REJECTED)
            if session_configured
            else ("instagram_access_denied", ANONYMOUS_REJECTED)
        )
    if _matches(class_names, messages, _NOT_FOUND_CLASSES, _NOT_FOUND_MARKERS):
        return (
            "instagram_not_found",
            PROFILE_NOT_FOUND if target == "profile" else MEDIA_NOT_FOUND,
        )
    return "instagram_unavailable", TRANSIENT


def _exception_class_chain(error: BaseException) -> tuple[str, ...]:
    seen: set[int] = set()
    pending: list[BaseException] = [error]
    class_names: list[str] = []
    while pending and len(class_names) < _MAX_EXCEPTION_CLASS_CHAIN_LENGTH:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        class_names.append(_class_name(current))
        context = _related_exception(current, "__context__")
        cause = _related_exception(current, "__cause__")
        if context is not None:
            pending.append(context)
        if cause is not None:
            pending.append(cause)
    return tuple(class_names)


def _exception_details(error: BaseException) -> tuple[tuple[str, str], ...]:
    seen: set[int] = set()
    pending: list[BaseException] = [error]
    details: list[tuple[str, str]] = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        details.append((_class_name(current), _message(current)))
        context = _related_exception(current, "__context__")
        cause = _related_exception(current, "__cause__")
        if context is not None:
            pending.append(context)
        if cause is not None:
            pending.append(cause)
    return tuple(details)


def _related_exception(error: BaseException, attribute: str) -> BaseException | None:
    try:
        related = getattr(error, attribute, None)
    except Exception:  # noqa: BLE001 - malformed upstream exceptions are ignored.
        return None
    return related if isinstance(related, BaseException) else None


def _class_name(error: BaseException) -> str:
    try:
        name = error.__class__.__name__
    except Exception:  # noqa: BLE001 - malformed upstream exceptions are ignored.
        return "UnknownException"
    return name[:_MAX_EXCEPTION_CLASS_NAME_LENGTH] or "UnknownException"


def _message(error: BaseException) -> str:
    try:
        return str(error).casefold()
    except Exception:  # noqa: BLE001 - malformed upstream exceptions are ignored.
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


def _sanitize_log_field(value: object) -> str:
    text = str(value)
    text = _URL_QUERY_OR_FRAGMENT.sub(r"\1", text)
    text = _SENSITIVE_VALUE.sub("[redacted]", text)
    return _WHITESPACE.sub(" ", text).strip()
