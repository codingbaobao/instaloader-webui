from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event

from sqlalchemy import event, text

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


def test_success_clears_account_history_but_retains_ip_failure_history(
    throttle,
) -> None:
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
    key = LoginAttemptKey(username="owner", client_ip="203.0.113.7")
    for offset in range(4):
        throttle.record_failure(key, now + timedelta(seconds=offset))

    throttle.record_success(key)

    assert throttle.check(
        LoginAttemptKey(username="owner", client_ip="203.0.113.8"),
        now + timedelta(seconds=4),
    ).allowed
    throttle.record_failure(
        LoginAttemptKey(username="other", client_ip="203.0.113.7"),
        now + timedelta(seconds=4),
    )
    assert not throttle.check(
        LoginAttemptKey(username="third", client_ip="203.0.113.7"),
        now + timedelta(seconds=5),
    ).allowed


def test_username_normalization_shares_a_failure_bucket(throttle) -> None:
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
    stored_key = LoginAttemptKey(username=" Owner ", client_ip="203.0.113.7")
    equivalent_key = LoginAttemptKey(username="owner", client_ip="203.0.113.7")

    for offset in range(5):
        throttle.record_failure(stored_key, now + timedelta(seconds=offset))

    assert throttle.check(equivalent_key, now + timedelta(seconds=5)).allowed is False


def test_account_and_ip_scopes_are_independent(throttle) -> None:
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
    blocked_key = LoginAttemptKey(username="owner", client_ip="203.0.113.7")

    for offset in range(5):
        throttle.record_failure(blocked_key, now + timedelta(seconds=offset))

    assert (
        throttle.check(
            LoginAttemptKey(username="other", client_ip="203.0.113.7"),
            now + timedelta(seconds=5),
        ).allowed
        is False
    )
    assert (
        throttle.check(
            LoginAttemptKey(username="owner", client_ip="203.0.113.8"),
            now + timedelta(seconds=5),
        ).allowed
        is False
    )
    assert (
        throttle.check(
            LoginAttemptKey(username="other", client_ip="203.0.113.8"),
            now + timedelta(seconds=5),
        ).allowed
        is True
    )


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


def test_concurrent_failures_are_counted_atomically(throttle, engine) -> None:
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
    key = LoginAttemptKey(username="owner", client_ip="203.0.113.7")
    workers_started = Barrier(5)
    write_locks_started = Barrier(5)
    begin_immediate_calls = 0

    def synchronize_write_lock(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        nonlocal begin_immediate_calls
        if statement == "BEGIN IMMEDIATE":
            begin_immediate_calls += 1
            write_locks_started.wait(timeout=5)

    event.listen(engine, "before_cursor_execute", synchronize_write_lock)
    try:
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [
                executor.submit(
                    lambda: (
                        workers_started.wait(timeout=5),
                        throttle.record_failure(key, now),
                    )
                )
                for _ in range(5)
            ]
            for future in futures:
                future.result(timeout=10)
    finally:
        event.remove(engine, "before_cursor_execute", synchronize_write_lock)

    assert begin_immediate_calls == 5
    decision = throttle.check(key, now)

    assert decision.allowed is False
    assert decision.retry_after_seconds == 15 * 60


def test_stale_expiry_check_does_not_delete_a_reset_failure_bucket(
    throttle, login_failure_repository, monkeypatch
) -> None:
    first_failure = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
    expired_at = first_failure + timedelta(minutes=15)
    key = LoginAttemptKey(username="owner", client_ip="203.0.113.7")
    throttle.record_failure(key, first_failure)
    stale_read_complete = Event()
    allow_stale_check_to_continue = Event()
    original_get = login_failure_repository.get

    def pause_after_stale_read(bucket_digest: str):
        snapshot = original_get(bucket_digest)
        stale_read_complete.set()
        assert allow_stale_check_to_continue.wait(timeout=5)
        return snapshot

    monkeypatch.setattr(login_failure_repository, "get", pause_after_stale_read)
    with ThreadPoolExecutor(max_workers=1) as executor:
        stale_check = executor.submit(throttle.check, key, expired_at)
        assert stale_read_complete.wait(timeout=5)
        throttle.record_failure(key, expired_at)
        allow_stale_check_to_continue.set()
        assert stale_check.result(timeout=5).allowed is True

    monkeypatch.undo()
    for offset in range(1, 5):
        throttle.record_failure(key, expired_at + timedelta(seconds=offset))

    assert throttle.check(key, expired_at + timedelta(seconds=5)).allowed is False


def test_concurrent_login_admission_reserves_only_five_attempts(throttle) -> None:
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
    key = LoginAttemptKey(username="owner", client_ip="203.0.113.7")
    workers_started = Barrier(8)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(
                lambda: (
                    workers_started.wait(timeout=5),
                    throttle.reserve(key, now),
                )[1]
            )
            for _ in range(8)
        ]
        admissions = tuple(future.result(timeout=10) for future in futures)

    assert sum(admission.allowed for admission in admissions) == 5
    assert sum(not admission.allowed for admission in admissions) == 3
    assert all(
        admission.reservation_id is not None
        for admission in admissions
        if admission.allowed
    )
    assert all(
        admission.retry_after_seconds > 0
        for admission in admissions
        if not admission.allowed
    )


def test_rotating_usernames_from_one_ip_hit_the_ip_scope(throttle) -> None:
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)

    for offset in range(5):
        key = LoginAttemptKey(
            username=f"owner-{offset}",
            client_ip="203.0.113.7",
        )
        admission = throttle.reserve(key, now + timedelta(seconds=offset))
        assert admission.allowed
        throttle.record_reserved_failure(admission, now + timedelta(seconds=offset))

    denied = throttle.reserve(
        LoginAttemptKey(username="owner-next", client_ip="203.0.113.7"),
        now + timedelta(seconds=5),
    )

    assert denied.allowed is False
    assert denied.reservation_id is None


def test_one_account_across_ips_hits_the_account_scope(throttle) -> None:
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)

    for offset in range(5):
        key = LoginAttemptKey(
            username=" Owner ",
            client_ip=f"203.0.113.{offset + 1}",
        )
        admission = throttle.reserve(key, now + timedelta(seconds=offset))
        assert admission.allowed
        throttle.record_reserved_failure(admission, now + timedelta(seconds=offset))

    denied = throttle.reserve(
        LoginAttemptKey(username="owner", client_ip="198.51.100.8"),
        now + timedelta(seconds=5),
    )

    assert denied.allowed is False


def test_distributed_attempts_hit_the_service_global_scope(throttle) -> None:
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)

    for offset in range(20):
        key = LoginAttemptKey(
            username=f"owner-{offset}",
            client_ip=f"2001:db8::{offset + 1}",
        )
        admission = throttle.reserve(key, now + timedelta(seconds=offset))
        assert admission.allowed
        throttle.record_reserved_failure(admission, now + timedelta(seconds=offset))

    denied = throttle.reserve(
        LoginAttemptKey(username="owner-next", client_ip="198.51.100.8"),
        now + timedelta(seconds=20),
    )

    assert denied.allowed is False


def test_ipv4_mapped_address_shares_the_canonical_ip_scope(throttle) -> None:
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)

    for offset in range(5):
        key = LoginAttemptKey(
            username=f"owner-{offset}",
            client_ip="::ffff:203.0.113.7",
        )
        admission = throttle.reserve(key, now + timedelta(seconds=offset))
        assert admission.allowed
        throttle.record_reserved_failure(admission, now + timedelta(seconds=offset))

    denied = throttle.reserve(
        LoginAttemptKey(username="owner-next", client_ip="203.0.113.7"),
        now + timedelta(seconds=5),
    )

    assert denied.allowed is False


def test_reservation_prunes_expired_failure_rows_globally(
    throttle, session_factory
) -> None:
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
    for offset in range(3):
        throttle.record_failure(
            LoginAttemptKey(
                username=f"owner-{offset}",
                client_ip=f"203.0.113.{offset + 1}",
            ),
            now,
        )

    throttle.reserve(
        LoginAttemptKey(username="fresh-owner", client_ip="198.51.100.9"),
        now + timedelta(minutes=16),
    )

    with session_factory() as session:
        failure_rows = session.scalar(text("SELECT COUNT(*) FROM login_failures"))
    assert failure_rows == 0


def test_hard_cardinality_denial_does_not_leak_a_reservation(
    throttle, session_factory
) -> None:
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
    with session_factory.begin() as session:
        session.execute(
            text(
                """
                INSERT INTO login_failures (
                    bucket_digest,
                    failure_count,
                    first_failure_at,
                    last_failure_at,
                    blocked_until
                )
                VALUES (:digest, 1, :now, :now, NULL)
                """
            ),
            [
                {
                    "digest": f"{offset:064x}",
                    "now": now.replace(tzinfo=None).isoformat(sep=" "),
                }
                for offset in range(1_022)
            ],
        )

    denied = throttle.reserve(
        LoginAttemptKey(username="owner", client_ip="203.0.113.7"),
        now,
    )

    with session_factory() as session:
        reservation_rows = session.scalar(
            text("SELECT COUNT(*) FROM login_attempt_reservations")
        )
        failure_rows = session.scalar(text("SELECT COUNT(*) FROM login_failures"))
    assert denied.allowed is False
    assert reservation_rows == 0
    assert failure_rows == 1_022
