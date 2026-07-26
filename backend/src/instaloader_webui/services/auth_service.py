import hmac
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from unicodedata import normalize

from instaloader_webui.auth.passwords import PasswordService, PasswordServiceBusyError
from instaloader_webui.auth.session_tokens import (
    digest_session_token,
    issue_session_token,
)
from instaloader_webui.auth.throttle import LoginAttemptKey, LoginThrottle
from instaloader_webui.config import (
    MAXIMUM_USERNAME_BYTES,
)
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


class AuthenticationBusyError(Exception):
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


def _valid_utf8(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _valid_utf8_bytes(value: str, *, maximum_bytes: int) -> bool:
    return _valid_utf8(value) and len(value.encode("utf-8")) <= maximum_bytes


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
        canonical_username = normalize("NFKC", username).strip().casefold()
        if not _valid_utf8_bytes(
            canonical_username, maximum_bytes=MAXIMUM_USERNAME_BYTES
        ):
            raise InvalidCredentialsError
        current_time = _as_utc(now)
        attempt = LoginAttemptKey(username=canonical_username, client_ip=client_ip)
        admission = self._throttle.reserve(attempt, current_time)
        if not admission.allowed:
            raise LoginThrottledError(admission.retry_after_seconds)
        assert admission.reservation_id is not None

        administrator = self._administrators.get_single()
        username_matches = administrator is not None and hmac.compare_digest(
            canonical_username.encode("utf-8"),
            administrator.username.casefold().encode("utf-8"),
        )
        password_hash = (
            administrator.password_hash
            if administrator is not None
            else self._passwords.dummy_hash
        )
        try:
            password_matches = self._passwords.verify(password_hash, password)
        except PasswordServiceBusyError as error:
            self._throttle.cancel(admission)
            raise AuthenticationBusyError from error
        if not username_matches or not password_matches or administrator is None:
            self._throttle.record_reserved_failure(admission, current_time)
            raise InvalidCredentialsError

        issued = issue_session_token()
        expires_at = current_time + SESSION_LIFETIME
        session = self._administrators.create_session_if_password_hash_matches(
            administrator_id=administrator.id,
            expected_password_hash=administrator.password_hash,
            reservation_id=admission.reservation_id,
            account_bucket_digest=admission.account_bucket_digest,
            ip_bucket_digest=admission.ip_bucket_digest,
            global_bucket_digest=admission.global_bucket_digest,
            token_digest=issued.digest,
            now=current_time,
            expires_at=expires_at,
        )
        if session is None:
            raise InvalidCredentialsError
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
        session = self._sessions.get_active_and_touch(
            token_digest=token_digest,
            now=current_time,
            cadence=SESSION_TOUCH_INTERVAL,
        )
        if session is None:
            return None

        administrator = self._administrators.get_by_id(session.admin_user_id)
        if administrator is None:
            return None

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
        if not _valid_utf8(current_password):
            raise InvalidCurrentPasswordError
        if not _valid_utf8(new_password):
            raise InvalidNewPasswordError
        current_time = _as_utc(now)
        authenticated = self.authenticate_session(raw_token, current_time)
        if authenticated is None:
            raise InvalidCredentialsError

        administrator = self._administrators.get_by_id(authenticated.administrator_id)
        if administrator is None:
            raise InvalidCredentialsError
        try:
            if not self._passwords.verify(
                administrator.password_hash, current_password
            ):
                raise InvalidCurrentPasswordError
        except PasswordServiceBusyError as error:
            raise AuthenticationBusyError from error
        try:
            replacement_password_hash = self._passwords.hash(new_password)
        except PasswordServiceBusyError as error:
            raise AuthenticationBusyError from error
        updated = self._administrators.change_password_and_revoke_other_sessions(
            administrator_id=administrator.id,
            expected_password_hash=administrator.password_hash,
            password_hash=replacement_password_hash,
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
