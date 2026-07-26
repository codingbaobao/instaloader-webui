from instaloader_webui.auth.passwords import PasswordService


def test_argon2_hash_does_not_reveal_password() -> None:
    service = PasswordService()
    encoded = service.hash("correct-horse-battery-staple")

    assert "correct-horse-battery-staple" not in encoded
    assert encoded.startswith("$argon2id$")
    assert service.verify(encoded, "correct-horse-battery-staple")
    assert not service.verify(encoded, "wrong-password")


def test_verify_returns_false_for_an_invalid_hash() -> None:
    assert not PasswordService().verify("not-an-argon2-hash", "any-password")
