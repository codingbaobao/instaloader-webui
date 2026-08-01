from pathlib import Path

import pytest
from pydantic import ValidationError

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


def test_profile_lookup_mode_defaults_to_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("IW_INSTAGRAM_PROFILE_LOOKUP_MODE", raising=False)

    settings = Settings(admin_username="owner")

    assert settings.instagram_profile_lookup_mode == "fallback"


@pytest.mark.parametrize("mode", ["native", "fallback", "legacy"])
def test_profile_lookup_mode_accepts_exact_environment_values(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    monkeypatch.setenv("IW_INSTAGRAM_PROFILE_LOOKUP_MODE", mode)

    settings = Settings(admin_username="owner")

    assert settings.instagram_profile_lookup_mode == mode


@pytest.mark.parametrize(
    "mode",
    ["", " fallback", "fallback ", "Fallback", "automatic"],
)
def test_profile_lookup_mode_rejects_non_exact_environment_values(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    monkeypatch.setenv("IW_INSTAGRAM_PROFILE_LOOKUP_MODE", mode)

    with pytest.raises(ValidationError):
        Settings(admin_username="owner")


def test_profile_lookup_mode_hides_invalid_environment_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_mode = "sensitive-invalid-mode"
    monkeypatch.setenv("IW_INSTAGRAM_PROFILE_LOOKUP_MODE", invalid_mode)

    with pytest.raises(ValidationError) as exc_info:
        Settings(admin_username="owner")

    assert invalid_mode not in str(exc_info.value)
