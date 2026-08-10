"""Persist account/IP-wide Instagram rate-limit cooldown state."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_STATE_DIRECTORY_NAME = "state"
_STATE_FILENAME = "instagram_cooldown.json"
_STATE_VERSION = 1
_STATE_DIRECTORY_MODE = 0o700
_STATE_FILE_MODE = 0o600
_MAX_STATE_BYTES = 4096
_BASE_COOLDOWN = timedelta(minutes=30)
_MAX_COOLDOWN = timedelta(hours=6)
_EXPECTED_KEYS = frozenset({"version", "until", "consecutive_rate_limits"})


class InstagramCooldownStoreError(RuntimeError):
    """Signal cooldown state that cannot be safely read or persisted."""


@dataclass(frozen=True, slots=True)
class InstagramCooldownStatus:
    """Current active deadline and consecutive 429 count."""

    until: datetime | None
    consecutive_rate_limits: int


@dataclass(frozen=True, slots=True)
class _PersistedCooldown:
    until: datetime
    consecutive_rate_limits: int


class InstagramCooldownStore:
    """Atomically persist global Instagram cooldown state without SQLite changes."""

    def __init__(self, data_root: Path) -> None:
        self._state_directory = data_root / _STATE_DIRECTORY_NAME
        self._state_path = self._state_directory / _STATE_FILENAME

    def status(self, now: datetime) -> InstagramCooldownStatus:
        """Return active cooldown state while preserving expired strike counts."""
        persisted = self._load()
        if persisted is None:
            return InstagramCooldownStatus(None, 0)
        current_time = _as_utc(now)
        return InstagramCooldownStatus(
            until=(persisted.until if persisted.until > current_time else None),
            consecutive_rate_limits=persisted.consecutive_rate_limits,
        )

    def record_rate_limit(self, now: datetime) -> InstagramCooldownStatus:
        """Increase exponential backoff and persist its new deadline."""
        current_time = _as_utc(now)
        persisted = self._load()
        consecutive = 1 if persisted is None else persisted.consecutive_rate_limits + 1
        multiplier = 2 ** (consecutive - 1)
        delay = min(_BASE_COOLDOWN * multiplier, _MAX_COOLDOWN)
        status = InstagramCooldownStatus(
            until=current_time + delay,
            consecutive_rate_limits=consecutive,
        )
        self._write(status)
        return status

    def record_success(self) -> None:
        """Reset cooldown escalation after a successful Instagram job."""
        try:
            self._state_path.unlink(missing_ok=True)
        except OSError as error:
            raise InstagramCooldownStoreError(
                "Instagram cooldown state could not be reset."
            ) from error
        if self._state_directory.exists():
            self._sync_directory()

    def _load(self) -> _PersistedCooldown | None:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self._state_path, flags)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise InstagramCooldownStoreError(
                "Instagram cooldown state could not be read."
            ) from error

        try:
            with os.fdopen(descriptor, "rb") as source:
                metadata = os.fstat(source.fileno())
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_size > _MAX_STATE_BYTES
                ):
                    raise InstagramCooldownStoreError(
                        "Instagram cooldown state is invalid."
                    )
                if os.name == "posix":
                    os.fchmod(source.fileno(), _STATE_FILE_MODE)
                payload = source.read(_MAX_STATE_BYTES + 1)
        except InstagramCooldownStoreError:
            raise
        except OSError as error:
            raise InstagramCooldownStoreError(
                "Instagram cooldown state could not be read."
            ) from error
        return _decode_state(payload)

    def _write(self, status: InstagramCooldownStatus) -> None:
        if status.until is None or status.consecutive_rate_limits < 1:
            raise ValueError("Active Instagram cooldown state is required.")
        self._ensure_state_directory()
        document = {
            "version": _STATE_VERSION,
            "until": status.until.astimezone(UTC).isoformat(),
            "consecutive_rate_limits": status.consecutive_rate_limits,
        }
        payload = json.dumps(
            document,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{_STATE_FILENAME}.",
            dir=self._state_directory,
        )
        temporary_path = Path(temporary_name)
        try:
            if os.name == "posix":
                os.fchmod(descriptor, _STATE_FILE_MODE)
            with os.fdopen(descriptor, "wb") as target:
                target.write(payload)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary_path, self._state_path)
            if os.name == "posix":
                self._state_path.chmod(_STATE_FILE_MODE)
            self._sync_directory()
        except OSError as error:
            raise InstagramCooldownStoreError(
                "Instagram cooldown state could not be persisted."
            ) from error
        finally:
            temporary_path.unlink(missing_ok=True)

    def _ensure_state_directory(self) -> None:
        try:
            self._state_directory.mkdir(
                mode=_STATE_DIRECTORY_MODE,
                parents=True,
                exist_ok=True,
            )
            if self._state_directory.is_symlink() or not self._state_directory.is_dir():
                raise InstagramCooldownStoreError(
                    "Instagram cooldown state directory is invalid."
                )
            if os.name == "posix":
                self._state_directory.chmod(_STATE_DIRECTORY_MODE)
        except InstagramCooldownStoreError:
            raise
        except OSError as error:
            raise InstagramCooldownStoreError(
                "Instagram cooldown state directory could not be prepared."
            ) from error

    def _sync_directory(self) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            descriptor = os.open(self._state_directory, flags)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as error:
            raise InstagramCooldownStoreError(
                "Instagram cooldown state directory could not be synchronized."
            ) from error


def _decode_state(payload: bytes) -> _PersistedCooldown:
    try:
        document: Any = json.loads(payload)
        if not isinstance(document, dict) or set(document) != _EXPECTED_KEYS:
            raise ValueError
        version = document["version"]
        raw_until = document["until"]
        consecutive = document["consecutive_rate_limits"]
        if (
            type(version) is not int
            or version != _STATE_VERSION
            or not isinstance(raw_until, str)
            or type(consecutive) is not int
            or consecutive < 1
        ):
            raise ValueError
        until = datetime.fromisoformat(raw_until)
        if until.tzinfo is None:
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise InstagramCooldownStoreError(
            "Instagram cooldown state is invalid."
        ) from None
    return _PersistedCooldown(
        until=until.astimezone(UTC),
        consecutive_rate_limits=consecutive,
    )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
