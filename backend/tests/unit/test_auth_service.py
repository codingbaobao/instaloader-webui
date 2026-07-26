from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event

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
from instaloader_webui.services.auth_service import (
    AuthService,
    InvalidCredentialsError,
    LoginThrottledError,
)


def make_auth_service(
    session_factory, test_settings, passwords: PasswordService
) -> AuthService:
    return AuthService(
        administrators=AdminRepository(session_factory),
        sessions=WebSessionRepository(session_factory),
        throttle=LoginThrottle(
            repository=LoginFailureRepository(session_factory),
            hmac_secret=test_settings.app_secret_key.get_secret_value(),
        ),
        passwords=passwords,
    )


def build_auth_service(session_factory, test_settings) -> AuthService:
    run_migrations(test_settings)
    passwords = PasswordService()
    bootstrap_admin(session_factory, test_settings, passwords)
    return make_auth_service(session_factory, test_settings, passwords)


class PausingPasswordService(PasswordService):
    def __init__(self, verified: Event, release: Event) -> None:
        super().__init__()
        self._verified = verified
        self._release = release

    def verify(self, hash_value: str, password: str) -> bool:
        matches = super().verify(hash_value, password)
        if matches:
            self._verified.set()
            assert self._release.wait(timeout=5)
        return matches


class BarrierPasswordService(PasswordService):
    def __init__(self, barrier: Barrier) -> None:
        super().__init__()
        self._barrier = barrier

    def verify(self, hash_value: str, password: str) -> bool:
        matches = super().verify(hash_value, password)
        if matches:
            self._barrier.wait(timeout=5)
        return matches


class PausingFailedPasswordService(PasswordService):
    def __init__(self, failures_verified: Barrier, release: Event) -> None:
        super().__init__()
        self._failures_verified = failures_verified
        self._release = release

    def verify(self, hash_value: str, password: str) -> bool:
        matches = super().verify(hash_value, password)
        if not matches:
            self._failures_verified.wait(timeout=5)
            assert self._release.wait(timeout=5)
        return matches


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
    original_touch = sessions.get_active_and_touch

    def revoke_then_touch(
        *, token_digest: str, now: datetime, cadence: timedelta
    ):
        sessions.revoke(token_digest=token_digest, now=now)
        return original_touch(
            token_digest=token_digest,
            now=now,
            cadence=cadence,
        )

    monkeypatch.setattr(sessions, "get_active_and_touch", revoke_then_touch)

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


def test_login_verified_before_password_change_cannot_create_session_afterward(
    session_factory, test_settings
) -> None:
    run_migrations(test_settings)
    setup_passwords = PasswordService()
    bootstrap_admin(session_factory, test_settings, setup_passwords)
    setup_service = make_auth_service(
        session_factory, test_settings, setup_passwords
    )
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
    retained = setup_service.login(
        "owner",
        "correct-horse-battery-staple",
        "203.0.113.7",
        now,
    )
    login_verified = Event()
    release_login = Event()
    racing_login = make_auth_service(
        session_factory,
        test_settings,
        PausingPasswordService(login_verified, release_login),
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            racing_login.login,
            "owner",
            "correct-horse-battery-staple",
            "203.0.113.8",
            now + timedelta(minutes=1),
        )
        assert login_verified.wait(timeout=5)
        changed = setup_service.change_password(
            retained.raw_token,
            "correct-horse-battery-staple",
            "different-long-owner-password",
            now + timedelta(minutes=1),
        )
        release_login.set()
        with pytest.raises(InvalidCredentialsError):
            future.result(timeout=5)

    assert changed.must_change_password is False


def test_success_invalidates_in_flight_failures_from_the_same_bucket(
    session_factory, test_settings
) -> None:
    service = build_auth_service(session_factory, test_settings)
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
    failures_verified = Barrier(5)
    release_failures = Event()
    racing_failures = make_auth_service(
        session_factory,
        test_settings,
        PausingFailedPasswordService(failures_verified, release_failures),
    )

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(
                racing_failures.login,
                "owner",
                "wrong-password-value",
                "203.0.113.7",
                now,
            )
            for _ in range(4)
        ]
        failures_verified.wait(timeout=5)
        successful = service.login(
            "owner",
            "correct-horse-battery-staple",
            "203.0.113.7",
            now,
        )
        release_failures.set()
        for future in futures:
            with pytest.raises(InvalidCredentialsError):
                future.result(timeout=5)

    assert successful.raw_token
    for offset in range(5):
        with pytest.raises(InvalidCredentialsError):
            service.login(
                "owner",
                "wrong-password-value",
                "203.0.113.7",
                now + timedelta(seconds=offset + 1),
            )
    with pytest.raises(LoginThrottledError):
        service.login(
            "owner",
            "wrong-password-value",
            "203.0.113.7",
            now + timedelta(seconds=6),
        )


def test_concurrent_password_changes_allow_exactly_one_winner(
    session_factory, test_settings
) -> None:
    setup_service = build_auth_service(session_factory, test_settings)
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
    first_session = setup_service.login(
        "owner",
        "correct-horse-battery-staple",
        "203.0.113.7",
        now,
    )
    second_session = setup_service.login(
        "owner",
        "correct-horse-battery-staple",
        "203.0.113.8",
        now,
    )
    password_barrier = Barrier(2)
    first_service = make_auth_service(
        session_factory, test_settings, BarrierPasswordService(password_barrier)
    )
    second_service = make_auth_service(
        session_factory, test_settings, BarrierPasswordService(password_barrier)
    )
    replacements = (
        "first-different-owner-password",
        "second-different-owner-password",
    )

    def attempt_change(
        service: AuthService, raw_token: str, new_password: str
    ) -> tuple[str, str]:
        try:
            service.change_password(
                raw_token,
                "correct-horse-battery-staple",
                new_password,
                now + timedelta(minutes=1),
            )
        except InvalidCredentialsError:
            return ("rejected", new_password)
        return ("changed", new_password)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(
            future.result(timeout=10)
            for future in (
                executor.submit(
                    attempt_change,
                    first_service,
                    first_session.raw_token,
                    replacements[0],
                ),
                executor.submit(
                    attempt_change,
                    second_service,
                    second_session.raw_token,
                    replacements[1],
                ),
            )
        )

    assert [status for status, _password in outcomes].count("changed") == 1
    winning_password = next(
        password for status, password in outcomes if status == "changed"
    )
    administrator = AdminRepository(session_factory).get_single()
    assert administrator is not None
    assert PasswordService().verify(administrator.password_hash, winning_password)


def test_password_change_revalidates_retained_session_inside_write_transaction(
    monkeypatch, session_factory, test_settings
) -> None:
    run_migrations(test_settings)
    passwords = PasswordService()
    bootstrap_admin(session_factory, test_settings, passwords)
    administrators = AdminRepository(session_factory)
    sessions = WebSessionRepository(session_factory)
    service = AuthService(
        administrators=administrators,
        sessions=sessions,
        throttle=LoginThrottle(
            repository=LoginFailureRepository(session_factory),
            hmac_secret=test_settings.app_secret_key.get_secret_value(),
        ),
        passwords=passwords,
    )
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
    retained = service.login(
        "owner",
        "correct-horse-battery-staple",
        "203.0.113.7",
        now,
    )
    write_ready = Event()
    allow_write = Event()
    original_change = administrators.change_password_and_revoke_other_sessions

    def pause_before_write(**kwargs):
        write_ready.set()
        assert allow_write.wait(timeout=5)
        return original_change(**kwargs)

    monkeypatch.setattr(
        administrators,
        "change_password_and_revoke_other_sessions",
        pause_before_write,
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            service.change_password,
            retained.raw_token,
            "correct-horse-battery-staple",
            "different-long-owner-password",
            now + timedelta(minutes=1),
        )
        assert write_ready.wait(timeout=5)
        sessions.revoke(
            token_digest=digest_session_token(retained.raw_token),
            now=now + timedelta(minutes=1),
        )
        allow_write.set()
        with pytest.raises(InvalidCredentialsError):
            future.result(timeout=5)

    administrator = administrators.get_single()
    assert administrator is not None
    assert passwords.verify(
        administrator.password_hash,
        "correct-horse-battery-staple",
    )
