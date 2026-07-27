"""Encrypted, file-backed storage for the global Instagram browser session."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any
import unicodedata

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from instaloader_webui.auth.app_secret import AppSecret
from instaloader_webui.instagram.cookie_file import (
    REQUIRED_COOKIE_NAMES,
    InstagramCookie,
)


INSTAGRAM_SESSION_FILENAME = "instagram_session.enc"
INSTAGRAM_SESSION_VERSION = 1
SECRET_DIRECTORY_MODE = 0o700
SECRET_FILE_MODE = 0o600


class InstagramSessionStoreError(RuntimeError):
    """Raised when encrypted Instagram session storage cannot be used safely."""


@dataclass(frozen=True, slots=True)
class InstagramSessionSnapshot:
    username: str
    cookies: tuple[InstagramCookie, ...] = field(repr=False)
    imported_at: datetime
    last_validated_at: datetime


@dataclass(frozen=True, slots=True)
class InstagramSessionStatus:
    configured: bool
    username: str | None
    imported_at: datetime | None
    last_validated_at: datetime | None


class InstagramSessionStore:
    """Own the encrypted session document without exposing Cookie values."""

    def __init__(self, data_root: Path, app_secret: AppSecret) -> None:
        self._secret_directory = data_root / "secrets"
        self._session_path = self._secret_directory / INSTAGRAM_SESSION_FILENAME
        derived_key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"instaloader-webui:instagram-session:v1",
        ).derive(app_secret.value.encode("utf-8"))
        self._fernet = Fernet(base64.urlsafe_b64encode(derived_key))

    def load(self) -> InstagramSessionSnapshot | None:
        """Load and validate the current encrypted snapshot, if configured."""
        encrypted = self._read_encrypted()
        if encrypted is None:
            return None
        try:
            plaintext = self._fernet.decrypt(encrypted)
        except (InvalidToken, ValueError, TypeError):
            raise InstagramSessionStoreError("Instagram session storage is invalid.") from None

        document = _load_json_document(plaintext)
        return _snapshot_from_document(document)

    def status(self) -> InstagramSessionStatus:
        """Return configuration metadata without returning Cookie information."""
        snapshot = self.load()
        if snapshot is None:
            return InstagramSessionStatus(
                configured=False,
                username=None,
                imported_at=None,
                last_validated_at=None,
            )
        return InstagramSessionStatus(
            configured=True,
            username=snapshot.username,
            imported_at=snapshot.imported_at,
            last_validated_at=snapshot.last_validated_at,
        )

    def replace(self, snapshot: InstagramSessionSnapshot) -> InstagramSessionStatus:
        """Encrypt and atomically replace the session after complete validation."""
        document = _document_from_snapshot(snapshot)
        try:
            encrypted = self._fernet.encrypt(
                json.dumps(
                    document,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
        except (TypeError, ValueError):
            raise InstagramSessionStoreError("Instagram session data is invalid.") from None

        self._ensure_secret_directory()
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".instagram_session.",
            dir=self._secret_directory,
        )
        temporary_path = Path(temporary_name)
        try:
            _set_private_file_mode(descriptor)
            with os.fdopen(descriptor, "wb") as target:
                target.write(encrypted)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary_path, self._session_path)
            _fsync_directory(self._secret_directory)
        except OSError:
            raise InstagramSessionStoreError("Instagram session storage could not be updated.") from None
        finally:
            temporary_path.unlink(missing_ok=True)

        return InstagramSessionStatus(
            configured=True,
            username=snapshot.username,
            imported_at=snapshot.imported_at,
            last_validated_at=snapshot.last_validated_at,
        )

    def delete(self) -> None:
        """Remove the encrypted session if present, without resolving symlinks."""
        try:
            self._session_path.unlink(missing_ok=True)
        except OSError:
            raise InstagramSessionStoreError("Instagram session storage could not be removed.") from None

    def _ensure_secret_directory(self) -> None:
        try:
            self._secret_directory.mkdir(
                mode=SECRET_DIRECTORY_MODE,
                parents=True,
                exist_ok=True,
            )
            if os.name == "posix":
                self._secret_directory.chmod(SECRET_DIRECTORY_MODE)
        except OSError:
            raise InstagramSessionStoreError("Instagram session storage could not be prepared.") from None

    def _read_encrypted(self) -> bytes | None:
        try:
            metadata = self._session_path.lstat()
        except FileNotFoundError:
            return None
        except OSError:
            raise InstagramSessionStoreError("Instagram session storage is unavailable.") from None
        if stat.S_ISLNK(metadata.st_mode):
            raise InstagramSessionStoreError("Instagram session storage is invalid.")

        try:
            descriptor = os.open(
                self._session_path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
        except FileNotFoundError:
            return None
        except OSError:
            raise InstagramSessionStoreError("Instagram session storage is unavailable.") from None

        try:
            with os.fdopen(descriptor, "rb") as source:
                metadata = os.fstat(source.fileno())
                if not stat.S_ISREG(metadata.st_mode):
                    raise InstagramSessionStoreError(
                        "Instagram session storage is invalid."
                    )
                return source.read()
        except InstagramSessionStoreError:
            raise
        except OSError:
            raise InstagramSessionStoreError("Instagram session storage is unavailable.") from None


def _set_private_file_mode(descriptor: int) -> None:
    try:
        os.fchmod(descriptor, SECRET_FILE_MODE)
    except OSError:
        if os.name == "posix":
            raise


def _fsync_directory(directory: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _load_json_document(plaintext: bytes) -> dict[str, Any]:
    try:
        decoded = plaintext.decode("utf-8")
        document = json.loads(decoded, object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise InstagramSessionStoreError("Instagram session storage is invalid.") from None
    if not isinstance(document, dict):
        raise InstagramSessionStoreError("Instagram session storage is invalid.")
    return document


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate JSON key")
        document[key] = value
    return document


def _snapshot_from_document(document: dict[str, Any]) -> InstagramSessionSnapshot:
    _require_exact_keys(
        document,
        {"version", "username", "cookies", "imported_at", "last_validated_at"},
    )
    if document["version"] != INSTAGRAM_SESSION_VERSION:
        raise InstagramSessionStoreError("Instagram session storage is invalid.")
    username = _require_safe_nonempty_string(document["username"])
    cookies_value = document["cookies"]
    if not isinstance(cookies_value, list):
        raise InstagramSessionStoreError("Instagram session storage is invalid.")
    cookies = tuple(_cookie_from_document(cookie) for cookie in cookies_value)
    _validate_cookies(cookies)
    return InstagramSessionSnapshot(
        username=username,
        cookies=cookies,
        imported_at=_datetime_from_document(document["imported_at"]),
        last_validated_at=_datetime_from_document(document["last_validated_at"]),
    )


def _document_from_snapshot(snapshot: InstagramSessionSnapshot) -> dict[str, Any]:
    if not isinstance(snapshot, InstagramSessionSnapshot):
        raise InstagramSessionStoreError("Instagram session data is invalid.")
    username = _require_safe_nonempty_string(snapshot.username)
    if not isinstance(snapshot.cookies, tuple):
        raise InstagramSessionStoreError("Instagram session data is invalid.")
    _validate_cookies(snapshot.cookies)
    imported_at = _require_aware_datetime(snapshot.imported_at)
    last_validated_at = _require_aware_datetime(snapshot.last_validated_at)
    return {
        "version": INSTAGRAM_SESSION_VERSION,
        "username": username,
        "cookies": [_cookie_to_document(cookie) for cookie in snapshot.cookies],
        "imported_at": imported_at.isoformat(),
        "last_validated_at": last_validated_at.isoformat(),
    }


def _cookie_from_document(value: Any) -> InstagramCookie:
    if not isinstance(value, dict):
        raise InstagramSessionStoreError("Instagram session storage is invalid.")
    _require_exact_keys(value, {"domain", "path", "secure", "expires_at", "name", "value"})
    secure = value["secure"]
    expires_at = value["expires_at"]
    if not isinstance(secure, bool) or not _is_integer(expires_at) or expires_at < 0:
        raise InstagramSessionStoreError("Instagram session storage is invalid.")
    return InstagramCookie(
        domain=_require_safe_string(value["domain"]),
        path=_require_safe_string(value["path"]),
        secure=secure,
        expires_at=expires_at,
        name=_require_safe_string(value["name"]),
        value=_require_safe_string(value["value"]),
    )


def _cookie_to_document(cookie: InstagramCookie) -> dict[str, Any]:
    return {
        "domain": cookie.domain,
        "path": cookie.path,
        "secure": cookie.secure,
        "expires_at": cookie.expires_at,
        "name": cookie.name,
        "value": cookie.value,
    }


def _validate_cookies(cookies: tuple[InstagramCookie, ...]) -> None:
    if not cookies:
        raise InstagramSessionStoreError("Instagram session data is invalid.")
    values_by_name: dict[str, str] = {}
    required_names: set[str] = set()
    for cookie in cookies:
        if not isinstance(cookie, InstagramCookie):
            raise InstagramSessionStoreError("Instagram session data is invalid.")
        if (
            not _is_safe_nonempty_string(cookie.domain)
            or not _is_safe_nonempty_string(cookie.path)
            or not _is_instagram_domain(cookie.domain)
            or not cookie.path.startswith("/")
            or not _is_integer(cookie.expires_at)
            or cookie.expires_at < 0
            or not isinstance(cookie.secure, bool)
            or not _is_safe_nonempty_string(cookie.name)
            or not _is_safe_string(cookie.value)
        ):
            raise InstagramSessionStoreError("Instagram session data is invalid.")
        if cookie.name in REQUIRED_COOKIE_NAMES:
            if not cookie.value:
                raise InstagramSessionStoreError("Instagram session data is invalid.")
            required_names.add(cookie.name)
        previous_value = values_by_name.get(cookie.name)
        if cookie.name in values_by_name and previous_value != cookie.value:
            raise InstagramSessionStoreError("Instagram session data is invalid.")
        values_by_name[cookie.name] = cookie.value
    if required_names != REQUIRED_COOKIE_NAMES:
        raise InstagramSessionStoreError("Instagram session data is invalid.")


def _datetime_from_document(value: Any) -> datetime:
    if not isinstance(value, str):
        raise InstagramSessionStoreError("Instagram session storage is invalid.")
    try:
        return _require_aware_datetime(datetime.fromisoformat(value))
    except (TypeError, ValueError):
        raise InstagramSessionStoreError("Instagram session storage is invalid.") from None


def _require_aware_datetime(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InstagramSessionStoreError("Instagram session data is invalid.")
    return value


def _require_exact_keys(value: dict[str, Any], expected_keys: set[str]) -> None:
    if set(value) != expected_keys:
        raise InstagramSessionStoreError("Instagram session storage is invalid.")


def _require_safe_nonempty_string(value: Any) -> str:
    if not _is_safe_nonempty_string(value):
        raise InstagramSessionStoreError("Instagram session storage is invalid.")
    return value


def _require_safe_string(value: Any) -> str:
    if not _is_safe_string(value):
        raise InstagramSessionStoreError("Instagram session storage is invalid.")
    return value


def _is_safe_nonempty_string(value: Any) -> bool:
    return _is_safe_string(value) and bool(value)


def _is_safe_string(value: Any) -> bool:
    return isinstance(value, str) and not any(
        unicodedata.category(character) == "Cc" for character in value
    )


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_instagram_domain(domain: str) -> bool:
    if not isinstance(domain, str):
        return False
    normalized = domain.casefold()
    return normalized in {"instagram.com", ".instagram.com"} or normalized.endswith(
        ".instagram.com"
    )
