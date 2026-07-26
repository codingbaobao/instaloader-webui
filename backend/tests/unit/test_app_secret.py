import os
import stat

import pytest

import instaloader_webui.auth.app_secret as app_secret_module
from instaloader_webui.auth.app_secret import load_or_create_app_secret


def test_app_secret_is_generated_once_and_reused(tmp_path) -> None:
    first = load_or_create_app_secret(tmp_path)
    second = load_or_create_app_secret(tmp_path)
    secret_path = tmp_path / "secrets" / "app_secret_key"

    assert first == second
    assert first.value
    assert first.value not in repr(first)
    assert secret_path.read_text(encoding="utf-8") == first.value
    if os.name == "posix":
        assert stat.S_IMODE(secret_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(secret_path.parent.stat().st_mode) == 0o700


def test_empty_persisted_app_secret_is_rejected(tmp_path) -> None:
    secret_directory = tmp_path / "secrets"
    secret_directory.mkdir()
    (secret_directory / "app_secret_key").write_text("", encoding="utf-8")

    with pytest.raises(RuntimeError, match="internal application secret is empty"):
        load_or_create_app_secret(tmp_path)


def test_app_secret_is_fully_written_before_it_is_published(
    monkeypatch,
    tmp_path,
) -> None:
    original_link = app_secret_module.os.link

    def assert_complete_then_link(source, destination) -> None:
        assert source.read_text(encoding="utf-8")
        original_link(source, destination)

    monkeypatch.setattr(app_secret_module.os, "link", assert_complete_then_link)

    generated = load_or_create_app_secret(tmp_path)

    assert generated.value
