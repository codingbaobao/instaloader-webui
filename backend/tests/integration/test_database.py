from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from sqlalchemy import text

from instaloader_webui.db.engine import build_engine
from instaloader_webui.db.repositories import AdminRepository, WebSessionRepository
from instaloader_webui.db.schema import initialize_database


def test_sqlite_enables_wal_and_foreign_keys(test_settings) -> None:
    engine = build_engine(test_settings.database_path)

    with engine.connect() as connection:
        journal_mode = connection.execute(text("PRAGMA journal_mode")).scalar_one()
        foreign_keys = connection.execute(text("PRAGMA foreign_keys")).scalar_one()
        busy_timeout = connection.execute(text("PRAGMA busy_timeout")).scalar_one()

    assert journal_mode.lower() == "wal"
    assert foreign_keys == 1
    assert busy_timeout == 5_000


def test_admin_repository_returns_immutable_snapshot(
    session_factory, test_settings
) -> None:
    initialize_database(test_settings)
    repository = AdminRepository(session_factory)
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)

    created = repository.create(
        username="owner",
        password_hash="argon2-hash",
        must_change_password=True,
        now=now,
    )

    assert created.username == "owner"
    assert created.password_hash == "argon2-hash"
    assert created.must_change_password is True
    assert created.created_at == now
    assert created.updated_at == now
    with pytest.raises(FrozenInstanceError):
        created.username = "changed"  # type: ignore[misc]
    assert repository.get_single() == created


def test_web_session_repository_returns_new_snapshots_on_updates(
    session_factory, test_settings
) -> None:
    initialize_database(test_settings)
    admins = AdminRepository(session_factory)
    sessions = WebSessionRepository(session_factory)
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
    admin = admins.create(
        username="owner",
        password_hash="argon2-hash",
        must_change_password=True,
        now=now,
    )

    created = sessions.create(
        admin_user_id=admin.id,
        token_digest="a" * 64,
        now=now,
        expires_at=now + timedelta(days=7),
    )
    refreshed_at = now + timedelta(minutes=5)
    refreshed = sessions.touch(token_digest=created.token_digest, now=refreshed_at)
    revoked_at = now + timedelta(hours=1)
    revoked = sessions.revoke(token_digest=created.token_digest, now=revoked_at)

    assert created.last_seen_at == now
    assert refreshed is not None
    assert refreshed.last_seen_at == refreshed_at
    assert refreshed != created
    assert revoked is not None
    assert revoked.revoked_at == revoked_at
    assert sessions.get_by_token_digest(created.token_digest) == revoked


def test_delayed_session_touch_cannot_regress_last_seen(
    session_factory, test_settings
) -> None:
    initialize_database(test_settings)
    admins = AdminRepository(session_factory)
    sessions = WebSessionRepository(session_factory)
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
    admin = admins.create(
        username="owner",
        password_hash="argon2-hash",
        must_change_password=False,
        now=now,
    )
    created = sessions.create(
        admin_user_id=admin.id,
        token_digest="b" * 64,
        now=now,
        expires_at=now + timedelta(days=7),
    )
    older_ready = Event()
    allow_older = Event()

    def delayed_older_touch():
        older_ready.set()
        assert allow_older.wait(timeout=5)
        return sessions.get_active_and_touch(
            token_digest=created.token_digest,
            now=now + timedelta(minutes=5),
            cadence=timedelta(minutes=5),
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        older = executor.submit(delayed_older_touch)
        assert older_ready.wait(timeout=5)
        newer = sessions.get_active_and_touch(
            token_digest=created.token_digest,
            now=now + timedelta(minutes=10),
            cadence=timedelta(minutes=5),
        )
        allow_older.set()
        assert older.result(timeout=5) is not None

    persisted = sessions.get_by_token_digest(created.token_digest)
    assert newer is not None
    assert persisted is not None
    assert persisted.last_seen_at == now + timedelta(minutes=10)
