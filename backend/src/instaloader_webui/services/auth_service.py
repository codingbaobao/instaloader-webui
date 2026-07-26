import hmac
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from instaloader_webui.auth.passwords import PasswordService
from instaloader_webui.auth.session_tokens import (
    digest_session_token,
    issue_session_token,
)
from instaloader_webui.auth.throttle import LoginAttemptKey, LoginThrottle
from instaloader_webui.config import MINIMUM_ADMIN_PASSWORD_LENGTH
from instaloader_webui.db.repositories import (
    AdminRepository,
    WebSessionRepository,
)

SESSION_LIFETIME = timedelta(days=7)
SESSION_TOUCH_INTERVAL = timedelta(minutes=5)


class InvalidCredentialsError(Exception):
    pass


class InvalidCurrentPasswordError(Exception):
    pass


class InvalidNewPasswordError(Exception):
    pass


class PasswordUnchangedError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class LoginThrottledError(Exception):
    retry_after_seconds: int


@dataclass(frozen=True, slots=True)
class LoginResult:
    administrator_id: str
    username: str
    must_change_password: bool
    expires_at: datetime
    raw_token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    administrator_id: str
    username: str
    must_change_password: bool
    expires_at: datetime
    raw_token: str | None = None


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class AuthService:
    def __init__(
        self,
        *,
        administrators: AdminRepository,
        sessions: WebSessionRepository,
        throttle: LoginThrottle,
        passwords: PasswordService,
    ) -> None:
        self._administrators = administrators
        self._sessions = sessions
        self._throttle = throttle
        self._passwords = passwords

    def login(
        self, username: str, password: str, client_ip: str, now: datetime
    ) -> LoginResult:
        current_time = _as_utc(now)
        attempt = LoginAttemptKey(username=username, client_ip=client_ip)
        decision = self._throttle.check(attempt, current_time)
        if not decision.allowed:
            raise LoginThrottledError(decision.retry_after_seconds)

        administrator = self._administrators.get_single()
        username_matches = (
            administrator is not None
            and hmac.compare_digest(
                username.strip().casefold().encode("utf-8"),
                administrator.username.casefold().encode("utf-8"),
            )
        )
        password_matches = (
            administrator is not None
            and self._passwords.verify(administrator.password_hash, password)
        )
        if not username_matches or not password_matches or administrator is None:
            self._throttle.record_failure(attempt, current_time)
            raise InvalidCredentialsError

        self._throttle.record_success(attempt)
        issued = issue_session_token()
        expires_at = current_time + SESSION_LIFETIME
        self._sessions.create(
            admin_user_id=administrator.id,
            token_digest=issued.digest,
            now=current_time,
            expires_at=expires_at,
        )
        return LoginResult(
            administrator_id=administrator.id,
            username=administrator.username,
            must_change_password=administrator.must_change_password,
            expires_at=expires_at,
            raw_token=issued.raw,
        )

    def authenticate_session(
        self, raw_token: str, now: datetime
    ) -> AuthenticatedSession | None:
        current_time = _as_utc(now)
        token_digest = digest_session_token(raw_token)
        session = self._sessions.get_by_token_digest(token_digest)
        if (
            session is None
            or session.revoked_at is not None
            or session.expires_at <= current_time
        ):
            return None

        administrator = self._administrators.get_by_id(session.admin_user_id)
        if administrator is None:
            return None

        if current_time - session.last_seen_at >= SESSION_TOUCH_INTERVAL:
            refreshed = self._sessions.touch(
                token_digest=token_digest, now=current_time
            )
            if (
                refreshed is None
                or refreshed.revoked_at is not None
                or refreshed.expires_at <= current_time
            ):
                return None
            session = refreshed

        return AuthenticatedSession(
            administrator_id=administrator.id,
            username=administrator.username,
            must_change_password=administrator.must_change_password,
            expires_at=session.expires_at,
        )

    def change_password(
        self,
        raw_token: str,
        current_password: str,
        new_password: str,
        now: datetime,
    ) -> AuthenticatedSession:
        current_time = _as_utc(now)
        authenticated = self.authenticate_session(raw_token, current_time)
        if authenticated is None:
            raise InvalidCredentialsError

        administrator = self._administrators.get_by_id(
            authenticated.administrator_id
        )
        if administrator is None:
            raise InvalidCredentialsError
        if not self._passwords.verify(administrator.password_hash, current_password):
            raise InvalidCurrentPasswordError
        if len(new_password) < MINIMUM_ADMIN_PASSWORD_LENGTH:
            raise InvalidNewPasswordError
        if hmac.compare_digest(
            current_password.encode("utf-8"), new_password.encode("utf-8")
        ):
            raise PasswordUnchangedError

        updated = self._administrators.change_password_and_revoke_other_sessions(
            administrator_id=administrator.id,
            password_hash=self._passwords.hash(new_password),
            must_change_password=False,
            retained_token_digest=digest_session_token(raw_token),
            now=current_time,
        )
        if updated is None:
            raise InvalidCredentialsError
        return AuthenticatedSession(
            administrator_id=administrator.id,
            username=administrator.username,
            must_change_password=False,
            expires_at=authenticated.expires_at,
        )

    def logout(self, raw_token: str, now: datetime) -> None:
        self._sessions.revoke(
            token_digest=digest_session_token(raw_token),
            now=_as_utc(now),
        )
