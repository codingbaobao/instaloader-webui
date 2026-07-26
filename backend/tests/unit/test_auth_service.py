from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from instaloader_webui.auth.passwords import PasswordService
from instaloader_webui.auth.session_tokens import digest_session_token
from instaloader_webui.auth.throttle import LoginThrottle
from instaloader_webui.db.migrations import run_migrations
from instaloader_webui.db.repositories import (
    AdminRepository,
    LoginFailureRepository,
    WebSessionRepository,
)
from instaloader_webui.services.admin_bootstrap import bootstrap_admin
from instaloader_webui.services.auth_service import AuthService


def build_auth_service(session_factory, test_settings) -> AuthService:
    run_migrations(test_settings)
    passwords = PasswordService()
    bootstrap_admin(session_factory, test_settings, passwords)
    return AuthService(
        administrators=AdminRepository(session_factory),
        sessions=WebSessionRepository(session_factory),
        throttle=LoginThrottle(
            repository=LoginFailureRepository(session_factory),
            hmac_secret=test_settings.app_secret_key.get_secret_value(),
        ),
        passwords=passwords,
    )


def test_login_creates_seven_day_session_without_exposing_token_in_repr(
    session_factory, test_settings
) -> None:
    service = build_auth_service(session_factory, test_settings)
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)

    result = service.login(
        "owner",
        "correct-horse-battery-staple",
        "203.0.113.7",
        now,
    )

    assert result.expires_at == now + timedelta(days=7)
    assert result.raw_token not in repr(result)


def test_session_last_seen_refresh_is_bounded(
    session_factory, test_settings
) -> None:
    service = build_auth_service(session_factory, test_settings)
    sessions = WebSessionRepository(session_factory)
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
    result = service.login(
        "owner",
        "correct-horse-battery-staple",
        "203.0.113.7",
        now,
    )
    digest = digest_session_token(result.raw_token)

    assert service.authenticate_session(result.raw_token, now + timedelta(minutes=1))
    before_cadence = sessions.get_by_token_digest(digest)
    assert before_cadence is not None
    assert before_cadence.last_seen_at == now

    assert service.authenticate_session(result.raw_token, now + timedelta(minutes=5))
    at_cadence = sessions.get_by_token_digest(digest)
    assert at_cadence is not None
    assert at_cadence.last_seen_at == now + timedelta(minutes=5)


def test_session_revoked_during_last_seen_refresh_is_rejected(
    monkeypatch, session_factory, test_settings
) -> None:
    run_migrations(test_settings)
    passwords = PasswordService()
    bootstrap_admin(session_factory, test_settings, passwords)
    sessions = WebSessionRepository(session_factory)
    service = AuthService(
        administrators=AdminRepository(session_factory),
        sessions=sessions,
        throttle=LoginThrottle(
            repository=LoginFailureRepository(session_factory),
            hmac_secret=test_settings.app_secret_key.get_secret_value(),
        ),
        passwords=passwords,
    )
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
    result = service.login(
        "owner",
        "correct-horse-battery-staple",
        "203.0.113.7",
        now,
    )
    original_touch = sessions.touch

    def revoke_then_touch(*, token_digest: str, now: datetime):
        sessions.revoke(token_digest=token_digest, now=now)
        return original_touch(token_digest=token_digest, now=now)

    monkeypatch.setattr(sessions, "touch", revoke_then_touch)

    assert (
        service.authenticate_session(result.raw_token, now + timedelta(minutes=5))
        is None
    )


def test_password_change_rolls_back_if_other_session_revocation_fails(
    engine, session_factory, test_settings
) -> None:
    service = build_auth_service(session_factory, test_settings)
    passwords = PasswordService()
    administrators = AdminRepository(session_factory)
    sessions = WebSessionRepository(session_factory)
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
    retained = service.login(
        "owner",
        "correct-horse-battery-staple",
        "203.0.113.7",
        now,
    )
    other = service.login(
        "owner",
        "correct-horse-battery-staple",
        "203.0.113.8",
        now,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TRIGGER reject_session_revocation
                BEFORE UPDATE OF revoked_at ON web_sessions
                WHEN NEW.revoked_at IS NOT NULL
                BEGIN
                    SELECT RAISE(ABORT, 'forced revocation failure');
                END
                """
            )
        )

    with pytest.raises(IntegrityError):
        service.change_password(
            retained.raw_token,
            "correct-horse-battery-staple",
            "different-long-owner-password",
            now + timedelta(minutes=1),
        )

    administrator = administrators.get_single()
    other_session = sessions.get_by_token_digest(
        digest_session_token(other.raw_token)
    )
    assert administrator is not None
    assert passwords.verify(
        administrator.password_hash, "correct-horse-battery-staple"
    )
    assert other_session is not None
    assert other_session.revoked_at is None
