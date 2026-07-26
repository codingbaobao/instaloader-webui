from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from instaloader_webui.auth.throttle import LoginThrottle
from instaloader_webui.config import Settings
from instaloader_webui.db.engine import build_engine, build_session_factory
from instaloader_webui.db.migrations import run_migrations
from instaloader_webui.db.repositories import LoginFailureRepository
from instaloader_webui.main import create_app


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_root=tmp_path,
        app_secret_key="a" * 32,
        admin_username="owner",
        admin_password="correct-horse-battery-staple",
    )


@pytest.fixture
def secure_test_settings(test_settings: Settings) -> Settings:
    return test_settings.model_copy(update={"session_cookie_secure": True})


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


@pytest.fixture
def client(test_settings: Settings):
    with TestClient(create_app(test_settings)) as test_client:
        yield test_client


@pytest.fixture
def authenticated_client(test_settings: Settings):
    with TestClient(create_app(test_settings)) as test_client:
        response = test_client.post(
            "/api/auth/login",
            json={
                "username": "owner",
                "password": "correct-horse-battery-staple",
            },
        )
        assert response.status_code == 200
        yield test_client


@pytest.fixture
def second_authenticated_client(test_settings: Settings):
    with TestClient(create_app(test_settings)) as test_client:
        response = test_client.post(
            "/api/auth/login",
            json={
                "username": "owner",
                "password": "correct-horse-battery-staple",
            },
        )
        assert response.status_code == 200
        yield test_client
