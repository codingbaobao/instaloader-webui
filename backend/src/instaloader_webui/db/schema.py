"""Create, migrate, and validate the supported pre-1.0 database schema."""

import fcntl
import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

from sqlalchemy import Table, create_engine, insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from instaloader_webui.config import Settings
from instaloader_webui.db.base import Base
from instaloader_webui.db.engine import build_engine
from instaloader_webui.db.models import (
    AppSetting,
    JobProgressSegment,
    ProfileSyncCheckpoint,
    SchemaMarker,
)

CURRENT_SCHEMA_VERSION = "pre-1.0-feed-sync-2"
_LEGACY_SCHEMA_VERSION = "pre-1.0-fresh-schema-1"
_LEGACY_SCHEMA_DIGEST = (
    "5edfc7cdd9d45fe1dea7ad4507417d5b1ea599793c49000cea26f90bab82e3b7"
)
SCHEMA_COMPATIBILITY_ERROR = (
    "Unsupported pre-1.0 database schema. Delete and recreate the database."
)
_GLOBAL_SINGLETON_ID = "global"
_DEFAULT_PROFILE_SYNC_INTERVAL_MINUTES = 360
_DEFAULT_TIMESTAMP = datetime(1970, 1, 1, tzinfo=UTC)
_SCHEMA_DEFINITION_QUERY = (
    "SELECT type, name, tbl_name, sql FROM sqlite_schema "
    "WHERE name NOT LIKE 'sqlite_%' "
    "AND type IN ('table', 'index', 'view', 'trigger') "
    "AND sql IS NOT NULL ORDER BY type, name, tbl_name"
)

SchemaSignature = tuple[tuple[str, str, str, str], ...]


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


def _schema_signature(rows: Any) -> SchemaSignature:
    return tuple(
        (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            " ".join(str(row[3]).split()),
        )
        for row in rows
    )


def _schema_digest(signature: SchemaSignature) -> str:
    serialized = json.dumps(
        signature,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


@lru_cache(maxsize=1)
def _expected_schema_signature() -> SchemaSignature:
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            return _schema_signature(
                connection.exec_driver_sql(_SCHEMA_DEFINITION_QUERY)
            )
    finally:
        engine.dispose()


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
            actual_schema_signature = _schema_signature(
                connection.execute(_SCHEMA_DEFINITION_QUERY)
            )
    except sqlite3.DatabaseError as error:
        raise SchemaCompatibilityError(SCHEMA_COMPATIBILITY_ERROR) from error

    if marker_rows != [(_GLOBAL_SINGLETON_ID, CURRENT_SCHEMA_VERSION)]:
        raise SchemaCompatibilityError(SCHEMA_COMPATIBILITY_ERROR)
    if tables != expected_tables:
        raise SchemaCompatibilityError(SCHEMA_COMPATIBILITY_ERROR)
    if actual_schema_signature != _expected_schema_signature():
        raise SchemaCompatibilityError(SCHEMA_COMPATIBILITY_ERROR)


def _read_marker_and_signature(
    database_path: Path,
) -> tuple[list[tuple[str, str]], SchemaSignature]:
    database_uri = f"file:{quote(database_path.as_posix(), safe='/')}?mode=ro"
    try:
        with sqlite3.connect(database_uri, uri=True) as connection:
            marker_rows = [
                (str(row[0]), str(row[1]))
                for row in connection.execute(
                    "SELECT id, version FROM schema_marker"
                ).fetchall()
            ]
            signature = _schema_signature(connection.execute(_SCHEMA_DEFINITION_QUERY))
    except sqlite3.DatabaseError as error:
        raise SchemaCompatibilityError(SCHEMA_COMPATIBILITY_ERROR) from error
    return marker_rows, signature


def _validate_legacy_schema(database_path: Path, tables: set[str]) -> bool:
    if "alembic_version" in tables or "schema_marker" not in tables:
        return False
    marker_rows, signature = _read_marker_and_signature(database_path)
    if marker_rows != [(_GLOBAL_SINGLETON_ID, _LEGACY_SCHEMA_VERSION)]:
        return False
    if _schema_digest(signature) != _LEGACY_SCHEMA_DIGEST:
        raise SchemaCompatibilityError(SCHEMA_COMPATIBILITY_ERROR)
    return True


def _create_version_two_tables(connection: Any) -> None:
    cast(Table, JobProgressSegment.__table__).create(connection)
    cast(Table, ProfileSyncCheckpoint.__table__).create(connection)


def _assert_current_schema_in_transaction(connection: Any) -> None:
    actual_tables = {
        str(row[0])
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_schema "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    marker_rows = [
        (str(row[0]), str(row[1]))
        for row in connection.exec_driver_sql("SELECT id, version FROM schema_marker")
    ]
    signature = _schema_signature(connection.exec_driver_sql(_SCHEMA_DEFINITION_QUERY))
    if actual_tables != set(Base.metadata.tables):
        raise SchemaCompatibilityError(SCHEMA_COMPATIBILITY_ERROR)
    if marker_rows != [(_GLOBAL_SINGLETON_ID, CURRENT_SCHEMA_VERSION)]:
        raise SchemaCompatibilityError(SCHEMA_COMPATIBILITY_ERROR)
    if signature != _expected_schema_signature():
        raise SchemaCompatibilityError(SCHEMA_COMPATIBILITY_ERROR)


def _migrate_version_one_schema(database_path: Path) -> None:
    engine = build_engine(database_path)
    now = datetime.now(UTC).isoformat()
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                connection.exec_driver_sql(
                    "ALTER TABLE jobs ADD COLUMN target_label TEXT"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE jobs ADD COLUMN target_url TEXT"
                )
                _create_version_two_tables(connection)
                connection.exec_driver_sql(
                    "UPDATE jobs SET target_label = ("
                    "SELECT '@' || profiles.username FROM profiles "
                    "WHERE profiles.id = json_extract(jobs.payload_text, '$.profile_id')"
                    ") WHERE type = 'profile_sync' AND json_valid(payload_text) "
                    "AND json_type(payload_text, '$.profile_id') = 'text'"
                )
                connection.exec_driver_sql(
                    "UPDATE jobs SET "
                    "target_label = json_extract(payload_text, '$.original_url'), "
                    "target_url = json_extract(payload_text, '$.original_url') "
                    "WHERE type = 'single_media' AND json_valid(payload_text) "
                    "AND json_type(payload_text, '$.original_url') = 'text'"
                )
                connection.exec_driver_sql(
                    "INSERT INTO profile_sync_checkpoints "
                    "(profile_id, source, cursor_version, cursor_json, "
                    "backfill_complete, updated_at) "
                    "SELECT profiles.id, sources.source, 1, NULL, 0, :updated_at "
                    "FROM profiles CROSS JOIN ("
                    "SELECT 'posts' AS source UNION ALL SELECT 'reels'"
                    ") AS sources "
                    "WHERE profiles.tracked = 1 AND profiles.status = 'active'",
                    {"updated_at": now},
                )
                connection.exec_driver_sql(
                    "UPDATE schema_marker SET version = :version "
                    "WHERE id = :singleton_id",
                    {
                        "version": CURRENT_SCHEMA_VERSION,
                        "singleton_id": _GLOBAL_SINGLETON_ID,
                    },
                )
                _assert_current_schema_in_transaction(connection)
            except SchemaCompatibilityError:
                connection.rollback()
                raise
            except Exception as error:
                connection.rollback()
                raise SchemaCompatibilityError(SCHEMA_COMPATIBILITY_ERROR) from error
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()
    finally:
        engine.dispose()


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
    """Create, migrate, or validate the supported current database schema."""
    database_path = settings.database_path.resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with _schema_lock(database_path):
        existing_tables = _read_existing_tables(database_path)
        if not existing_tables:
            _create_fresh_schema(database_path)
            return

        marker_rows, _ = _read_marker_and_signature(database_path)
        if marker_rows == [(_GLOBAL_SINGLETON_ID, CURRENT_SCHEMA_VERSION)]:
            _validate_supported_schema(database_path, existing_tables)
        elif _validate_legacy_schema(database_path, existing_tables):
            _migrate_version_one_schema(database_path)
            migrated_tables = _read_existing_tables(database_path)
            if migrated_tables is None:
                raise SchemaCompatibilityError(SCHEMA_COMPATIBILITY_ERROR)
            _validate_supported_schema(database_path, migrated_tables)
        else:
            raise SchemaCompatibilityError(SCHEMA_COMPATIBILITY_ERROR)
        _repair_required_settings(database_path)
