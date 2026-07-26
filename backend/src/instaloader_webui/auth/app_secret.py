import os
import secrets
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

APP_SECRET_DIRECTORY_MODE = 0o700
APP_SECRET_FILE_MODE = 0o600
APP_SECRET_TOKEN_BYTES = 48


@dataclass(frozen=True, slots=True)
class AppSecret:
    value: str = field(repr=False)


def load_or_create_app_secret(data_root: Path) -> AppSecret:
    secret_directory = data_root / "secrets"
    secret_path = secret_directory / "app_secret_key"
    secret_directory.mkdir(
        mode=APP_SECRET_DIRECTORY_MODE,
        parents=True,
        exist_ok=True,
    )
    secret_directory.chmod(APP_SECRET_DIRECTORY_MODE)

    if secret_path.exists():
        return _read_app_secret(secret_path)

    generated = secrets.token_urlsafe(APP_SECRET_TOKEN_BYTES)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".app_secret_key.",
        dir=secret_directory,
    )
    temporary_path = Path(temporary_name)
    try:
        if os.name == "posix":
            os.fchmod(descriptor, APP_SECRET_FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as target:
            target.write(generated)
            target.flush()
            os.fsync(target.fileno())

        try:
            os.link(temporary_path, secret_path)
        except FileExistsError:
            return _read_app_secret(secret_path)
        return AppSecret(generated)
    finally:
        temporary_path.unlink(missing_ok=True)


def _read_app_secret(secret_path: Path) -> AppSecret:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(secret_path, flags)
    with os.fdopen(descriptor, "r", encoding="utf-8") as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("the internal application secret is not a regular file")
        if os.name == "posix":
            os.fchmod(source.fileno(), APP_SECRET_FILE_MODE)
        value = source.read()
    if not value:
        raise RuntimeError("the internal application secret is empty")
    return AppSecret(value)
