"""Lightweight, resumable Posts and Reels manifests for profile Feed content."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast

from instaloader import InvalidArgumentException, Post, Profile
from instaloader.nodeiterator import FrozenNodeIterator, NodeIterator

FeedManifestSource = Literal["posts", "reels"]
SAFE_FEED_MANIFEST_ERROR = "Instagram Feed manifest data is invalid."
_REELS_DOC_ID = "7845543455542541"
_INSTALOADER_GET_REELS = Profile.get_reels


class FeedManifestError(RuntimeError):
    """An upstream Feed manifest cannot be safely interpreted or resumed."""


@dataclass(frozen=True, slots=True)
class FeedManifestEntry:
    shortcode: str
    published_at_hint: datetime | None
    source: FeedManifestSource
    resolve: Callable[[], Post] = field(repr=False, compare=False)


class FeedManifestIterator(Protocol):
    @property
    def count(self) -> int | None: ...

    def __iter__(self) -> FeedManifestIterator: ...

    def __next__(self) -> FeedManifestEntry: ...

    def freeze(self) -> FrozenNodeIterator: ...

    def thaw(self, frozen: FrozenNodeIterator) -> None: ...


@dataclass(slots=True)
class _NodeFeedManifestIterator:
    _iterator: Iterator[Any]
    _entry: Callable[[Any], FeedManifestEntry]

    @property
    def count(self) -> int | None:
        count = getattr(self._iterator, "count", None)
        return count if type(count) is int and count >= 0 else None

    def __iter__(self) -> _NodeFeedManifestIterator:
        return self

    def __next__(self) -> FeedManifestEntry:
        return self._entry(next(self._iterator))

    def freeze(self) -> FrozenNodeIterator:
        freeze = getattr(self._iterator, "freeze", None)
        if not callable(freeze):
            raise FeedManifestError(SAFE_FEED_MANIFEST_ERROR)
        frozen = freeze()
        if not isinstance(frozen, FrozenNodeIterator):
            raise FeedManifestError(SAFE_FEED_MANIFEST_ERROR)
        return frozen

    def thaw(self, frozen: FrozenNodeIterator) -> None:
        thaw = getattr(self._iterator, "thaw", None)
        if not callable(thaw):
            raise FeedManifestError(SAFE_FEED_MANIFEST_ERROR)
        try:
            thaw(frozen)
        except InvalidArgumentException as error:
            raise FeedManifestError(SAFE_FEED_MANIFEST_ERROR) from error


def _published_at_hint(value: object) -> datetime | None:
    if type(value) not in (int, float):
        return None
    timestamp = float(cast(int | float, value))
    if not math.isfinite(timestamp):
        return None
    try:
        return datetime.fromtimestamp(timestamp, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _post_entry(post: object) -> FeedManifestEntry:
    if not isinstance(post, Post):
        raise FeedManifestError(SAFE_FEED_MANIFEST_ERROR)
    shortcode = post.shortcode
    if type(shortcode) is not str or not shortcode:
        raise FeedManifestError(SAFE_FEED_MANIFEST_ERROR)
    published_at = post.date_utc
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=UTC)
    else:
        published_at = published_at.astimezone(UTC)
    return FeedManifestEntry(
        shortcode=shortcode,
        published_at_hint=published_at,
        source="posts",
        resolve=lambda: post,
    )


def _reel_post_entry(post: object) -> FeedManifestEntry:
    entry = _post_entry(post)
    return FeedManifestEntry(
        shortcode=entry.shortcode,
        published_at_hint=entry.published_at_hint,
        source="reels",
        resolve=entry.resolve,
    )


def _reel_entry(context: object, node: object) -> FeedManifestEntry:
    if type(node) is not dict:
        raise FeedManifestError(SAFE_FEED_MANIFEST_ERROR)
    media = node.get("media")
    if type(media) is not dict:
        raise FeedManifestError(SAFE_FEED_MANIFEST_ERROR)
    shortcode = media.get("code")
    if type(shortcode) is not str or not shortcode:
        raise FeedManifestError(SAFE_FEED_MANIFEST_ERROR)
    return FeedManifestEntry(
        shortcode=shortcode,
        published_at_hint=_published_at_hint(media.get("taken_at")),
        source="reels",
        resolve=lambda: Post.from_shortcode(
            context=cast(Any, context),
            shortcode=shortcode,
        ),
    )


def _reels_connection(document: object) -> dict[str, Any]:
    try:
        connection = cast(dict[str, Any], document)["data"][
            "xdt_api__v1__clips__user__connection_v2"
        ]
    except (KeyError, TypeError):
        raise FeedManifestError(SAFE_FEED_MANIFEST_ERROR) from None
    if type(connection) is not dict:
        raise FeedManifestError(SAFE_FEED_MANIFEST_ERROR)
    return connection


def build_posts_manifest(profile: Profile) -> FeedManifestIterator:
    """Wrap Instaloader's timeline iterator without additional item lookups."""
    return _NodeFeedManifestIterator(
        iter(profile.get_posts()),
        _post_entry,
    )


def build_reels_manifest(profile: Profile) -> FeedManifestIterator:
    """Read raw Reel identities and defer full Post lookup until resolution."""
    profile_get_reels = getattr(type(profile), "get_reels", None)
    if (
        not hasattr(profile, "_context")
        or (
            profile_get_reels is not None
            and profile_get_reels is not _INSTALOADER_GET_REELS
        )
    ):
        return _NodeFeedManifestIterator(
            iter(profile.get_reels()),
            _reel_post_entry,
        )
    profile._obtain_metadata()
    context = profile._context
    iterator: NodeIterator[dict[str, Any]] = NodeIterator(
        context=context,
        edge_extractor=_reels_connection,
        node_wrapper=lambda node: node,
        query_variables={
            "data": {
                "page_size": 12,
                "include_feed_video": True,
                "target_user_id": str(profile.userid),
            }
        },
        query_referer=f"https://www.instagram.com/{profile.username}/",
        doc_id=_REELS_DOC_ID,
        query_hash=None,
    )
    return _NodeFeedManifestIterator(
        iterator,
        lambda node: _reel_entry(context, node),
    )
