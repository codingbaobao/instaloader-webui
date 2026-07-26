from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from types import SimpleNamespace

import pytest

from instaloader_webui.auth.passwords import PasswordService, PasswordServiceBusyError


def test_argon2_hash_does_not_reveal_password() -> None:
    service = PasswordService()
    encoded = service.hash("correct-horse-battery-staple")

    assert "correct-horse-battery-staple" not in encoded
    assert encoded.startswith("$argon2id$")
    assert service.verify(encoded, "correct-horse-battery-staple")
    assert not service.verify(encoded, "wrong-password")


def test_verify_returns_false_for_an_invalid_hash() -> None:
    assert not PasswordService().verify("not-an-argon2-hash", "any-password")


def test_verify_returns_false_for_a_decodable_malformed_hash() -> None:
    malformed = "$argon2id$v=19$m=65536,t=3,p=4$AAAA$AAAA"

    assert not PasswordService().verify(malformed, "any-password")


def test_verify_returns_false_for_a_password_that_cannot_encode_as_utf8() -> None:
    valid_hash = PasswordService().hash("correct-horse-battery-staple")

    assert not PasswordService().verify(valid_hash, "\ud800")


def test_argon2_work_is_bounded_and_overload_fails_without_waiting(
    monkeypatch,
) -> None:
    service = PasswordService()
    two_workers_entered = Event()
    release_workers = Event()
    counter_lock = Lock()
    active_workers = 0
    maximum_active_workers = 0

    def blocking_verify(_hash_value: str, _password: str) -> bool:
        nonlocal active_workers, maximum_active_workers
        with counter_lock:
            active_workers += 1
            maximum_active_workers = max(maximum_active_workers, active_workers)
            if active_workers == 2:
                two_workers_entered.set()
        assert release_workers.wait(timeout=5)
        with counter_lock:
            active_workers -= 1
        return True

    monkeypatch.setattr(service, "_hasher", SimpleNamespace(verify=blocking_verify))
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(service.verify, "hash", f"password-{offset}")
            for offset in range(2)
        ]
        assert two_workers_entered.wait(timeout=5)
        with pytest.raises(PasswordServiceBusyError):
            service.verify("hash", "overload-one")
        with pytest.raises(PasswordServiceBusyError):
            service.verify("hash", "overload-two")
        release_workers.set()
        outcomes = [future.result(timeout=5) for future in futures]

    assert outcomes.count(True) == 2
    assert maximum_active_workers == 2
