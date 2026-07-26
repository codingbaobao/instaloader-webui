from pathlib import Path

import pytest

from instaloader_webui.config import Settings
from instaloader_webui.db.engine import build_engine, build_session_factory


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
