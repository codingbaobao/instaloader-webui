import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from instaloader_webui.instagram.cooldown import (
    InstagramCooldownStore,
    InstagramCooldownStoreError,
)

NOW = datetime(2026, 8, 11, 1, 0, tzinfo=UTC)


def test_consecutive_rate_limits_back_off_and_cap_at_six_hours(
    tmp_path: Path,
) -> None:
    # Break caught: a per-profile or constant cooldown lets repeated 429 jobs
    # immediately resume with the same unsafe request cadence.
    store = InstagramCooldownStore(tmp_path)
    expected_delays = (
        timedelta(minutes=30),
        timedelta(hours=1),
        timedelta(hours=2),
        timedelta(hours=4),
        timedelta(hours=6),
        timedelta(hours=6),
    )

    for index, expected_delay in enumerate(expected_delays, start=1):
        current = NOW + timedelta(days=index)
        status = store.record_rate_limit(current)
        assert status.consecutive_rate_limits == index
        assert status.until == current + expected_delay


def test_cooldown_state_survives_store_reconstruction(tmp_path: Path) -> None:
    # Break caught: process-only state disappears on container restart and lets
    # queued Instagram jobs resume during an active cooldown.
    first = InstagramCooldownStore(tmp_path)
    recorded = first.record_rate_limit(NOW)

    loaded = InstagramCooldownStore(tmp_path).status(NOW + timedelta(minutes=1))

    assert loaded == recorded


def test_success_resets_persisted_backoff(tmp_path: Path) -> None:
    # Break caught: never resetting consecutive failures leaves every later
    # isolated 429 at the six-hour maximum.
    store = InstagramCooldownStore(tmp_path)
    store.record_rate_limit(NOW)
    store.record_rate_limit(NOW + timedelta(hours=1))

    store.record_success()

    assert store.status(NOW + timedelta(hours=2)).until is None
    assert store.status(NOW + timedelta(hours=2)).consecutive_rate_limits == 0
    restarted = store.record_rate_limit(NOW + timedelta(hours=2))
    assert restarted.until == NOW + timedelta(hours=2, minutes=30)
    assert restarted.consecutive_rate_limits == 1


def test_expired_status_is_inactive_but_keeps_backoff_count(
    tmp_path: Path,
) -> None:
    # Break caught: treating an expired timestamp as active blocks jobs forever,
    # while discarding its count prevents escalation when the first retry 429s.
    store = InstagramCooldownStore(tmp_path)
    store.record_rate_limit(NOW)

    expired = store.status(NOW + timedelta(minutes=31))

    assert expired.until is None
    assert expired.consecutive_rate_limits == 1


@pytest.mark.parametrize(
    "document",
    [
        b"not-json",
        json.dumps({"version": 2}).encode(),
        json.dumps(
            {
                "version": 1,
                "until": "not-a-date",
                "consecutive_rate_limits": 1,
            }
        ).encode(),
        json.dumps(
            {
                "version": 1,
                "until": "2026-08-11T01:30:00+00:00",
                "consecutive_rate_limits": 0,
            }
        ).encode(),
    ],
)
def test_malformed_state_is_rejected_without_guessing(
    tmp_path: Path,
    document: bytes,
) -> None:
    # Break caught: silently ignoring corrupted cooldown state can resume
    # Instagram traffic before the persisted safety boundary.
    state_directory = tmp_path / "state"
    state_directory.mkdir()
    (state_directory / "instagram_cooldown.json").write_bytes(document)

    with pytest.raises(InstagramCooldownStoreError):
        InstagramCooldownStore(tmp_path).status(NOW)


@pytest.mark.skipif(not hasattr(stat, "S_IMODE"), reason="POSIX modes unavailable")
def test_state_uses_restrictive_posix_modes(tmp_path: Path) -> None:
    # Break caught: cooldown metadata is non-secret but lives beside encrypted
    # session state and must not weaken the data directory's permissions.
    store = InstagramCooldownStore(tmp_path)

    store.record_rate_limit(NOW)

    assert stat.S_IMODE((tmp_path / "state").stat().st_mode) == 0o700
    assert (
        stat.S_IMODE((tmp_path / "state" / "instagram_cooldown.json").stat().st_mode)
        == 0o600
    )
