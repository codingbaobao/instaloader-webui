from threading import BoundedSemaphore

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MAXIMUM_CONCURRENT_ARGON2_OPERATIONS = 2
_ARGON2_ADMISSION = BoundedSemaphore(MAXIMUM_CONCURRENT_ARGON2_OPERATIONS)
_DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$4QAyJiqGP6AZpsbMYoPTNw$"
    "KJ7V7+4KGaHLNrHl0BUJGf5Gr/Ho49FCAUDR9t5wr8A"
)


class PasswordServiceBusyError(Exception):
    pass


class PasswordService:
    """Hash and verify administrator passwords with Argon2id."""

    def __init__(self) -> None:
        self._hasher = PasswordHasher()

    @property
    def dummy_hash(self) -> str:
        return _DUMMY_PASSWORD_HASH

    def hash(self, password: str) -> str:
        if not _ARGON2_ADMISSION.acquire(blocking=False):
            raise PasswordServiceBusyError
        try:
            return self._hasher.hash(password)
        finally:
            _ARGON2_ADMISSION.release()

    def verify(self, hash_value: str, password: str) -> bool:
        if not _ARGON2_ADMISSION.acquire(blocking=False):
            raise PasswordServiceBusyError
        try:
            try:
                return self._hasher.verify(hash_value, password)
            except (InvalidHashError, VerificationError, UnicodeEncodeError):
                return False
        finally:
            _ARGON2_ADMISSION.release()
