from pathlib import Path

import pytest

from instaloader_webui.config import Settings


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_root=tmp_path,
        app_secret_key="a" * 32,
        admin_username="owner",
        admin_password="correct-horse-battery-staple",
    )
