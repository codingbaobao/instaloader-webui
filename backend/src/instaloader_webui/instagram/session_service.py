"""Validate and persist one imported Instagram browser session."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime

from instaloader import (
    AbortDownloadException,
    ConnectionException,
    Instaloader,
    LoginRequiredException,
    QueryReturnedBadRequestException,
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
            if not isinstance(response, Mapping):
                raise _unavailable_error()
            if _is_challenge_or_checkpoint(response):
                raise InstagramSessionImportError(
                    status_code=422,
                    code="instagram_challenge_required",
                    message="Instagram requires a challenge or checkpoint before this session can be used.",
                )
            data = response.get("data")
            if not isinstance(data, Mapping):
                raise _unavailable_error()
            user = data.get("user")
            if user is None:
                raise InstagramSessionImportError(
                    status_code=422,
                    code="instagram_session_invalid",
                    message="The imported Instagram session is invalid or expired.",
                )
            if not isinstance(user, Mapping):
                raise _unavailable_error()
            username = user.get("username")
            if username is None or (isinstance(username, str) and not username):
                raise InstagramSessionImportError(
                    status_code=422,
                    code="instagram_session_invalid",
                    message="The imported Instagram session is invalid or expired.",
                )
            if not isinstance(username, str):
                raise _unavailable_error()
            return username
        except InstagramSessionImportError:
            raise
        except AbortDownloadException as error:
            raise _classify_exception(error, invalid_session=True) from error
        except TooManyRequestsException as error:
            raise _classify_exception(error) from error
        except (LoginRequiredException, QueryReturnedBadRequestException) as error:
            raise _classify_exception(error, invalid_session=True) from error
        except ConnectionException as error:
            raise _classify_exception(error) from error
        finally:
            loader.close()


def _is_challenge_or_checkpoint(value: object) -> bool:
    if isinstance(value, Mapping):
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


def _is_rate_limited(error: BaseException) -> bool:
    return any(
        isinstance(current, TooManyRequestsException) or "429" in str(current)
        for current in _exception_chain(error)
    )


def _classify_exception(
    error: BaseException, *, invalid_session: bool = False
) -> InstagramSessionImportError:
    if any(
        _is_challenge_or_checkpoint(current) for current in _exception_chain(error)
    ):
        return InstagramSessionImportError(
            status_code=422,
            code="instagram_challenge_required",
            message="Instagram requires a challenge or checkpoint before this session can be used.",
        )
    if _is_rate_limited(error):
        return InstagramSessionImportError(
            status_code=429,
            code="instagram_rate_limited",
            message="Instagram is temporarily rate limiting this server. Try again later.",
        )
    if invalid_session:
        return InstagramSessionImportError(
            status_code=422,
            code="instagram_session_invalid",
            message="The imported Instagram session is invalid or expired.",
        )
    return InstagramSessionImportError(
        status_code=502,
        code="instagram_unavailable",
        message="Instagram is temporarily unavailable. Try again later.",
    )


def _unavailable_error() -> InstagramSessionImportError:
    return InstagramSessionImportError(
        status_code=502,
        code="instagram_unavailable",
        message="Instagram is temporarily unavailable. Try again later.",
    )


def _exception_chain(error: BaseException) -> Iterator[BaseException]:
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        for chained in (current.__cause__, current.__context__):
            if chained is not None:
                pending.append(chained)
