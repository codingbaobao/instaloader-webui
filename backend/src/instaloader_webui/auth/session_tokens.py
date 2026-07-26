import hashlib
import hmac
import secrets
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class IssuedSession:
    raw: str = field(repr=False)
    digest: str


def digest_session_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue_session_token() -> IssuedSession:
    raw = secrets.token_urlsafe(32)
    return IssuedSession(raw=raw, digest=digest_session_token(raw))


def derive_csrf_token(raw_session: str, app_secret_key: str) -> str:
    return hmac.new(
        app_secret_key.encode("utf-8"),
        raw_session.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
