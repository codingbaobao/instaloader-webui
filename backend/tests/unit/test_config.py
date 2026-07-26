from pathlib import Path

import pytest
from pydantic import ValidationError

from instaloader_webui.config import Settings


def test_settings_keep_runtime_paths_under_data_root(tmp_path: Path) -> None:
    settings = Settings(
        data_root=tmp_path,
        app_secret_key="a" * 32,
        admin_username="owner",
        admin_password="correct-horse-battery-staple",
    )

    assert settings.database_path == tmp_path / "database" / "app.sqlite3"
    assert settings.static_root == Path("/app/static")


def test_settings_reject_short_app_secret_without_echoing_it(tmp_path: Path) -> None:
    short_secret = "unique-short-secret"

    with pytest.raises(ValidationError) as caught:
        Settings(
            data_root=tmp_path,
            app_secret_key=short_secret,
            admin_username="owner",
        )

    assert short_secret not in str(caught.value)
    assert "32" in str(caught.value)


def test_settings_accept_exactly_thirty_two_character_app_secret(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_root=tmp_path,
        app_secret_key="a" * 32,
        admin_username="owner",
    )

    assert len(settings.app_secret_key.get_secret_value()) == 32
