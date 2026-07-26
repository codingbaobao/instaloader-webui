from pathlib import Path

from instaloader_webui.config import Settings


def test_settings_keep_runtime_paths_under_data_root(tmp_path: Path) -> None:
    settings = Settings(
        data_root=tmp_path,
        admin_username="owner",
        admin_password="",
    )

    assert settings.database_path == tmp_path / "database" / "app.sqlite3"
    assert settings.static_root == Path("/app/static")
    assert settings.admin_password is not None
    assert settings.admin_password.get_secret_value() == ""


def test_settings_do_not_expose_an_app_secret_variable() -> None:
    assert "app_secret_key" not in Settings.model_fields
