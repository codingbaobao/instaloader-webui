from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from instaloader_webui.db.models import AdminUser, WebSession

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
