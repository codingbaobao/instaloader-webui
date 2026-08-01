import logging

import pytest

from instaloader_webui import worker as worker_module
from instaloader_webui.config import Settings


@pytest.mark.parametrize("mode", ["fallback", "legacy"])
def test_worker_builds_one_shared_resolver_from_exact_setting(
    mode: str,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Break caught: hard-coding a lookup mode or constructing the resolver per
    # adapter would ignore startup configuration and split resolver identity.
    settings = test_settings.model_copy(
        update={"instagram_profile_lookup_mode": mode}
    )
    resolver = object()
    resolver_calls: list[tuple[str, logging.Logger]] = []
    runner_kwargs: list[dict[str, object]] = []
    startup_events: list[str] = []

    class StopWorker(Exception):
        pass

    def profile_lookup_resolver_factory(
        configured_mode: str,
        logger: logging.Logger,
    ) -> object:
        resolver_calls.append((configured_mode, logger))
        return resolver

    def job_runner_factory(**kwargs: object) -> object:
        runner_kwargs.append(kwargs)
        raise StopWorker

    monkeypatch.setattr(worker_module, "Settings", lambda: settings)
    monkeypatch.setattr(
        worker_module,
        "initialize_database",
        lambda _settings: startup_events.append("initialize"),
        raising=False,
    )

    def build_engine(_path):
        startup_events.append("engine")
        assert startup_events == ["initialize", "engine"]
        return object()

    monkeypatch.setattr(worker_module, "build_engine", build_engine)
    monkeypatch.setattr(
        worker_module,
        "build_session_factory",
        lambda _engine: object(),
    )
    for repository_name in (
        "LibraryRepository",
        "JobRepository",
        "FolloweeImportRepository",
        "SettingsRepository",
    ):
        monkeypatch.setattr(
            worker_module,
            repository_name,
            lambda _factory: object(),
        )
    monkeypatch.setattr(
        worker_module,
        "load_or_create_app_secret",
        lambda _data_root: object(),
    )
    monkeypatch.setattr(
        worker_module,
        "InstagramSessionStore",
        lambda _data_root, _secret: object(),
    )
    monkeypatch.setattr(
        worker_module,
        "WorkerInstaloaderRuntime",
        lambda _sessions: object(),
    )
    monkeypatch.setattr(
        worker_module,
        "ProfileLookupResolver",
        profile_lookup_resolver_factory,
        raising=False,
    )
    monkeypatch.setattr(worker_module, "JobRunner", job_runner_factory)

    with pytest.raises(StopWorker):
        worker_module.main()

    assert len(resolver_calls) == 1
    configured_mode, logger = resolver_calls[0]
    assert configured_mode == mode
    assert logger.name == "instaloader_webui.instagram.profile_lookup"
    assert startup_events == ["initialize", "engine"]
    assert len(runner_kwargs) == 1
    assert runner_kwargs[0]["profile_lookup_resolver"] is resolver
