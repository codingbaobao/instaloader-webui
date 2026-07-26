import hmac
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from instaloader_webui.db.repositories import (
    LoginFailureRepository,
    LoginFailureSnapshot,
)

FAILURE_WINDOW = timedelta(minutes=15)
MAXIMUM_FAILURES = 5
BLOCK_DURATION = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class LoginAttemptKey:
    username: str
    client_ip: str


@dataclass(frozen=True, slots=True)
class ThrottleDecision:
    allowed: bool
    retry_after_seconds: int


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class LoginThrottle:
    """Apply persistent, digest-only throttling to administrator login attempts."""

    def __init__(self, *, repository: LoginFailureRepository, hmac_secret: str) -> None:
        self._repository = repository
        self._hmac_secret = hmac_secret

    def check(self, key: LoginAttemptKey, now: datetime) -> ThrottleDecision:
        current_time = _as_utc(now)
        bucket_digest = self._bucket_digest(key)
        bucket = self._repository.get(bucket_digest)
        if bucket is None:
            return ThrottleDecision(allowed=True, retry_after_seconds=0)
        if bucket.blocked_until is not None and bucket.blocked_until > current_time:
            seconds_remaining = math.ceil(
                (bucket.blocked_until - current_time).total_seconds()
            )
            return ThrottleDecision(
                allowed=False,
                retry_after_seconds=max(0, seconds_remaining),
            )
        if bucket.blocked_until is not None or self._is_expired(bucket, current_time):
            self._repository.delete(bucket_digest)
        return ThrottleDecision(allowed=True, retry_after_seconds=0)

    def record_failure(self, key: LoginAttemptKey, now: datetime) -> None:
        current_time = _as_utc(now)
        bucket_digest = self._bucket_digest(key)
        bucket = self._repository.get(bucket_digest)
        if bucket is None or self._is_expired(bucket, current_time):
            self._repository.save(
                LoginFailureSnapshot(
                    bucket_digest=bucket_digest,
                    failure_count=1,
                    first_failure_at=current_time,
                    last_failure_at=current_time,
                    blocked_until=None,
                )
            )
            return
        if bucket.blocked_until is not None and bucket.blocked_until > current_time:
            return
        failure_count = bucket.failure_count + 1
        blocked_until = (
            current_time + BLOCK_DURATION
            if failure_count >= MAXIMUM_FAILURES
            else None
        )
        self._repository.save(
            LoginFailureSnapshot(
                bucket_digest=bucket_digest,
                failure_count=failure_count,
                first_failure_at=bucket.first_failure_at,
                last_failure_at=current_time,
                blocked_until=blocked_until,
            )
        )

    def record_success(self, key: LoginAttemptKey) -> None:
        self._repository.delete(self._bucket_digest(key))

    def _bucket_digest(self, key: LoginAttemptKey) -> str:
        normalized_username = key.username.strip().casefold()
        payload = json.dumps(
            [normalized_username, key.client_ip], separators=(",", ":")
        ).encode("utf-8")
        return hmac.new(
            self._hmac_secret.encode("utf-8"), payload, sha256
        ).hexdigest()

    @staticmethod
    def _is_expired(bucket: LoginFailureSnapshot, now: datetime) -> bool:
        return now - bucket.first_failure_at >= FAILURE_WINDOW
