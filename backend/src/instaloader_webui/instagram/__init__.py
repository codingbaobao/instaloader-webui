"""Public, anonymous Instagram integration boundary for the worker."""

from instaloader_webui.instagram.media_processor import MediaProcessor
from instaloader_webui.instagram.media_types import (
    MediaCandidate,
    MediaProcessResult,
    ResolvedMedia,
)
from instaloader_webui.instagram.public_adapter import (
    PublicInstagramAdapterError,
    PublicInstaloaderAdapter,
)

__all__ = [
    "MediaCandidate",
    "MediaProcessResult",
    "MediaProcessor",
    "PublicInstagramAdapterError",
    "PublicInstaloaderAdapter",
    "ResolvedMedia",
]
