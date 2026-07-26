from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

from instaloader_webui.auth.throttle import LoginAttemptKey


def test_fifth_failure_blocks_for_fifteen_minutes(throttle) -> None:
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
    key = LoginAttemptKey(username="owner", client_ip="203.0.113.7")

    for offset in range(5):
        throttle.record_failure(key, now + timedelta(seconds=offset))

    decision = throttle.check(key, now + timedelta(seconds=5))

    assert decision.allowed is False
    assert decision.retry_after_seconds == 15 * 60 - 1


def test_success_clears_previous_failures(throttle) -> None:
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
    key = LoginAttemptKey(username="owner", client_ip="203.0.113.7")
    throttle.record_failure(key, now)
    throttle.record_success(key)

    assert throttle.check(key, now).allowed is True


def test_username_normalization_shares_a_failure_bucket(throttle) -> None:
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
    stored_key = LoginAttemptKey(username=" Owner ", client_ip="203.0.113.7")
    equivalent_key = LoginAttemptKey(username="owner", client_ip="203.0.113.7")

    for offset in range(5):
        throttle.record_failure(stored_key, now + timedelta(seconds=offset))

    assert throttle.check(equivalent_key, now + timedelta(seconds=5)).allowed is False


def test_different_username_or_client_ip_uses_a_separate_bucket(throttle) -> None:
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
    blocked_key = LoginAttemptKey(username="owner", client_ip="203.0.113.7")

    for offset in range(5):
        throttle.record_failure(blocked_key, now + timedelta(seconds=offset))

    assert throttle.check(
        LoginAttemptKey(username="other", client_ip="203.0.113.7"),
        now + timedelta(seconds=5),
    ).allowed is True
    assert throttle.check(
        LoginAttemptKey(username="owner", client_ip="203.0.113.8"),
        now + timedelta(seconds=5),
    ).allowed is True


def test_failures_expire_after_the_fifteen_minute_window(throttle) -> None:
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
    key = LoginAttemptKey(username="owner", client_ip="203.0.113.7")

    for offset in range(4):
        throttle.record_failure(key, now + timedelta(seconds=offset))

    throttle.record_failure(key, now + timedelta(minutes=15))

    assert throttle.check(key, now + timedelta(minutes=15)).allowed is True


def test_retry_after_is_never_negative_when_a_block_expires(throttle) -> None:
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
    key = LoginAttemptKey(username="owner", client_ip="203.0.113.7")

    for offset in range(5):
        throttle.record_failure(key, now + timedelta(seconds=offset))

    decision = throttle.check(key, now + timedelta(minutes=15, seconds=4))

    assert decision.allowed is True
    assert decision.retry_after_seconds == 0


def test_concurrent_failures_are_counted_atomically(
    throttle, login_failure_repository, monkeypatch
) -> None:
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
    key = LoginAttemptKey(username="owner", client_ip="203.0.113.7")
    all_reads_completed = Barrier(5)
    original_get = login_failure_repository.get

    def wait_after_read(bucket_digest: str):
        result = original_get(bucket_digest)
        all_reads_completed.wait(timeout=5)
        return result

    monkeypatch.setattr(login_failure_repository, "get", wait_after_read)

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(throttle.record_failure, key, now) for _ in range(5)]
        for future in futures:
            future.result(timeout=10)

    monkeypatch.undo()
    decision = throttle.check(key, now)

    assert decision.allowed is False
    assert decision.retry_after_seconds == 15 * 60
