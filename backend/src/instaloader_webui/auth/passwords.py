from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError


class PasswordService:
    """Hash and verify administrator passwords with Argon2id."""

    def __init__(self) -> None:
        self._hasher = PasswordHasher()

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, hash_value: str, password: str) -> bool:
        try:
            return self._hasher.verify(hash_value, password)
        except (InvalidHashError, VerifyMismatchError):
            return False
