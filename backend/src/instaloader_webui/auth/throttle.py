import hmac
import ipaddress
import json
import math
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from instaloader_webui.db.repositories import (
    LoginAdmissionSnapshot,
    LoginFailureRepository,
)

FAILURE_WINDOW = timedelta(minutes=15)
ACCOUNT_MAXIMUM_FAILURES = 5
IP_MAXIMUM_FAILURES = 5
GLOBAL_MAXIMUM_FAILURES = 20
BLOCK_DURATION = timedelta(minutes=15)
RESERVATION_LEASE = timedelta(seconds=30)
LOGIN_STATE_CARDINALITY_CAP = 1_024


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
        retry_after_seconds = 0
        for bucket_digest in self._scope_digests(key):
            bucket = self._repository.get(bucket_digest)
            if (
                bucket is not None
                and bucket.blocked_until is not None
                and bucket.blocked_until > current_time
            ):
                retry_after_seconds = max(
                    retry_after_seconds,
                    math.ceil((bucket.blocked_until - current_time).total_seconds()),
                )
        if retry_after_seconds > 0:
            return ThrottleDecision(False, retry_after_seconds)
        return ThrottleDecision(allowed=True, retry_after_seconds=0)

    def reserve(self, key: LoginAttemptKey, now: datetime) -> LoginAdmissionSnapshot:
        account_digest, ip_digest, global_digest = self._scope_digests(key)
        return self._repository.reserve_attempt(
            account_bucket_digest=account_digest,
            ip_bucket_digest=ip_digest,
            global_bucket_digest=global_digest,
            now=_as_utc(now),
            failure_window=FAILURE_WINDOW,
            account_maximum_failures=ACCOUNT_MAXIMUM_FAILURES,
            ip_maximum_failures=IP_MAXIMUM_FAILURES,
            global_maximum_failures=GLOBAL_MAXIMUM_FAILURES,
            reservation_lease=RESERVATION_LEASE,
            cardinality_cap=LOGIN_STATE_CARDINALITY_CAP,
        )

    def record_reserved_failure(
        self, admission: LoginAdmissionSnapshot, now: datetime
    ) -> None:
        if admission.reservation_id is None:
            return
        self._repository.complete_reserved_failure(
            reservation_id=admission.reservation_id,
            now=_as_utc(now),
            failure_window=FAILURE_WINDOW,
            account_maximum_failures=ACCOUNT_MAXIMUM_FAILURES,
            ip_maximum_failures=IP_MAXIMUM_FAILURES,
            global_maximum_failures=GLOBAL_MAXIMUM_FAILURES,
            block_duration=BLOCK_DURATION,
        )

    def cancel(self, admission: LoginAdmissionSnapshot) -> None:
        if admission.reservation_id is not None:
            self._repository.cancel_reservation(admission.reservation_id)

    def record_failure(self, key: LoginAttemptKey, now: datetime) -> None:
        account_digest, ip_digest, global_digest = self._scope_digests(key)
        self._repository.record_scoped_failures(
            scope_limits=(
                (account_digest, ACCOUNT_MAXIMUM_FAILURES),
                (ip_digest, IP_MAXIMUM_FAILURES),
                (global_digest, GLOBAL_MAXIMUM_FAILURES),
            ),
            now=_as_utc(now),
            failure_window=FAILURE_WINDOW,
            block_duration=BLOCK_DURATION,
        )

    def record_success(self, key: LoginAttemptKey) -> None:
        account_digest, ip_digest, _ = self._scope_digests(key)
        self._repository.clear_account_and_invalidate_tuple(
            account_bucket_digest=account_digest,
            ip_bucket_digest=ip_digest,
        )

    def _scope_digests(self, key: LoginAttemptKey) -> tuple[str, str, str]:
        normalized_username = (
            unicodedata.normalize("NFKC", key.username).strip().casefold()
        )
        canonical_ip = self._canonical_client_ip(key.client_ip)
        return (
            self._digest(["account", normalized_username]),
            self._digest(["ip", canonical_ip]),
            self._digest(["global"]),
        )

    def _digest(self, payload_parts: list[str]) -> str:
        payload = json.dumps(payload_parts, separators=(",", ":")).encode("utf-8")
        return hmac.new(self._hmac_secret.encode("utf-8"), payload, sha256).hexdigest()

    @staticmethod
    def _canonical_client_ip(client_ip: str) -> str:
        try:
            address = ipaddress.ip_address(client_ip)
        except ValueError:
            return "unknown"
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            return address.ipv4_mapped.compressed
        return address.compressed
