"""Entrypoint for the persistent single-process public-library worker."""

import logging
import time
from datetime import UTC, datetime

from instaloader_webui.auth.app_secret import load_or_create_app_secret
from instaloader_webui.config import Settings
from instaloader_webui.db.engine import build_engine, build_session_factory
from instaloader_webui.db.followee_import_repositories import (
    FolloweeImportRepository,
)
from instaloader_webui.db.library_repositories import (
    JobRepository,
    JobSnapshot,
    LibraryRepository,
    SettingsRepository,
)
from instaloader_webui.db.schema import initialize_database
from instaloader_webui.instagram.cooldown import InstagramCooldownStore
from instaloader_webui.instagram.profile_lookup import ProfileLookupResolver
from instaloader_webui.instagram.profile_sync_checkpoints import (
    ProfileSyncCheckpointRepository,
)
from instaloader_webui.instagram.session_store import InstagramSessionStore
from instaloader_webui.instagram.worker_runtime import WorkerInstaloaderRuntime
from instaloader_webui.services.job_runner import JobRunner, enqueue_due_profile_syncs

_PROFILE_LOOKUP_LOGGER = logging.getLogger("instaloader_webui.instagram.profile_lookup")
_INSTAGRAM_JOB_TYPES = frozenset({"profile_sync", "single_media", "followee_discovery"})


def _claim_next_job(
    *,
    jobs: JobRepository,
    cooldowns: InstagramCooldownStore,
    now: datetime,
) -> JobSnapshot | None:
    excluded_types = (
        _INSTAGRAM_JOB_TYPES if cooldowns.status(now).until is not None else ()
    )
    return jobs.claim_next(now, excluded_types=excluded_types)


def main() -> None:
    """Recover work and continuously claim public-library jobs."""
    settings = Settings()
    settings.data_root.mkdir(parents=True, exist_ok=True)
    settings.media_root.mkdir(parents=True, exist_ok=True)
    settings.jobs_root.mkdir(parents=True, exist_ok=True)
    initialize_database(settings)

    engine = build_engine(settings.database_path)
    session_factory = build_session_factory(engine)
    library = LibraryRepository(session_factory)
    jobs = JobRepository(session_factory)
    followee_imports = FolloweeImportRepository(session_factory)
    scheduling = SettingsRepository(session_factory)
    checkpoints = ProfileSyncCheckpointRepository(session_factory)
    app_secret = load_or_create_app_secret(settings.data_root)
    instagram_sessions = InstagramSessionStore(settings.data_root, app_secret)
    cooldowns = InstagramCooldownStore(settings.data_root)
    loader_runtime = WorkerInstaloaderRuntime(instagram_sessions)
    profile_lookup_resolver = ProfileLookupResolver(
        settings.instagram_profile_lookup_mode,
        _PROFILE_LOOKUP_LOGGER,
    )
    runner = JobRunner(
        data_root=settings.data_root,
        media_root=settings.media_root,
        jobs_root=settings.jobs_root,
        library=library,
        jobs=jobs,
        followee_imports=followee_imports,
        loader_runtime=loader_runtime,
        profile_lookup_resolver=profile_lookup_resolver,
        cooldowns=cooldowns,
        checkpoints=checkpoints,
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
            job = _claim_next_job(
                jobs=jobs,
                cooldowns=cooldowns,
                now=datetime.now(UTC),
            )
            if job is None:
                time.sleep(2)
                continue
            runner.run(job)
    finally:
        try:
            loader_runtime.close()
        finally:
            engine.dispose()
