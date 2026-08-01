"""Create and validate the one supported pre-1.0 database schema."""

import fcntl
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from sqlalchemy import insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from instaloader_webui.config import Settings
from instaloader_webui.db.base import Base
from instaloader_webui.db.engine import build_engine
from instaloader_webui.db.models import AppSetting, SchemaMarker

CURRENT_SCHEMA_VERSION = "pre-1.0-fresh-schema-1"
SCHEMA_COMPATIBILITY_ERROR = (
    "Unsupported pre-1.0 database schema. Delete and recreate the database."
)
_GLOBAL_SINGLETON_ID = "global"
_DEFAULT_PROFILE_SYNC_INTERVAL_MINUTES = 360
_DEFAULT_TIMESTAMP = datetime(1970, 1, 1, tzinfo=UTC)


class SchemaCompatibilityError(RuntimeError):
    """The database is not the exact fresh schema supported by this build."""


@contextmanager
def _schema_lock(database_path: Path) -> Iterator[None]:
    lock_path = database_path.with_name(f"{database_path.name}.schema.lock")
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read_existing_tables(database_path: Path) -> set[str] | None:
    if not database_path.exists() or database_path.stat().st_size == 0:
        return None

    database_uri = f"file:{quote(database_path.as_posix(), safe='/')}?mode=ro"
    try:
        with sqlite3.connect(database_uri, uri=True) as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_schema "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
            return {str(row[0]) for row in rows}
    except sqlite3.DatabaseError as error:
        raise SchemaCompatibilityError(SCHEMA_COMPATIBILITY_ERROR) from error


def _validate_supported_schema(database_path: Path, tables: set[str]) -> None:
    expected_tables = set(Base.metadata.tables)
    if "alembic_version" in tables or "schema_marker" not in tables:
        raise SchemaCompatibilityError(SCHEMA_COMPATIBILITY_ERROR)

    database_uri = f"file:{quote(database_path.as_posix(), safe='/')}?mode=ro"
    try:
        with sqlite3.connect(database_uri, uri=True) as connection:
            marker_rows = connection.execute(
                "SELECT id, version FROM schema_marker"
            ).fetchall()
    except sqlite3.DatabaseError as error:
        raise SchemaCompatibilityError(SCHEMA_COMPATIBILITY_ERROR) from error

    if marker_rows != [(_GLOBAL_SINGLETON_ID, CURRENT_SCHEMA_VERSION)]:
        raise SchemaCompatibilityError(SCHEMA_COMPATIBILITY_ERROR)
    if tables != expected_tables:
        raise SchemaCompatibilityError(SCHEMA_COMPATIBILITY_ERROR)


def _seed_settings_statement() -> Any:
    return (
        sqlite_insert(AppSetting)
        .values(
            id=_GLOBAL_SINGLETON_ID,
            profile_sync_interval_minutes=_DEFAULT_PROFILE_SYNC_INTERVAL_MINUTES,
            next_sync_at=_DEFAULT_TIMESTAMP,
            created_at=_DEFAULT_TIMESTAMP,
            updated_at=_DEFAULT_TIMESTAMP,
        )
        .on_conflict_do_nothing(index_elements=[AppSetting.id])
    )


def _create_fresh_schema(database_path: Path) -> None:
    engine = build_engine(database_path)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                Base.metadata.create_all(connection)
                connection.execute(
                    insert(SchemaMarker).values(
                        id=_GLOBAL_SINGLETON_ID,
                        version=CURRENT_SCHEMA_VERSION,
                    )
                )
                connection.execute(_seed_settings_statement())
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()
    finally:
        engine.dispose()


def _repair_required_settings(database_path: Path) -> None:
    engine = build_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(_seed_settings_statement())
    finally:
        engine.dispose()


def initialize_database(settings: Settings) -> None:
    """Create a fresh current schema or validate a supported current database."""
    database_path = settings.database_path.resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with _schema_lock(database_path):
        existing_tables = _read_existing_tables(database_path)
        if not existing_tables:
            _create_fresh_schema(database_path)
            return

        _validate_supported_schema(database_path, existing_tables)
        _repair_required_settings(database_path)
