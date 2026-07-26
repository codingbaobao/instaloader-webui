from pathlib import Path

import pytest

from instaloader_webui.auth.throttle import LoginThrottle
from instaloader_webui.config import Settings
from instaloader_webui.db.engine import build_engine, build_session_factory
from instaloader_webui.db.migrations import run_migrations
from instaloader_webui.db.repositories import LoginFailureRepository


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_root=tmp_path,
        app_secret_key="a" * 32,
        admin_username="owner",
        admin_password="correct-horse-battery-staple",
    )


@pytest.fixture
def engine(test_settings: Settings):
    return build_engine(test_settings.database_path)


@pytest.fixture
def session_factory(engine):
    return build_session_factory(engine)


@pytest.fixture
def login_failure_repository(session_factory, test_settings: Settings) -> LoginFailureRepository:
    run_migrations(test_settings)
    return LoginFailureRepository(session_factory)


@pytest.fixture
def throttle(
    login_failure_repository: LoginFailureRepository, test_settings: Settings
) -> LoginThrottle:
    return LoginThrottle(
        repository=login_failure_repository,
        hmac_secret=test_settings.app_secret_key.get_secret_value(),
    )
