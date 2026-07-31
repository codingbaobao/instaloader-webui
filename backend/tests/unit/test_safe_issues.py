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
    issue = SafeMediaIssue(
        identity=MediaIdentity(
            "shortcode",
            "DOqEJyxCRGJ?igsh=identity-secret#fragment",
        ),
        kind="reel\nCookie: cookie-secret",
        error_code="instagram_unavailable",
        safe_message="Instagram could not be reached. Try again later.",
        exception_class_chain=(
            "BadResponseException",
            "ConnectionException",
            "sessionid=chain-secret",
        ),
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
