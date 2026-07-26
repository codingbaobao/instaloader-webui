import re
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from instaloader_webui.auth.passwords import PasswordService
from instaloader_webui.config import (
    ADMIN_USERNAME_PATTERN,
    MAXIMUM_BOOTSTRAP_PASSWORD_FILE_BYTES,
    MINIMUM_ADMIN_PASSWORD_LENGTH,
    Settings,
)
from instaloader_webui.db.repositories import AdminRepository, AdminSnapshot


def resolve_bootstrap_password(settings: Settings) -> str:
    """Return the configured bootstrap secret without persisting or logging it."""
    has_inline_password = settings.admin_password is not None
    has_password_file = settings.admin_password_file is not None
    if has_inline_password == has_password_file:
        raise ValueError("configure exactly one bootstrap password source")

    if has_inline_password:
        return settings.admin_password.get_secret_value()  # type: ignore[union-attr]

    password_file = settings.admin_password_file
    assert password_file is not None
    with password_file.open("rb") as source:
        password_bytes = source.read(MAXIMUM_BOOTSTRAP_PASSWORD_FILE_BYTES + 1)
    if len(password_bytes) > MAXIMUM_BOOTSTRAP_PASSWORD_FILE_BYTES:
        raise ValueError("bootstrap password file must not exceed 4 KiB")

    try:
        password = password_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("bootstrap password file must be UTF-8") from error
    if password.endswith("\r\n"):
        return password[:-2]
    if password.endswith(("\r", "\n")):
        return password[:-1]
    return password


def _validate_bootstrap_credentials(settings: Settings) -> str:
    if re.fullmatch(ADMIN_USERNAME_PATTERN, settings.admin_username) is None:
        raise ValueError("bootstrap username has an invalid format")

    password = resolve_bootstrap_password(settings)
    if len(password) < MINIMUM_ADMIN_PASSWORD_LENGTH:
        raise ValueError("bootstrap password must be at least 16 characters")
    return password


def bootstrap_admin(
    session_factory: sessionmaker[Session],
    settings: Settings,
    passwords: PasswordService | None = None,
) -> AdminSnapshot:
    """Create the sole bootstrap administrator once, or return its snapshot."""
    admins = AdminRepository(session_factory)
    existing = admins.get_single()
    if existing is not None:
        return existing

    password = _validate_bootstrap_credentials(settings)
    password_service = passwords or PasswordService()
    try:
        return admins.create(
            username=settings.admin_username,
            password_hash=password_service.hash(password),
            must_change_password=True,
            now=datetime.now(UTC),
        )
    except IntegrityError:
        existing = admins.get_single()
        if existing is not None:
            return existing
        raise
