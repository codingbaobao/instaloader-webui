"""Entrypoint for the persistent single-process public-library worker."""

from datetime import UTC, datetime
import time

from instaloader_webui.config import Settings
from instaloader_webui.db.engine import build_engine, build_session_factory
from instaloader_webui.db.library_repositories import (
    JobRepository,
    LibraryRepository,
    SettingsRepository,
)
from instaloader_webui.db.migrations import run_migrations
from instaloader_webui.services.job_runner import JobRunner, enqueue_due_profile_syncs


def main() -> None:
    """Recover work and continuously claim public-library jobs."""
    settings = Settings()
    settings.data_root.mkdir(parents=True, exist_ok=True)
    settings.media_root.mkdir(parents=True, exist_ok=True)
    settings.jobs_root.mkdir(parents=True, exist_ok=True)
    run_migrations(settings)

    engine = build_engine(settings.database_path)
    session_factory = build_session_factory(engine)
    library = LibraryRepository(session_factory)
    jobs = JobRepository(session_factory)
    scheduling = SettingsRepository(session_factory)
    runner = JobRunner(
        data_root=settings.data_root,
        media_root=settings.media_root,
        jobs_root=settings.jobs_root,
        library=library,
        jobs=jobs,
    )
    jobs.recover_interrupted(datetime.now(UTC))

    try:
        while True:
            now = datetime.now(UTC)
            enqueue_due_profile_syncs(
                library=library,
                jobs=jobs,
                now=now,
                settings_claim_due_sync=scheduling.claim_due_sync,
            )
            job = jobs.claim_next(datetime.now(UTC))
            if job is None:
                time.sleep(2)
                continue
            runner.run(job)
    finally:
        engine.dispose()
