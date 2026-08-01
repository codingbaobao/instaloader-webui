import ast
import importlib
import multiprocessing
import sqlite3
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import inspect, text

from instaloader_webui.config import Settings
from instaloader_webui.db.engine import build_engine

SAFE_SCHEMA_ERROR = (
    "Unsupported pre-1.0 database schema. Delete and recreate the database."
)


def _schema_module() -> Any:
    return importlib.import_module("instaloader_webui.db.schema")


def _initialize_in_process(
    data_root: str,
    barrier: Any,
    results: Any,
) -> None:
    barrier.wait(timeout=10)
    schema = _schema_module()
    schema.initialize_database(
        Settings(
            data_root=Path(data_root),
            admin_username="owner",
            admin_password="correct-horse-battery-staple",
        )
    )
    results.put(("ok", ""))


def _user_tables(database_path: Path) -> set[str]:
    with sqlite3.connect(database_path) as connection:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }


def test_fresh_bootstrap_creates_the_complete_current_orm_schema_and_defaults(
    test_settings: Settings,
) -> None:
    # Break caught: creating only an auth-era or partially modeled schema leaves
    # current repositories without Story, asset-role, issue, or followee storage.
    schema = _schema_module()

    schema.initialize_database(test_settings)

    from instaloader_webui.db.base import Base

    engine = build_engine(test_settings.database_path)
    inspector = inspect(engine)
    actual_tables = set(inspector.get_table_names())
    assert actual_tables == set(Base.metadata.tables)
    assert "alembic_version" not in actual_tables

    for table_name, table in Base.metadata.tables.items():
        assert {column["name"] for column in inspector.get_columns(table_name)} == {
            column.name for column in table.columns
        }
        assert {
            (index["name"], tuple(index["column_names"]))
            for index in inspector.get_indexes(table_name)
        } >= {
            (index.name, tuple(column.name for column in index.columns))
            for index in table.indexes
            if index.name is not None
        }
        assert {
            constraint["name"]
            for constraint in inspector.get_check_constraints(table_name)
        } >= {
            constraint.name
            for constraint in table.constraints
            if constraint.__class__.__name__ == "CheckConstraint"
            and constraint.name is not None
        }
        assert {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(table_name)
        } == {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert {
            (
                tuple(foreign_key["constrained_columns"]),
                foreign_key["referred_table"],
                tuple(foreign_key["referred_columns"]),
                foreign_key["options"].get("ondelete"),
            )
            for foreign_key in inspector.get_foreign_keys(table_name)
        } == {
            (
                tuple(constraint.column_keys),
                next(iter(constraint.elements)).column.table.name,
                tuple(element.column.name for element in constraint.elements),
                constraint.ondelete,
            )
            for constraint in table.constraints
            if constraint.__class__.__name__ == "ForeignKeyConstraint"
        }

    media_columns = {
        column["name"]: column["nullable"]
        for column in inspector.get_columns("media_items")
    }
    assert media_columns["shortcode"] is True
    assert media_columns["identity_type"] is False
    assert media_columns["identity_value"] is False
    assert media_columns["story_expires_at"] is True
    assert {
        column["name"]: column["nullable"]
        for column in inspector.get_columns("media_assets")
    }["role"] is False
    assert {column["name"] for column in inspector.get_columns("jobs")} >= {"phase"}
    assert {index["name"] for index in inspector.get_indexes("job_issues")} >= {
        "ix_job_issues_job_id_occurred_at",
        "ix_job_issues_identity_type_identity_value",
    }

    with engine.connect() as connection:
        marker = connection.execute(
            text("SELECT version FROM schema_marker WHERE id = 'global'")
        ).scalar_one()
        singleton = connection.execute(
            text(
                "SELECT id, profile_sync_interval_minutes, next_sync_at, "
                "created_at, updated_at FROM app_settings"
            )
        ).one()
    engine.dispose()

    assert marker == schema.CURRENT_SCHEMA_VERSION
    assert singleton[0:2] == ("global", 360)
    assert tuple(
        datetime.fromisoformat(value).replace(tzinfo=UTC) for value in singleton[2:]
    ) == (
        datetime(1970, 1, 1, tzinfo=UTC),
        datetime(1970, 1, 1, tzinfo=UTC),
        datetime(1970, 1, 1, tzinfo=UTC),
    )


def test_repeated_bootstrap_preserves_existing_data(test_settings: Settings) -> None:
    # Break caught: treating a supported restart as fresh can recreate tables or
    # overwrite application rows.
    schema = _schema_module()
    schema.initialize_database(test_settings)
    engine = build_engine(test_settings.database_path)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO admin_users "
                "(id, username, password_hash, must_change_password, created_at, updated_at) "
                "VALUES ('admin-1', 'preserved', 'hash', 0, :now, :now)"
            ),
            {"now": "2026-08-01T00:00:00+00:00"},
        )
    engine.dispose()

    schema.initialize_database(test_settings)

    with build_engine(test_settings.database_path).connect() as connection:
        assert (
            connection.execute(
                text("SELECT username FROM admin_users WHERE id = 'admin-1'")
            ).scalar_one()
            == "preserved"
        )


def test_two_processes_can_initialize_one_new_sqlite_database(
    test_settings: Settings,
) -> None:
    # Break caught: process-local or naked checkfirst guards race when web and
    # worker start together against a brand-new shared SQLite path.
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(
            target=_initialize_in_process,
            args=(str(test_settings.data_root), barrier, results),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)

    assert [process.exitcode for process in processes] == [0, 0]
    assert sorted(results.get(timeout=2) for _ in processes) == [
        ("ok", ""),
        ("ok", ""),
    ]
    schema = _schema_module()
    engine = build_engine(test_settings.database_path)
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM schema_marker WHERE version = :version"),
                {"version": schema.CURRENT_SCHEMA_VERSION},
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM app_settings WHERE id = 'global'")
            ).scalar_one()
            == 1
        )
    engine.dispose()


@pytest.mark.parametrize("existing_kind", ["zero-byte", "table-empty"])
def test_empty_existing_database_is_accepted_as_fresh(
    test_settings: Settings,
    existing_kind: str,
) -> None:
    # Break caught: deciding freshness only from file existence rejects valid
    # empty volumes and SQLite files without application tables.
    test_settings.database_path.parent.mkdir(parents=True)
    if existing_kind == "zero-byte":
        test_settings.database_path.touch()
    else:
        with sqlite3.connect(test_settings.database_path) as connection:
            connection.execute("PRAGMA user_version = 7")

    schema = _schema_module()
    schema.initialize_database(test_settings)

    from instaloader_webui.db.base import Base

    assert set(Base.metadata.tables) == _user_tables(test_settings.database_path)


def _create_incompatible_database(database_path: Path, kind: str) -> None:
    database_path.parent.mkdir(parents=True)
    with sqlite3.connect(database_path) as connection:
        if kind == "unmarked":
            connection.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
            connection.execute("INSERT INTO sentinel VALUES ('preserve-me')")
        elif kind == "alembic":
            connection.execute(
                "CREATE TABLE alembic_version (version_num TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO alembic_version VALUES ('0008_story_media')"
            )
        else:
            connection.execute(
                "CREATE TABLE schema_marker "
                "(id TEXT PRIMARY KEY, version TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO schema_marker VALUES ('global', 'different-schema')"
            )
            connection.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")


def _rewrite_table_definition(
    connection: sqlite3.Connection,
    table_name: str,
    old_fragment: str,
    new_fragment: str,
) -> None:
    definition = connection.execute(
        "SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()[0]
    changed_definition = definition.replace(old_fragment, new_fragment)
    assert changed_definition != definition
    schema_version = connection.execute("PRAGMA schema_version").fetchone()[0]
    connection.execute("PRAGMA writable_schema = ON")
    connection.execute(
        "UPDATE sqlite_schema SET sql = ? WHERE type = 'table' AND name = ?",
        (changed_definition, table_name),
    )
    connection.execute(f"PRAGMA schema_version = {schema_version + 1}")
    connection.execute("PRAGMA writable_schema = OFF")


def _apply_marked_schema_drift(database_path: Path, kind: str) -> None:
    with sqlite3.connect(database_path) as connection:
        if kind == "missing-index":
            connection.execute("DROP INDEX ix_job_issues_identity_type_identity_value")
        elif kind == "missing-column":
            connection.execute("ALTER TABLE jobs DROP COLUMN phase")
        elif kind == "changed-unique":
            _rewrite_table_definition(
                connection,
                "media_items",
                "UNIQUE (identity_type, identity_value)",
                "UNIQUE (identity_type, shortcode)",
            )
        elif kind == "changed-check":
            _rewrite_table_definition(
                connection,
                "media_assets",
                "CHECK (role IN ('content', 'poster'))",
                "CHECK (role IN ('content', 'poster', 'thumbnail'))",
            )
        else:
            _rewrite_table_definition(
                connection,
                "job_issues",
                ", \n\tFOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE",
                "",
            )
        connection.execute("DELETE FROM app_settings")


def _schema_definitions(database_path: Path) -> tuple[tuple[object, ...], ...]:
    with sqlite3.connect(database_path) as connection:
        return tuple(
            connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_schema "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            )
        )


@pytest.mark.parametrize(
    "kind",
    [
        "missing-index",
        "missing-column",
        "changed-unique",
        "changed-check",
        "missing-foreign-key",
    ],
)
def test_marked_database_with_schema_drift_fails_closed_before_seed_repair(
    test_settings: Settings,
    kind: str,
) -> None:
    # Break caught: trusting only the marker and table names accepts a drifted
    # schema, then mutates it by repairing seed data before repositories start.
    schema = _schema_module()
    schema.initialize_database(test_settings)
    _apply_marked_schema_drift(test_settings.database_path, kind)
    before_bytes = test_settings.database_path.read_bytes()
    before_schema = _schema_definitions(test_settings.database_path)

    with pytest.raises(schema.SchemaCompatibilityError) as caught:
        schema.initialize_database(test_settings)

    assert str(caught.value) == SAFE_SCHEMA_ERROR
    assert test_settings.database_path.read_bytes() == before_bytes
    assert _schema_definitions(test_settings.database_path) == before_schema
    with sqlite3.connect(test_settings.database_path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM app_settings").fetchone()[0] == 0
        )


@pytest.mark.parametrize("kind", ["unmarked", "alembic", "mismatched-marker"])
def test_incompatible_database_fails_closed_without_mutation(
    test_settings: Settings,
    kind: str,
) -> None:
    # Break caught: create_all on legacy/unmarked databases silently produces a
    # hybrid schema and can destroy the only recoverable pre-1.0 copy.
    _create_incompatible_database(test_settings.database_path, kind)
    before_bytes = test_settings.database_path.read_bytes()
    before_tables = _user_tables(test_settings.database_path)
    schema = _schema_module()

    with pytest.raises(schema.SchemaCompatibilityError) as caught:
        schema.initialize_database(test_settings)

    assert str(caught.value) == SAFE_SCHEMA_ERROR
    assert test_settings.database_path.read_bytes() == before_bytes
    assert _user_tables(test_settings.database_path) == before_tables


def test_supported_complete_schema_repairs_only_the_missing_settings_singleton(
    test_settings: Settings,
) -> None:
    # Break caught: restarts must repair required seed state without replacing
    # schema or unrelated data.
    schema = _schema_module()
    schema.initialize_database(test_settings)
    engine = build_engine(test_settings.database_path)
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM app_settings"))
        connection.execute(
            text(
                "INSERT INTO admin_users "
                "(id, username, password_hash, must_change_password, created_at, updated_at) "
                "VALUES ('admin-1', 'preserved', 'hash', 0, :now, :now)"
            ),
            {"now": "2026-08-01T00:00:00+00:00"},
        )
    engine.dispose()

    schema.initialize_database(test_settings)

    engine = build_engine(test_settings.database_path)
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM app_settings WHERE id = 'global'")
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text("SELECT username FROM admin_users WHERE id = 'admin-1'")
            ).scalar_one()
            == "preserved"
        )
    engine.dispose()


def test_backend_has_no_alembic_runtime_or_packaged_migration_assets() -> None:
    # Break caught: leaving a dependency, import, entry point, or package rule
    # keeps the removed migration engine in deployed artifacts.
    backend_root = Path(__file__).parents[2]
    project = tomllib.loads((backend_root / "pyproject.toml").read_text("utf-8"))
    assert all(
        not dependency.casefold().startswith("alembic")
        for dependency in project["project"]["dependencies"]
    )
    assert not (backend_root / "alembic.ini").exists()
    assert not (backend_root / "migrations").exists()
    assert not (backend_root / "src/instaloader_webui/db/migrations.py").exists()
    assert "force-include" not in project["tool"]["hatch"]["build"]["targets"]["wheel"]
    for source_file in (backend_root / "src").rglob("*.py"):
        syntax_tree = ast.parse(source_file.read_text("utf-8"))
        for node in ast.walk(syntax_tree):
            if isinstance(node, ast.Import):
                assert all(
                    not imported.name.startswith("alembic") for imported in node.names
                )
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                assert not node.module.startswith("alembic")
