"""Validate and persist one imported Instagram browser session."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from instaloader import (
    AbortDownloadException,
    ConnectionException,
    Instaloader,
    TooManyRequestsException,
)

from instaloader_webui.instagram.cookie_file import (
    CookieFileError,
    InstagramCookie,
    cookie_dict,
    parse_netscape_cookie_file,
)
from instaloader_webui.instagram.session_store import (
    InstagramSessionSnapshot,
    InstagramSessionStatus,
    InstagramSessionStore,
)

_LOGIN_CHECK_QUERY_HASH = "d6f4427fbe92d846298cf93df0b937d3"


@dataclass(frozen=True, slots=True)
class InstagramSessionImportError(Exception):
    """A user-safe failure while validating an Instagram Cookie import."""

    status_code: int
    code: str
    message: str


class InstagramSessionService:
    """Validate candidates in isolation before atomically replacing the session."""

    def __init__(self, sessions: InstagramSessionStore) -> None:
        self._sessions = sessions

    def status(self) -> InstagramSessionStatus:
        """Return safe metadata for the currently configured session."""
        return self._sessions.status()

    def import_netscape(
        self, payload: bytes, now: datetime
    ) -> InstagramSessionStatus:
        """Validate a Netscape Cookie export before storing it encrypted."""
        try:
            cookies = parse_netscape_cookie_file(payload)
        except CookieFileError as error:
            raise InstagramSessionImportError(
                status_code=400,
                code="invalid_cookie_file",
                message="The Cookie file is invalid or unsupported.",
            ) from error

        username = self._validate_candidate(cookies)
        return self._sessions.replace(
            InstagramSessionSnapshot(
                username=username,
                cookies=cookies,
                imported_at=now,
                last_validated_at=now,
            )
        )

    def remove(self) -> None:
        """Remove the configured session, if one exists."""
        self._sessions.delete()

    @staticmethod
    def _validate_candidate(cookies: tuple[InstagramCookie, ...]) -> str:
        loader = Instaloader(
            sleep=False,
            quiet=True,
            max_connection_attempts=3,
            request_timeout=20,
        )
        try:
            loader.load_session("cookie-import", cookie_dict(cookies))
            response = loader.context.graphql_query(_LOGIN_CHECK_QUERY_HASH, {})
            if _is_challenge_or_checkpoint(response):
                raise InstagramSessionImportError(
                    status_code=422,
                    code="instagram_challenge_required",
                    message="Instagram requires a challenge or checkpoint before this session can be used.",
                )
            data = response.get("data")
            user = data.get("user") if isinstance(data, dict) else None
            username = user.get("username") if isinstance(user, dict) else None
            if not isinstance(username, str) or not username:
                raise InstagramSessionImportError(
                    status_code=422,
                    code="instagram_session_invalid",
                    message="The imported Instagram session is invalid or expired.",
                )
            return username
        except InstagramSessionImportError:
            raise
        except AbortDownloadException as error:
            if _is_challenge_or_checkpoint(error):
                raise InstagramSessionImportError(
                    status_code=422,
                    code="instagram_challenge_required",
                    message="Instagram requires a challenge or checkpoint before this session can be used.",
                ) from error
            raise InstagramSessionImportError(
                status_code=422,
                code="instagram_session_invalid",
                message="The imported Instagram session is invalid or expired.",
            ) from error
        except TooManyRequestsException as error:
            raise InstagramSessionImportError(
                status_code=429,
                code="instagram_rate_limited",
                message="Instagram is temporarily rate limiting this server. Try again later.",
            ) from error
        except ConnectionException as error:
            if _is_rate_limited(error):
                raise InstagramSessionImportError(
                    status_code=429,
                    code="instagram_rate_limited",
                    message="Instagram is temporarily rate limiting this server. Try again later.",
                ) from error
            raise InstagramSessionImportError(
                status_code=502,
                code="instagram_unavailable",
                message="Instagram is temporarily unavailable. Try again later.",
            ) from error
        finally:
            loader.close()


def _is_challenge_or_checkpoint(value: object) -> bool:
    if isinstance(value, dict):
        message = value.get("message")
        if isinstance(message, str) and _contains_challenge_marker(message):
            return True
        return any(
            key in value and value[key]
            for key in ("challenge", "checkpoint_url", "challenge_url")
        )
    return _contains_challenge_marker(str(value))


def _contains_challenge_marker(value: str) -> bool:
    normalized = value.casefold()
    return "challenge" in normalized or "checkpoint" in normalized


def _is_rate_limited(error: ConnectionException) -> bool:
    return "429" in str(error)
