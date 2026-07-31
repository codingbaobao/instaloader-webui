import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from instaloader_webui.db.engine import build_engine


def migration_config(database_path: Path) -> Config:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite:///{database_path.resolve().as_posix()}",
    )
    return config


def test_migration_preserves_existing_job_runner_logger_state(test_settings) -> None:
    logger = logging.getLogger("instaloader_webui.services.job_runner")
    original_state = (
        logger.disabled,
        logger.level,
        logger.propagate,
        logger.handlers[:],
    )
    sentinel_handler = logging.NullHandler()
    logger.disabled = False
    logger.setLevel(logging.WARNING)
    logger.propagate = False
    logger.addHandler(sentinel_handler)
    expected_state = (
        logger.disabled,
        logger.level,
        logger.propagate,
        logger.handlers[:],
    )

    try:
        command.upgrade(migration_config(test_settings.database_path), "head")

        assert (
            logger.disabled,
            logger.level,
            logger.propagate,
            logger.handlers,
        ) == expected_state
    finally:
        logger.disabled, logger.level, logger.propagate, handlers = original_state
        logger.handlers[:] = handlers


def insert_legacy_library_fixture(database_path: Path) -> None:
    with build_engine(database_path).begin() as connection:
        connection.execute(
            text(
                "INSERT INTO profiles ("
                "id, instagram_user_id, username, full_name, biography, "
                "profile_pic_url, tracked, status, created_at, updated_at"
                ") VALUES ("
                "'profile-1', '1234', 'example', 'Example Profile', '', "
                "NULL, 1, 'active', '2026-07-31T00:00:00+00:00', "
                "'2026-07-31T00:00:00+00:00'"
                ")"
            )
        )
        connection.execute(
            text(
                "INSERT INTO media_items ("
                "id, instagram_media_id, shortcode, owner_profile_id, kind, "
                "caption, accessibility_caption, published_at, original_url, "
                "downloaded_at, created_at, updated_at"
                ") VALUES ("
                "'media-1', '17800000000000001', 'CmzV2H-rrlI', 'profile-1', "
                "'post', '', '', '2026-07-31T00:00:00+00:00', "
                "'https://example.invalid/media', NULL, "
                "'2026-07-31T00:00:00+00:00', '2026-07-31T00:00:00+00:00'"
                ")"
            )
        )
        connection.execute(
            text(
                "INSERT INTO media_assets ("
                "id, media_item_id, relative_path, mime_type, kind, position, "
                "file_size, created_at"
                ") VALUES ("
                "'asset-1', 'media-1', 'example/CmzV2H-rrlI.jpg', 'image/jpeg', "
                "'image', 0, 42, '2026-07-31T00:00:00+00:00'"
                ")"
            )
        )
        connection.execute(
            text(
                "INSERT INTO jobs ("
                "id, type, state, payload_text, progress_current, progress_total, "
                "status_text, error, created_at, started_at, completed_at, updated_at"
                ") VALUES ("
                "'job-1', 'profile_sync', 'succeeded', '{}', 1, 1, 'done', NULL, "
                "'2026-07-31T00:00:00+00:00', '2026-07-31T00:00:00+00:00', "
                "'2026-07-31T00:00:00+00:00', '2026-07-31T00:00:00+00:00'"
                ")"
            )
        )


def test_story_schema_upgrade_preserves_existing_library_rows(test_settings) -> None:
    config = migration_config(test_settings.database_path)
    command.upgrade(config, "0007_followee_imports")
    insert_legacy_library_fixture(test_settings.database_path)

    command.upgrade(config, "head")

    engine = build_engine(test_settings.database_path)
    inspector = inspect(engine)
    with engine.connect() as connection:
        media = connection.execute(
            text(
                "SELECT shortcode, identity_type, identity_value, kind FROM media_items"
            )
        ).one()
        role = connection.execute(text("SELECT role FROM media_assets")).scalar_one()

    media_columns = {
        column["name"]: column for column in inspector.get_columns("media_items")
    }
    asset_columns = {
        column["name"]: column for column in inspector.get_columns("media_assets")
    }
    job_columns = {column["name"]: column for column in inspector.get_columns("jobs")}
    media_unique_constraints = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("media_items")
    }
    media_checks = {
        constraint["name"]: constraint["sqltext"]
        for constraint in inspector.get_check_constraints("media_items")
    }
    asset_checks = {
        constraint["name"]: constraint["sqltext"]
        for constraint in inspector.get_check_constraints("media_assets")
    }
    job_checks = {
        constraint["name"]: constraint["sqltext"]
        for constraint in inspector.get_check_constraints("jobs")
    }
    job_issue_foreign_keys = inspector.get_foreign_keys("job_issues")
    job_issue_columns = {
        column["name"]: column for column in inspector.get_columns("job_issues")
    }
    job_issue_indexes = {
        (index["name"], tuple(index["column_names"]))
        for index in inspector.get_indexes("job_issues")
    }

    assert media == ("CmzV2H-rrlI", "shortcode", "CmzV2H-rrlI", "post")
    assert role == "content"
    assert media_columns["shortcode"]["nullable"] is True
    assert media_columns["identity_type"]["nullable"] is False
    assert media_columns["identity_value"]["nullable"] is False
    assert media_columns["story_expires_at"]["nullable"] is True
    assert asset_columns["role"]["nullable"] is False
    assert asset_columns["role"]["default"] is None
    assert job_columns["state"]["type"].length == 32
    assert job_columns["phase"]["nullable"] is True
    assert {
        name for name, column in job_issue_columns.items() if not column["nullable"]
    } == {
        "id",
        "job_id",
        "identity_type",
        "identity_value",
        "media_kind",
        "error_code",
        "safe_message",
        "exception_class_chain_text",
        "occurred_at",
    }
    assert ("instagram_media_id",) in media_unique_constraints
    assert ("identity_type", "identity_value") in media_unique_constraints
    assert media_checks == {
        "ck_media_items_identity_type": "identity_type IN ('shortcode', 'story_media_id')",
        "ck_media_items_kind": "kind IN ('post', 'reel', 'story')",
    }
    assert asset_checks == {
        "ck_media_assets_kind": "kind IN ('image', 'video')",
        "ck_media_assets_role": "role IN ('content', 'poster')",
    }
    assert job_checks["ck_jobs_state"] == (
        "state IN ('pending', 'running', 'succeeded', 'failed', "
        "'completed_with_warnings')"
    )
    assert job_issue_foreign_keys == [
        {
            "name": None,
            "constrained_columns": ["job_id"],
            "referred_schema": None,
            "referred_table": "jobs",
            "referred_columns": ["id"],
            "options": {"ondelete": "CASCADE"},
        }
    ]
    assert {
        ("ix_job_issues_job_id_occurred_at", ("job_id", "occurred_at")),
        (
            "ix_job_issues_identity_type_identity_value",
            ("identity_type", "identity_value"),
        ),
    } <= job_issue_indexes


def test_story_schema_downgrade_removes_warning_jobs_and_dependent_batches(
    test_settings,
) -> None:
    config = migration_config(test_settings.database_path)
    command.upgrade(config, "0007_followee_imports")
    insert_legacy_library_fixture(test_settings.database_path)
    command.upgrade(config, "head")

    with build_engine(test_settings.database_path).begin() as connection:
        connection.execute(
            text(
                "INSERT INTO jobs ("
                "id, type, state, payload_text, progress_current, progress_total, "
                "status_text, error, phase, created_at, started_at, completed_at, "
                "updated_at"
                ") VALUES ("
                "'warning-job', 'followee_discovery', 'completed_with_warnings', "
                "'{}', 1, 1, 'finished with warnings', NULL, 'finalizing', "
                "'2026-07-31T00:00:00+00:00', '2026-07-31T00:00:00+00:00', "
                "'2026-07-31T00:00:00+00:00', '2026-07-31T00:00:00+00:00'"
                ")"
            )
        )
        connection.execute(
            text(
                "INSERT INTO followee_import_batches ("
                "id, state, source_username, session_imported_at, job_id, "
                "total_count, importable_count, existing_count, error, created_at, "
                "completed_at, imported_at"
                ") VALUES ("
                "'warning-batch', 'ready', 'example', '2026-07-31T00:00:00+00:00', "
                "'warning-job', 1, 1, 0, NULL, '2026-07-31T00:00:00+00:00', "
                "NULL, NULL"
                ")"
            )
        )
    command.downgrade(config, "0007_followee_imports")

    inspector = inspect(build_engine(test_settings.database_path))
    with build_engine(test_settings.database_path).connect() as connection:
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM jobs WHERE id = 'warning-job'")
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM followee_import_batches WHERE id = 'warning-batch'"
                )
            ).scalar_one()
            == 0
        )

    job_columns = {column["name"]: column for column in inspector.get_columns("jobs")}
    assert job_columns["state"]["type"].length == 16
    assert "phase" not in job_columns


def test_story_schema_downgrade_removes_non_shortcode_media_identities(
    test_settings,
) -> None:
    config = migration_config(test_settings.database_path)
    command.upgrade(config, "0007_followee_imports")
    insert_legacy_library_fixture(test_settings.database_path)
    command.upgrade(config, "head")

    with build_engine(test_settings.database_path).begin() as connection:
        connection.execute(
            text(
                "INSERT INTO media_items ("
                "id, instagram_media_id, shortcode, identity_type, identity_value, "
                "owner_profile_id, kind, caption, accessibility_caption, published_at, "
                "original_url, story_expires_at, downloaded_at, created_at, updated_at"
                ") VALUES ("
                "'story-identity-post', NULL, NULL, 'story_media_id', '987654321', "
                "'profile-1', 'post', '', '', '2026-07-31T00:00:00+00:00', "
                "'https://example.invalid/story', NULL, NULL, "
                "'2026-07-31T00:00:00+00:00', '2026-07-31T00:00:00+00:00'"
                ")"
            )
        )
        connection.execute(
            text(
                "INSERT INTO media_items ("
                "id, instagram_media_id, shortcode, identity_type, identity_value, "
                "owner_profile_id, kind, caption, accessibility_caption, published_at, "
                "original_url, story_expires_at, downloaded_at, created_at, updated_at"
                ") VALUES ("
                "'shortcode-identity-post', NULL, NULL, 'shortcode', "
                "'backfilled-shortcode', 'profile-1', 'post', '', '', "
                "'2026-07-31T00:00:00+00:00', 'https://example.invalid/post', "
                "NULL, NULL, '2026-07-31T00:00:00+00:00', "
                "'2026-07-31T00:00:00+00:00'"
                ")"
            )
        )

    command.downgrade(config, "0007_followee_imports")

    with build_engine(test_settings.database_path).connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM media_items WHERE id = 'story-identity-post'"
                )
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM media_items WHERE id = 'media-1'")
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text(
                    "SELECT shortcode FROM media_items "
                    "WHERE id = 'shortcode-identity-post'"
                )
            ).scalar_one()
            == "backfilled-shortcode"
        )
