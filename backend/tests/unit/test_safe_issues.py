import logging
from itertools import pairwise

import pytest
from instaloader import (
    BadResponseException,
    ConnectionException,
    LoginRequiredException,
    QueryReturnedNotFoundException,
    TooManyRequestsException,
    TwoFactorAuthRequiredException,
)

from instaloader_webui.db.library_repositories import MediaIdentity
from instaloader_webui.instagram.errors import (
    ANONYMOUS_REJECTED,
    CHALLENGE,
    MEDIA_NOT_FOUND,
    RATE_LIMITED,
    SESSION_REJECTED,
    TRANSIENT,
    classify_instaloader_error,
)
from instaloader_webui.instagram.safe_issues import (
    MediaItemFailure,
    SafeMediaIssue,
    classify_media_issue,
    log_media_issue,
)


def _identity() -> MediaIdentity:
    return MediaIdentity("shortcode", "DOqEJyxCRGJ")


def _valid_issue(
    *,
    identity: MediaIdentity | None = None,
    kind: str = "reel",
    error_code: str = "instagram_unavailable",
    safe_message: str = TRANSIENT,
    exception_class_chain: tuple[str, ...] = ("ConnectionException",),
) -> SafeMediaIssue:
    return SafeMediaIssue(
        identity=identity or _identity(),
        kind=kind,
        error_code=error_code,  # type: ignore[arg-type]
        safe_message=safe_message,
        exception_class_chain=exception_class_chain,
    )


@pytest.mark.parametrize(
    ("identity", "kind"),
    [
        (
            MediaIdentity("shortcode", "DOqEJyxCRGJ?igsh=identity-secret"),
            "reel",
        ),
        (MediaIdentity("story_media_id", "123?Cookie=story-secret"), "story"),
        (MediaIdentity("cookie_type", "type-secret"), "reel"),
        (MediaIdentity("shortcode", "DOqEJyxCRGJ"), "reel Cookie: kind-secret"),
    ],
)
def test_direct_issue_construction_rejects_secret_bearing_identity_or_kind(
    identity: MediaIdentity,
    kind: str,
) -> None:
    """Allowing unsafe issue fields would expose their values through repr/API."""
    with pytest.raises(ValueError) as raised:
        _valid_issue(identity=identity, kind=kind)

    assert str(raised.value) == "Invalid safe media issue."
    assert "secret" not in str(raised.value)


@pytest.mark.parametrize(
    ("error_code", "safe_message"),
    [
        ("made_up_code", TRANSIENT),
        ("instagram_unavailable", "Cookie: message-secret"),
        ("instagram_unavailable", CHALLENGE),
    ],
)
def test_direct_issue_construction_rejects_non_approved_code_or_message(
    error_code: str,
    safe_message: str,
) -> None:
    """A caller must not persist an arbitrary code or message through an issue."""
    with pytest.raises(ValueError) as raised:
        _valid_issue(error_code=error_code, safe_message=safe_message)

    assert str(raised.value) == "Invalid safe media issue."
    assert "secret" not in str(raised.value)


@pytest.mark.parametrize(
    ("identity", "kind"),
    [
        (MediaIdentity("shortcode", "DOqEJyxCRGJ?igsh=secret"), "reel"),
        (MediaIdentity("shortcode", "DOqEJyxCRGJ"), "reel Cookie: secret"),
    ],
)
def test_classification_rejects_unsafe_issue_fields_before_returning_an_issue(
    identity: MediaIdentity,
    kind: str,
) -> None:
    """Returning an issue with a query or Cookie would make repr unsafe."""
    with pytest.raises(ValueError) as raised:
        classify_media_issue(
            ConnectionException("unavailable"),
            session_configured=True,
            target="media",
            identity=identity,
            kind=kind,
        )

    assert str(raised.value) == "Invalid safe media issue."
    assert "secret" not in str(raised.value)


def test_direct_issue_construction_keeps_valid_story_identity() -> None:
    """Rejecting valid Story identities would break safe Story warning persistence."""
    issue = _valid_issue(
        identity=MediaIdentity("story_media_id", "3952742051065980676"),
        kind="story",
    )

    assert issue.identity == MediaIdentity("story_media_id", "3952742051065980676")


def test_issue_keeps_class_names_but_not_exception_message() -> None:
    """Removing raw exception text must make this test fail."""
    cause = ConnectionException(
        "https://instagram.com/p/x/?igsh=secret Cookie: sessionid=secret"
    )
    error = BadResponseException("wrapper")
    error.__cause__ = cause

    issue = classify_media_issue(
        error,
        session_configured=True,
        target="media",
        identity=_identity(),
        kind="reel",
    )

    assert issue.error_code == "instagram_unavailable"
    assert issue.safe_message == TRANSIENT
    assert issue.exception_class_chain == (
        "BadResponseException",
        "ConnectionException",
    )
    assert "secret" not in repr(issue)
    assert "igsh" not in repr(issue)


@pytest.mark.parametrize(
    ("error", "session_configured", "error_code", "safe_message"),
    [
        (TwoFactorAuthRequiredException("challenge"), True, "challenge_required", CHALLENGE),
        (
            TooManyRequestsException("429"),
            True,
            "instagram_rate_limited",
            RATE_LIMITED,
        ),
        (
            LoginRequiredException("login required"),
            True,
            "instagram_session_rejected",
            SESSION_REJECTED,
        ),
        (
            LoginRequiredException("login required"),
            False,
            "instagram_access_denied",
            ANONYMOUS_REJECTED,
        ),
        (
            QueryReturnedNotFoundException("not found"),
            True,
            "instagram_not_found",
            MEDIA_NOT_FOUND,
        ),
    ],
)
def test_classification_returns_stable_code_and_fixed_message(
    error: BaseException,
    session_configured: bool,
    error_code: str,
    safe_message: str,
) -> None:
    """Changing any upstream-error branch must change this public result."""
    issue = classify_media_issue(
        error,
        session_configured=session_configured,
        target="media",
        identity=_identity(),
        kind="reel",
    )

    assert issue.error_code == error_code
    assert issue.safe_message == safe_message


def test_issue_chain_prefers_cause_then_context_and_stays_bounded() -> None:
    """Changing exception traversal order, cycle handling, or bounds must fail."""
    root = RuntimeError("root")
    cause = ValueError("cause")
    context = LookupError("context")
    root.__cause__ = cause
    root.__context__ = context
    cause.__context__ = root
    context.__cause__ = RuntimeError("nested")

    issue = classify_media_issue(
        root,
        session_configured=True,
        target="media",
        identity=_identity(),
        kind="post",
    )

    assert issue.exception_class_chain == (
        "RuntimeError",
        "ValueError",
        "LookupError",
        "RuntimeError",
    )


def test_issue_chain_deduplicates_instances_and_limits_class_name_length() -> None:
    """Recording duplicate objects or overlong class names must fail this test."""
    long_name_error = type("x" * 200, (Exception,), {})("raw secret")
    root = RuntimeError("root")
    root.__cause__ = long_name_error
    long_name_error.__context__ = root

    issue = classify_media_issue(
        root,
        session_configured=True,
        target="media",
        identity=_identity(),
        kind="post",
    )

    assert issue.exception_class_chain == ("RuntimeError", "x" * 128)


def test_issue_chain_stops_after_eight_class_names() -> None:
    """Increasing the persisted class-chain limit must fail this test."""
    errors = [RuntimeError(str(index)) for index in range(9)]
    for current, cause in pairwise(errors):
        current.__cause__ = cause

    issue = classify_media_issue(
        errors[0],
        session_configured=True,
        target="media",
        identity=_identity(),
        kind="post",
    )

    assert issue.exception_class_chain == ("RuntimeError",) * 8


def test_media_item_failure_exposes_only_the_safe_issue() -> None:
    """Using a raw exception message for the item failure must fail this test."""
    issue = SafeMediaIssue(
        identity=_identity(),
        kind="story",
        error_code="asset_validation_failed",
        safe_message="Downloaded media files could not be validated.",
        exception_class_chain=("ValueError",),
    )

    failure = MediaItemFailure(issue)

    assert failure.issue is issue
    assert str(failure) == "Downloaded media files could not be validated."


def test_compatibility_wrapper_returns_only_safe_message() -> None:
    """Returning structured data from the legacy classifier must fail this test."""
    assert (
        classify_instaloader_error(
            ConnectionException("https://instagram.com/?igsh=secret"),
            session_configured=True,
            target="media",
        )
        == TRANSIENT
    )


def test_log_media_issue_redacts_every_field_and_emits_one_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Logging raw identifiers, queries, or credential assignments must fail."""
    issue = _valid_issue(
        exception_class_chain=("BadResponseException", "ConnectionException"),
    )
    object.__setattr__(
        issue,
        "identity",
        MediaIdentity("shortcode", "DOqEJyxCRGJ?igsh=identity-secret#fragment"),
    )
    object.__setattr__(issue, "kind", "reel\nCookie: cookie-secret")
    object.__setattr__(
        issue,
        "exception_class_chain",
        ("BadResponseException", "ConnectionException", "sessionid=chain-secret"),
    )
    logger = logging.getLogger("test.safe_issues")

    with caplog.at_level(logging.WARNING, logger="test.safe_issues"):
        log_media_issue(
            logger,
            job_id="job-1?csrftoken=job-secret",
            issue=issue,
        )

    [record] = caplog.records
    message = record.getMessage()
    assert record.levelno == logging.WARNING
    assert "job-1" in message
    assert "DOqEJyxCRGJ" in message
    assert "instagram_unavailable" in message
    assert "reel" in message
    assert "BadResponseException,ConnectionException,[redacted]" in message
    assert "\n" not in message
    for forbidden in (
        "Cookie",
        "cookie-secret",
        "sessionid",
        "chain-secret",
        "csrftoken",
        "job-secret",
        "identity-secret",
        "igsh=",
        "?",
        "#fragment",
    ):
        assert forbidden not in message
