"""Parse the small, intentionally strict Instagram input surface for the POC."""

from dataclasses import dataclass
import re
from typing import Literal
from urllib.parse import urlsplit


_USERNAME_PATTERN = re.compile(r"[A-Za-z0-9._]{1,30}\Z")
_MEDIA_PATHS = frozenset({"p", "reel", "tv"})
_INSTAGRAM_HOSTS = frozenset({"instagram.com", "www.instagram.com"})


class InvalidInstagramInput(ValueError):
    """Raised when an input is not a supported public Instagram reference."""


@dataclass(frozen=True, slots=True)
class ParsedInstagramInput:
    kind: Literal["profile", "media"]
    value: str
    original_url: str | None


def parse_instagram_input(raw: str) -> ParsedInstagramInput:
    """Return a canonical profile username or media shortcode from ``raw``.

    The POC deliberately accepts only direct HTTPS links to Instagram, so it
    cannot be used as a generic URL fetcher or redirect resolver.
    """
    normalized = raw.strip()
    if not normalized:
        raise InvalidInstagramInput("Enter an Instagram profile or media URL.")

    username_candidate = normalized.removeprefix("@").strip()
    if _USERNAME_PATTERN.fullmatch(username_candidate) is not None:
        return ParsedInstagramInput(
            kind="profile",
            value=username_candidate,
            original_url=None,
        )

    try:
        parsed = urlsplit(normalized)
        hostname = parsed.hostname.casefold() if parsed.hostname is not None else None
        port = parsed.port
    except ValueError as error:
        raise InvalidInstagramInput("Enter a supported HTTPS Instagram URL.") from error
    if (
        parsed.scheme != "https"
        or hostname not in _INSTAGRAM_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
    ):
        raise InvalidInstagramInput("Enter a supported HTTPS Instagram URL.")

    path_parts = tuple(part for part in parsed.path.split("/") if part)
    if len(path_parts) == 1 and _USERNAME_PATTERN.fullmatch(path_parts[0]) is not None:
        return ParsedInstagramInput(
            kind="profile",
            value=path_parts[0],
            original_url=normalized,
        )
    if (
        len(path_parts) == 2
        and path_parts[0].casefold() in _MEDIA_PATHS
        and _is_shortcode(path_parts[1])
    ):
        return ParsedInstagramInput(
            kind="media",
            value=path_parts[1],
            original_url=normalized,
        )

    raise InvalidInstagramInput("Enter a profile, post, reel, or TV URL.")


def _is_shortcode(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value))
