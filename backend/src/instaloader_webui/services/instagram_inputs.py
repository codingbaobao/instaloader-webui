"""Parse the small, intentionally strict Instagram input surface for the POC."""

import re
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

_USERNAME_PATTERN = re.compile(r"[A-Za-z0-9._]{1,30}\Z")
_STORY_MEDIA_ID_PATTERN = re.compile(r"[0-9]{1,32}\Z")
_INSTAGRAM_HOSTS = frozenset({"instagram.com", "www.instagram.com"})
_INVALID_INPUT_MESSAGE = "Enter a profile, post, reel, TV, or Story URL."


class InvalidInstagramInput(ValueError):
    """Raised when an input is not a supported public Instagram reference."""


@dataclass(frozen=True, slots=True)
class ProfileInput:
    username: str
    canonical_url: str | None
    kind: Literal["profile"] = field(default="profile", init=False)

    @property
    def identifier(self) -> str:
        return self.username


@dataclass(frozen=True, slots=True)
class PostInput:
    shortcode: str
    canonical_url: str
    kind: Literal["post"] = field(default="post", init=False)

    @property
    def identifier(self) -> str:
        return self.shortcode


@dataclass(frozen=True, slots=True)
class ReelInput:
    shortcode: str
    canonical_url: str
    kind: Literal["reel"] = field(default="reel", init=False)

    @property
    def identifier(self) -> str:
        return self.shortcode


@dataclass(frozen=True, slots=True)
class StoryInput:
    username: str
    story_media_id: str
    canonical_url: str
    kind: Literal["story"] = field(default="story", init=False)

    @property
    def identifier(self) -> str:
        return self.story_media_id


InstagramInput = ProfileInput | PostInput | ReelInput | StoryInput


def parse_instagram_input(raw: str) -> InstagramInput:
    """Return a typed, canonical public Instagram reference from ``raw``."""
    normalized = raw.strip()
    if not normalized:
        raise InvalidInstagramInput(_INVALID_INPUT_MESSAGE)

    username_candidate = normalized.removeprefix("@").strip()
    if _USERNAME_PATTERN.fullmatch(username_candidate) is not None:
        return ProfileInput(username=username_candidate, canonical_url=None)

    try:
        parsed = urlsplit(normalized)
        hostname = parsed.hostname.casefold() if parsed.hostname is not None else None
        port = parsed.port
    except ValueError as error:
        raise InvalidInstagramInput(_INVALID_INPUT_MESSAGE) from error
    if (
        parsed.scheme != "https"
        or hostname not in _INSTAGRAM_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
    ):
        raise InvalidInstagramInput(_INVALID_INPUT_MESSAGE)

    path_parts = tuple(part for part in parsed.path.split("/") if part)
    if len(path_parts) == 1 and _USERNAME_PATTERN.fullmatch(path_parts[0]) is not None:
        username = path_parts[0]
        return ProfileInput(
            username=username,
            canonical_url=f"https://www.instagram.com/{username}/",
        )
    if len(path_parts) == 2 and _is_shortcode(path_parts[1]):
        route, shortcode = path_parts
        if route.casefold() in {"p", "tv"}:
            return PostInput(
                shortcode=shortcode,
                canonical_url=f"https://www.instagram.com/p/{shortcode}/",
            )
        if route.casefold() == "reel":
            return ReelInput(
                shortcode=shortcode,
                canonical_url=f"https://www.instagram.com/reel/{shortcode}/",
            )
    if (
        len(path_parts) == 3
        and path_parts[0].casefold() == "stories"
        and _USERNAME_PATTERN.fullmatch(path_parts[1]) is not None
        and _STORY_MEDIA_ID_PATTERN.fullmatch(path_parts[2]) is not None
    ):
        username, story_media_id = path_parts[1:]
        return StoryInput(
            username=username,
            story_media_id=story_media_id,
            canonical_url=(
                "https://www.instagram.com/stories/"
                f"{username}/{story_media_id}/"
            ),
        )

    raise InvalidInstagramInput(_INVALID_INPUT_MESSAGE)


def _is_shortcode(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value))
