from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import func, select

from instaloader_webui.config import Settings
from instaloader_webui.db.models import AdminUser
from instaloader_webui.db.schema import initialize_database
from instaloader_webui.services.admin_bootstrap import (
    bootstrap_admin,
    resolve_bootstrap_password,
)


def count_admins(session_factory) -> int:
    with session_factory() as session:
        return session.scalar(select(func.count()).select_from(AdminUser)) or 0


def test_bootstrap_creates_exactly_one_forced_change_admin(
    session_factory, test_settings
) -> None:
    initialize_database(test_settings)

    first = bootstrap_admin(session_factory, test_settings)
    second = bootstrap_admin(session_factory, test_settings)

    assert first.id == second.id
    assert first.username == "owner"
    assert first.must_change_password is True
    assert count_admins(session_factory) == 1


def test_bootstrap_ignores_invalid_password_sources_after_admin_exists(
    session_factory, test_settings
) -> None:
    initialize_database(test_settings)
    created = bootstrap_admin(session_factory, test_settings)
    settings_without_a_password = Settings(
        data_root=test_settings.data_root,
        admin_username="owner",
    )

    existing = bootstrap_admin(session_factory, settings_without_a_password)

    assert existing == created


def test_bootstrap_race_creates_only_one_admin_for_different_usernames(
    session_factory, tmp_path: Path
) -> None:
    initialize_database(
        Settings(
            data_root=tmp_path,
            admin_username="owner-one",
            admin_password="correct-horse-battery-staple",
        )
    )
    first_settings = Settings(
        data_root=tmp_path,
        admin_username="owner-one",
        admin_password="correct-horse-battery-staple",
    )
    second_settings = Settings(
        data_root=tmp_path,
        admin_username="owner-two",
        admin_password="correct-horse-battery-staple",
    )
    hash_barrier = Barrier(2)

    class BarrierPasswordService:
        def hash(self, password: str) -> str:
            hash_barrier.wait()
            return f"hash:{password}"

    password_services = (BarrierPasswordService(), BarrierPasswordService())
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(
                bootstrap_admin,
                session_factory,
                first_settings,
                password_services[0],
            ),
            executor.submit(
                bootstrap_admin,
                session_factory,
                second_settings,
                password_services[1],
            ),
        )
        first, second = (future.result() for future in futures)

    assert first.id == second.id
    assert count_admins(session_factory) == 1


@pytest.mark.parametrize(
    ("inline_password", "password_file"),
    [
        (None, None),
        ("correct-horse-battery-staple", Path("bootstrap-password.txt")),
    ],
)
def test_resolve_bootstrap_password_rejects_missing_or_conflicting_sources(
    tmp_path: Path, inline_password: str | None, password_file: Path | None
) -> None:
    if password_file is not None:
        password_file = tmp_path / password_file
        password_file.write_text("correct-horse-battery-staple", encoding="utf-8")
    settings = Settings(
        data_root=tmp_path,
        admin_username="owner",
        admin_password=inline_password,
        admin_password_file=password_file,
    )

    with pytest.raises(ValueError, match="exactly one"):
        resolve_bootstrap_password(settings)


def test_resolve_bootstrap_password_reads_secret_file_and_strips_one_newline(
    tmp_path: Path,
) -> None:
    password_file = tmp_path / "bootstrap-password.txt"
    password_file.write_bytes(b"correct-horse-battery-staple\r\n")
    settings = Settings(
        data_root=tmp_path,
        admin_username="owner",
        admin_password_file=password_file,
    )

    password = resolve_bootstrap_password(settings)

    assert password == "correct-horse-battery-staple"


def test_resolve_bootstrap_password_accepts_files_larger_than_four_kib(
    tmp_path: Path,
) -> None:
    password_file = tmp_path / "bootstrap-password.txt"
    password_file.write_bytes(b"a" * 4097)
    settings = Settings(
        data_root=tmp_path,
        admin_username="owner",
        admin_password_file=password_file,
    )

    assert resolve_bootstrap_password(settings) == "a" * 4097


def test_bootstrap_accepts_long_unicode_password(
    session_factory, tmp_path: Path
) -> None:
    settings = Settings(
        data_root=tmp_path,
        admin_username="owner",
        admin_password="界" * 342,
    )
    initialize_database(settings)

    created = bootstrap_admin(session_factory, settings)

    assert created.username == "owner"


def test_bootstrap_accepts_empty_password(
    session_factory, tmp_path: Path
) -> None:
    settings = Settings(
        data_root=tmp_path,
        admin_username="owner",
        admin_password="",
    )
    initialize_database(settings)

    created = bootstrap_admin(session_factory, settings)

    assert created.username == "owner"


def test_bootstrap_rejects_username_outside_the_allowed_pattern(
    session_factory, tmp_path: Path
) -> None:
    settings = Settings(
        data_root=tmp_path,
        admin_username="not an owner",
        admin_password="correct-horse-battery-staple",
    )
    initialize_database(settings)

    with pytest.raises(ValueError, match="username"):
        bootstrap_admin(session_factory, settings)
