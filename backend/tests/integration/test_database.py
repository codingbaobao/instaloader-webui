from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect, text

from instaloader_webui.db.engine import build_engine
from instaloader_webui.db.migrations import run_migrations
from instaloader_webui.db.repositories import AdminRepository, WebSessionRepository


def test_sqlite_enables_wal_and_foreign_keys(test_settings) -> None:
    engine = build_engine(test_settings.database_path)

    with engine.connect() as connection:
        journal_mode = connection.execute(text("PRAGMA journal_mode")).scalar_one()
        foreign_keys = connection.execute(text("PRAGMA foreign_keys")).scalar_one()
        busy_timeout = connection.execute(text("PRAGMA busy_timeout")).scalar_one()

    assert journal_mode.lower() == "wal"
    assert foreign_keys == 1
    assert busy_timeout == 5_000


def test_initial_migration_creates_auth_tables(test_settings) -> None:
    run_migrations(test_settings)
    engine = build_engine(test_settings.database_path)

    with engine.connect() as connection:
        names = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'table'")
            )
        }

    assert {"admin_users", "web_sessions", "alembic_version"} <= names


def test_initial_migration_matches_auth_model_schema(test_settings) -> None:
    run_migrations(test_settings)
    inspector = inspect(build_engine(test_settings.database_path))

    admin_columns = {column["name"] for column in inspector.get_columns("admin_users")}
    session_columns = {
        column["name"] for column in inspector.get_columns("web_sessions")
    }
    admin_unique_columns = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("admin_users")
    }
    session_unique_columns = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("web_sessions")
    }
    foreign_keys = inspector.get_foreign_keys("web_sessions")

    assert admin_columns == {
        "id",
        "username",
        "password_hash",
        "must_change_password",
        "created_at",
        "updated_at",
    }
    assert session_columns == {
        "id",
        "admin_user_id",
        "token_digest",
        "created_at",
        "last_seen_at",
        "expires_at",
        "revoked_at",
    }
    assert ("username",) in admin_unique_columns
    assert ("token_digest",) in session_unique_columns
    assert foreign_keys == [
        {
            "name": None,
            "constrained_columns": ["admin_user_id"],
            "referred_schema": None,
            "referred_table": "admin_users",
            "referred_columns": ["id"],
            "options": {},
        }
    ]


def test_admin_repository_returns_immutable_snapshot(
    session_factory, test_settings
) -> None:
    run_migrations(test_settings)
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
    run_migrations(test_settings)
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
