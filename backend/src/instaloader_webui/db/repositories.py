from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from instaloader_webui.db.models import AdminUser, LoginFailure, WebSession

SINGLE_ADMIN_ID = "00000000-0000-0000-0000-000000000001"


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class AdminSnapshot:
    id: str
    username: str
    password_hash: str
    must_change_password: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class WebSessionSnapshot:
    id: str
    admin_user_id: str
    token_digest: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class LoginFailureSnapshot:
    bucket_digest: str
    failure_count: int
    first_failure_at: datetime
    last_failure_at: datetime
    blocked_until: datetime | None


def _admin_snapshot(model: AdminUser) -> AdminSnapshot:
    return AdminSnapshot(
        id=model.id,
        username=model.username,
        password_hash=model.password_hash,
        must_change_password=model.must_change_password,
        created_at=_as_utc(model.created_at),
        updated_at=_as_utc(model.updated_at),
    )


def _web_session_snapshot(model: WebSession) -> WebSessionSnapshot:
    return WebSessionSnapshot(
        id=model.id,
        admin_user_id=model.admin_user_id,
        token_digest=model.token_digest,
        created_at=_as_utc(model.created_at),
        last_seen_at=_as_utc(model.last_seen_at),
        expires_at=_as_utc(model.expires_at),
        revoked_at=_as_utc(model.revoked_at) if model.revoked_at is not None else None,
    )


def _login_failure_snapshot(model: LoginFailure) -> LoginFailureSnapshot:
    return LoginFailureSnapshot(
        bucket_digest=model.bucket_digest,
        failure_count=model.failure_count,
        first_failure_at=_as_utc(model.first_failure_at),
        last_failure_at=_as_utc(model.last_failure_at),
        blocked_until=(
            _as_utc(model.blocked_until) if model.blocked_until is not None else None
        ),
    )


class AdminRepository:
    """Persist administrators without exposing mutable ORM objects."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create(
        self,
        *,
        username: str,
        password_hash: str,
        must_change_password: bool,
        now: datetime,
    ) -> AdminSnapshot:
        created_at = _as_utc(now)
        model = AdminUser(
            id=SINGLE_ADMIN_ID,
            username=username,
            password_hash=password_hash,
            must_change_password=must_change_password,
            created_at=created_at,
            updated_at=created_at,
        )
        with self._session_factory.begin() as session:
            session.add(model)
            session.flush()
            return _admin_snapshot(model)

    def get_single(self) -> AdminSnapshot | None:
        with self._session_factory() as session:
            model = session.scalar(select(AdminUser).order_by(AdminUser.created_at))
            return _admin_snapshot(model) if model is not None else None

    def get_by_id(self, administrator_id: str) -> AdminSnapshot | None:
        with self._session_factory() as session:
            model = session.get(AdminUser, administrator_id)
            return _admin_snapshot(model) if model is not None else None

    def change_password_and_revoke_other_sessions(
        self,
        *,
        administrator_id: str,
        password_hash: str,
        must_change_password: bool,
        retained_token_digest: str,
        now: datetime,
    ) -> AdminSnapshot | None:
        changed_at = _as_utc(now)
        with self._session_factory.begin() as session:
            administrator = session.get(AdminUser, administrator_id)
            if administrator is None:
                return None
            other_sessions = session.scalars(
                select(WebSession).where(
                    WebSession.admin_user_id == administrator_id,
                    WebSession.token_digest != retained_token_digest,
                    WebSession.revoked_at.is_(None),
                )
            ).all()
            administrator.password_hash = password_hash
            administrator.must_change_password = must_change_password
            administrator.updated_at = changed_at
            for web_session in other_sessions:
                web_session.revoked_at = changed_at
            session.flush()
            return _admin_snapshot(administrator)


class WebSessionRepository:
    """Persist browser sessions using digest-only, immutable boundaries."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create(
        self,
        *,
        admin_user_id: str,
        token_digest: str,
        now: datetime,
        expires_at: datetime,
    ) -> WebSessionSnapshot:
        created_at = _as_utc(now)
        model = WebSession(
            id=str(uuid4()),
            admin_user_id=admin_user_id,
            token_digest=token_digest,
            created_at=created_at,
            last_seen_at=created_at,
            expires_at=_as_utc(expires_at),
            revoked_at=None,
        )
        with self._session_factory.begin() as session:
            session.add(model)
            session.flush()
            return _web_session_snapshot(model)

    def get_by_token_digest(self, token_digest: str) -> WebSessionSnapshot | None:
        with self._session_factory() as session:
            model = session.scalar(
                select(WebSession).where(WebSession.token_digest == token_digest)
            )
            return _web_session_snapshot(model) if model is not None else None

    def touch(self, *, token_digest: str, now: datetime) -> WebSessionSnapshot | None:
        with self._session_factory.begin() as session:
            model = session.scalar(
                select(WebSession).where(WebSession.token_digest == token_digest)
            )
            if model is None:
                return None
            model.last_seen_at = _as_utc(now)
            session.flush()
            return _web_session_snapshot(model)

    def revoke(self, *, token_digest: str, now: datetime) -> WebSessionSnapshot | None:
        with self._session_factory.begin() as session:
            model = session.scalar(
                select(WebSession).where(WebSession.token_digest == token_digest)
            )
            if model is None:
                return None
            model.revoked_at = _as_utc(now)
            session.flush()
            return _web_session_snapshot(model)

class LoginFailureRepository:
    """Persist digest-only login-failure buckets behind immutable snapshots."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get(self, bucket_digest: str) -> LoginFailureSnapshot | None:
        with self._session_factory() as session:
            model = session.get(LoginFailure, bucket_digest)
            return _login_failure_snapshot(model) if model is not None else None

    def save(self, snapshot: LoginFailureSnapshot) -> None:
        with self._session_factory.begin() as session:
            model = session.get(LoginFailure, snapshot.bucket_digest)
            if model is None:
                session.add(
                    LoginFailure(
                        bucket_digest=snapshot.bucket_digest,
                        failure_count=snapshot.failure_count,
                        first_failure_at=_as_utc(snapshot.first_failure_at),
                        last_failure_at=_as_utc(snapshot.last_failure_at),
                        blocked_until=(
                            _as_utc(snapshot.blocked_until)
                            if snapshot.blocked_until is not None
                            else None
                        ),
                    )
                )
                return
            model.failure_count = snapshot.failure_count
            model.first_failure_at = _as_utc(snapshot.first_failure_at)
            model.last_failure_at = _as_utc(snapshot.last_failure_at)
            model.blocked_until = (
                _as_utc(snapshot.blocked_until)
                if snapshot.blocked_until is not None
                else None
            )

    def record_failure(
        self,
        *,
        bucket_digest: str,
        now: datetime,
        failure_window: timedelta,
        maximum_failures: int,
        block_duration: timedelta,
    ) -> None:
        """Atomically apply a failed attempt using SQLite's write lock."""
        current_time = _as_utc(now)
        with self._session_factory() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            model = session.get(LoginFailure, bucket_digest)
            if model is None or current_time - _as_utc(
                model.first_failure_at
            ) >= failure_window:
                snapshot = LoginFailureSnapshot(
                    bucket_digest=bucket_digest,
                    failure_count=1,
                    first_failure_at=current_time,
                    last_failure_at=current_time,
                    blocked_until=None,
                )
            elif model.blocked_until is not None and _as_utc(
                model.blocked_until
            ) > current_time:
                session.commit()
                return
            else:
                failure_count = model.failure_count + 1
                snapshot = LoginFailureSnapshot(
                    bucket_digest=bucket_digest,
                    failure_count=failure_count,
                    first_failure_at=_as_utc(model.first_failure_at),
                    last_failure_at=current_time,
                    blocked_until=(
                        current_time + block_duration
                        if failure_count >= maximum_failures
                        else None
                    ),
                )
            if model is None:
                session.add(
                    LoginFailure(
                        bucket_digest=snapshot.bucket_digest,
                        failure_count=snapshot.failure_count,
                        first_failure_at=snapshot.first_failure_at,
                        last_failure_at=snapshot.last_failure_at,
                        blocked_until=snapshot.blocked_until,
                    )
                )
            else:
                model.failure_count = snapshot.failure_count
                model.first_failure_at = snapshot.first_failure_at
                model.last_failure_at = snapshot.last_failure_at
                model.blocked_until = snapshot.blocked_until
            session.commit()

    def delete(self, bucket_digest: str) -> None:
        with self._session_factory.begin() as session:
            model = session.get(LoginFailure, bucket_digest)
            if model is not None:
                session.delete(model)
