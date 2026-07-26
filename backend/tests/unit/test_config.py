from pathlib import Path

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
