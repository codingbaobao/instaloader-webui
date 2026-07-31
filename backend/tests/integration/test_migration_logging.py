import logging

from instaloader_webui.db.migrations import run_migrations


def test_application_migration_preserves_existing_job_runner_logger_state(
    test_settings,
) -> None:
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
        run_migrations(test_settings)

        assert (
            logger.disabled,
            logger.level,
            logger.propagate,
            logger.handlers,
        ) == expected_state
    finally:
        logger.disabled, logger.level, logger.propagate, handlers = original_state
        logger.handlers[:] = handlers
