from instaloader_webui.auth.session_tokens import (
    derive_csrf_token,
    digest_session_token,
    issue_session_token,
)


def test_session_token_is_opaque_and_only_digest_is_persisted() -> None:
    issued = issue_session_token()

    assert len(issued.raw) >= 43
    assert issued.raw not in issued.digest
    assert issued.digest == digest_session_token(issued.raw)
    assert issued.raw not in repr(issued)


def test_csrf_token_is_stable_for_session_and_secret() -> None:
    first = derive_csrf_token("raw-session", "s" * 32)
    second = derive_csrf_token("raw-session", "s" * 32)

    assert first == second
    assert first != derive_csrf_token("another-session", "s" * 32)
    assert first != derive_csrf_token("raw-session", "t" * 32)
