"""Strict parsing for Netscape-format Instagram Cookie exports."""

from __future__ import annotations

from dataclasses import dataclass, field
import unicodedata


MAXIMUM_COOKIE_FILE_BYTES = 256 * 1024
REQUIRED_COOKIE_NAMES = frozenset({"sessionid", "csrftoken"})


class CookieFileError(ValueError):
    """Raised when a Cookie export is malformed or unsuitable for Instagram."""


@dataclass(frozen=True, slots=True)
class InstagramCookie:
    domain: str
    path: str
    secure: bool
    expires_at: int
    name: str
    value: str = field(repr=False)


def parse_netscape_cookie_file(payload: bytes) -> tuple[InstagramCookie, ...]:
    """Return validated Instagram Cookie records or raise ``CookieFileError``."""
    if not isinstance(payload, bytes):
        raise CookieFileError("Cookie file must be bytes.")
    if len(payload) > MAXIMUM_COOKIE_FILE_BYTES:
        raise CookieFileError("Cookie file is too large.")

    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CookieFileError("Cookie file must be UTF-8 text.") from None
    if _contains_forbidden_control_characters(text):
        raise CookieFileError("Cookie file contains control characters.")

    cookies: list[InstagramCookie] = []
    values_by_name: dict[str, str] = {}
    for raw_line in text.split("\n"):
        line = raw_line.removesuffix("\r")
        if not line.strip() or (
            line.startswith("#") and not line.startswith("#HttpOnly_")
        ):
            continue

        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_") :]

        fields = line.split("\t")
        if len(fields) != 7:
            raise CookieFileError("Cookie file contains an invalid Netscape record.")
        if any(_contains_control_characters(field) for field in fields):
            raise CookieFileError("Cookie file contains control characters.")

        domain, include_subdomains, path, secure, expires_at, name, value = fields
        normalized_domain = domain.casefold()
        if not _is_instagram_domain(normalized_domain):
            continue
        if include_subdomains not in {"TRUE", "FALSE"}:
            raise CookieFileError("Cookie file contains an invalid domain flag.")
        if secure not in {"TRUE", "FALSE"}:
            raise CookieFileError("Cookie file contains an invalid secure flag.")
        if not expires_at.isascii() or not expires_at.isdecimal():
            raise CookieFileError("Cookie file contains an invalid expiry.")
        if not path.startswith("/") or not name:
            raise CookieFileError("Cookie file contains an invalid Cookie name or path.")
        if name in REQUIRED_COOKIE_NAMES and not value:
            raise CookieFileError("Cookie file contains an empty required Cookie.")

        previous_value = values_by_name.get(name)
        if name in values_by_name and previous_value != value:
            raise CookieFileError("Cookie file contains conflicting Cookie records.")
        values_by_name[name] = value
        cookies.append(
            InstagramCookie(
                domain=normalized_domain,
                path=path,
                secure=secure == "TRUE",
                expires_at=int(expires_at),
                name=name,
                value=value,
            )
        )

    present_names = {cookie.name for cookie in cookies if cookie.value}
    if not REQUIRED_COOKIE_NAMES.issubset(present_names):
        raise CookieFileError("Cookie file is missing required Instagram Cookies.")

    return tuple(sorted(cookies, key=lambda cookie: (cookie.name, cookie.domain, cookie.path)))


def cookie_dict(cookies: tuple[InstagramCookie, ...]) -> dict[str, str]:
    """Return Cookie values indexed by their names for Instaloader."""
    return {cookie.name: cookie.value for cookie in cookies}


def _contains_control_characters(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)


def _contains_forbidden_control_characters(value: str) -> bool:
    return any(
        unicodedata.category(character) == "Cc" and character not in {"\t", "\r", "\n"}
        for character in value
    )


def _is_instagram_domain(domain: str) -> bool:
    return domain in {"instagram.com", ".instagram.com"} or domain.endswith(
        ".instagram.com"
    )
