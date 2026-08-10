"""Resolve public Instaloader inputs for the shared local media processor."""

from __future__ import annotations

import hashlib
import logging
import os
import random
import shutil
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from inspect import signature
from pathlib import Path, PurePosixPath
from typing import Literal, cast
from urllib.parse import urlsplit

from instaloader import (
    AbortDownloadException,
    BadResponseException,
    Instaloader,
    InstaloaderException,
    Post,
    Profile,
    StoryItem,
)

from instaloader_webui.db.library_repositories import (
    LibraryRepository,
    MediaIdentity,
    MediaSnapshot,
    ProfileSnapshot,
)
from instaloader_webui.instagram.errors import (
    ANONYMOUS_REJECTED,
    MEDIA_NOT_FOUND,
    SESSION_REJECTED,
    classify_instaloader_error,
)
from instaloader_webui.instagram.media_processor import MediaProcessor
from instaloader_webui.instagram.media_types import (
    ContentKind,
    MediaCandidate,
    ResolvedMedia,
)
from instaloader_webui.instagram.profile_lookup import ProfileLookupResolver
from instaloader_webui.instagram.profile_sync import (
    IssueCallback,
    ProfileSyncCoordinator,
    ProfileSyncResult,
)
from instaloader_webui.instagram.safe_issues import (
    MediaItemFailure,
    SafeMediaIssue,
    classify_media_issue,
)
from instaloader_webui.instagram.session_store import InstagramSessionStoreError
from instaloader_webui.instagram.worker_runtime import (
    InstagramSessionRevisionError,
    WorkerInstaloaderRuntime,
)
from instaloader_webui.services.instagram_inputs import (
    PostInput,
    ReelInput,
    StoryInput,
)
from instaloader_webui.services.profile_avatars import (
    PROFILE_AVATAR_MEDIA_TYPE,
    PROFILE_AVATAR_WEBP_MEDIA_TYPE,
    profile_avatar_candidates,
    profile_avatar_path,
    stored_profile_avatar,
)

MediaKind = Literal["post", "reel"]
DirectMediaInput = PostInput | ReelInput | StoryInput
ProgressCallback = Callable[..., None]
_MISSING_STORY_METADATA = "Fetching StoryItem metadata failed."
_LOGGER = logging.getLogger(__name__)
_PROFILE_SYNC_TIME_SLICE_SECONDS = 5 * 60
_AVATAR_DIAGNOSTIC_PREFIX_BYTES = 64
_MAX_AVATAR_DIAGNOSTIC_VALUE_LENGTH = 160
_AVATAR_DIAGNOSTIC_UNAVAILABLE = "[unavailable]"
_AVATAR_DIAGNOSTIC_SENSITIVE_MARKERS = (
    "authorization",
    "cookie",
    "csrftoken",
    "igsh",
    "sessionid",
    "token",
)


def _safe_avatar_diagnostic_value(value: object) -> str:
    try:
        text = str(value)
    except Exception:  # noqa: BLE001 - diagnostics cannot mask the real error
        return _AVATAR_DIAGNOSTIC_UNAVAILABLE
    if any(
        marker in text.casefold() for marker in _AVATAR_DIAGNOSTIC_SENSITIVE_MARKERS
    ):
        return "[redacted]"
    bounded = " ".join(text.split())
    if not bounded:
        return "[empty]"
    if len(bounded) <= _MAX_AVATAR_DIAGNOSTIC_VALUE_LENGTH:
        return bounded
    return f"{bounded[: _MAX_AVATAR_DIAGNOSTIC_VALUE_LENGTH - 1]}…"


def _avatar_header(headers: object, name: str) -> str:
    if not isinstance(headers, Mapping):
        return _AVATAR_DIAGNOSTIC_UNAVAILABLE
    try:
        value = headers.get(name)
        if value is None:
            return _AVATAR_DIAGNOSTIC_UNAVAILABLE
        return _safe_avatar_diagnostic_value(value)
    except Exception:  # noqa: BLE001 - response mappings are untrusted
        return _AVATAR_DIAGNOSTIC_UNAVAILABLE


def _avatar_prefix_kind(prefix: bytes) -> str:
    if not prefix:
        return "empty"
    if prefix.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(prefix) >= 12 and prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP":
        return "webp"
    if prefix.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    stripped = prefix.lstrip().lower()
    if stripped.startswith((b"<!doctype html", b"<html", b"<?xml")):
        return "html"
    if stripped.startswith((b"{", b"[")):
        return "json"
    return "other"


def _avatar_prefix_diagnostic(
    response: object,
    prefix: bytes | None = None,
) -> tuple[str, str]:
    value = prefix
    if value is None:
        try:
            raw = getattr(response, "raw", None)
            if raw is None:
                return _AVATAR_DIAGNOSTIC_UNAVAILABLE, "unreadable"
            value = raw.read(_AVATAR_DIAGNOSTIC_PREFIX_BYTES)
            if not isinstance(value, bytes):
                return _AVATAR_DIAGNOSTIC_UNAVAILABLE, "unreadable"
        except Exception:  # noqa: BLE001 - body diagnostics are best-effort
            return _AVATAR_DIAGNOSTIC_UNAVAILABLE, "unreadable"

    prefix_kind = _avatar_prefix_kind(value)
    lowered_prefix = value.lower()
    if any(
        marker.encode() in lowered_prefix
        for marker in _AVATAR_DIAGNOSTIC_SENSITIVE_MARKERS
    ):
        return "[redacted]", prefix_kind
    return value.hex(), prefix_kind


def _log_invalid_profile_avatar_response(
    *,
    response: object,
    avatar_url: object,
    profile_id: object,
    normalized_content_type: object,
    prefix: bytes | None = None,
) -> None:
    """Emit bounded, credential-safe evidence for a rejected avatar response."""
    try:
        headers = getattr(response, "headers", {})
        final_url = getattr(response, "url", None) or avatar_url
        try:
            final_url_text = str(final_url)
            parsed_url = urlsplit(final_url_text)
            final_host = _safe_avatar_diagnostic_value(
                parsed_url.hostname or _AVATAR_DIAGNOSTIC_UNAVAILABLE
            )
            path_suffix = _safe_avatar_diagnostic_value(
                PurePosixPath(parsed_url.path).suffix
                or _AVATAR_DIAGNOSTIC_UNAVAILABLE
            )
            url_sha256 = hashlib.sha256(
                final_url_text.encode("utf-8", errors="surrogatepass")
            ).hexdigest()
        except Exception:  # noqa: BLE001 - upstream URL objects are untrusted
            final_host = _AVATAR_DIAGNOSTIC_UNAVAILABLE
            path_suffix = _AVATAR_DIAGNOSTIC_UNAVAILABLE
            url_sha256 = _AVATAR_DIAGNOSTIC_UNAVAILABLE

        try:
            redirect_count: object = len(getattr(response, "history", ()))
        except Exception:  # noqa: BLE001 - response history is best-effort
            redirect_count = _AVATAR_DIAGNOSTIC_UNAVAILABLE
        prefix_hex, prefix_kind = _avatar_prefix_diagnostic(response, prefix)

        _LOGGER.warning(
            "instagram_profile_avatar_invalid_response "
            "profile_id=%r status=%s raw_content_type=%r "
            "normalized_content_type=%r content_length=%r "
            "content_encoding=%r transfer_encoding=%r final_host=%r "
            "path_suffix=%r redirect_count=%s url_sha256=%r "
            "prefix_hex=%r prefix_kind=%r",
            _safe_avatar_diagnostic_value(profile_id),
            _safe_avatar_diagnostic_value(
                getattr(response, "status_code", _AVATAR_DIAGNOSTIC_UNAVAILABLE)
            ),
            _avatar_header(headers, "Content-Type"),
            _safe_avatar_diagnostic_value(normalized_content_type),
            _avatar_header(headers, "Content-Length"),
            _avatar_header(headers, "Content-Encoding"),
            _avatar_header(headers, "Transfer-Encoding"),
            final_host,
            path_suffix,
            redirect_count,
            url_sha256,
            prefix_hex,
            prefix_kind,
        )
    except Exception:  # noqa: BLE001 - preserve the stable adapter error
        # Keep even malformed responses observable without exposing the
        # exception, which can itself contain upstream credentials or content.
        try:
            _LOGGER.warning(
                "instagram_profile_avatar_invalid_response "
                "profile_id=%r diagnostics=%r",
                _safe_avatar_diagnostic_value(profile_id),
                _AVATAR_DIAGNOSTIC_UNAVAILABLE,
            )
        except Exception:  # noqa: BLE001 - logging must not mask the real error
            return


class PublicInstagramAdapterError(RuntimeError):
    """A concise, user-safe failure while accessing public Instagram content."""


class _StoryOwnerMismatchError(InstaloaderException):
    """The resolved Story does not belong to the canonical URL owner."""


class _StoryExpiredError(InstaloaderException):
    """The resolved Story is no longer available for download."""


class _MediaOwnerMismatchError(InstaloaderException):
    """Local and resolved Instagram owner identities do not match."""


class _InactiveMediaOwnerError(InstaloaderException):
    """Direct media must not attach to a Profile undergoing deletion."""


@dataclass(frozen=True, slots=True)
class PublicProfile:
    instagram_user_id: int
    username: str
    full_name: str
    biography: str
    profile_pic_url: str | None


@dataclass(frozen=True, slots=True)
class _PublicProfileMediaSource:
    adapter: PublicInstaloaderAdapter
    loader: Instaloader
    owner: ProfileSnapshot

    def iter_stories(self, profile: object) -> Iterator[MediaCandidate]:
        return self.adapter._iter_story_candidates(
            loader=self.loader,
            profile=cast(Profile, profile),
            owner=self.owner,
        )

    def iter_reels(self, profile: object) -> Iterator[MediaCandidate]:
        typed_profile = cast(Profile, profile)
        return (
            self.adapter._profile_post_candidate(
                loader=self.loader,
                post=post,
                kind="reel",
                owner=self.owner,
            )
            for post in typed_profile.get_reels()
        )

    def iter_posts(self, profile: object) -> Iterator[MediaCandidate]:
        typed_profile = cast(Profile, profile)
        return (
            self.adapter._profile_post_candidate(
                loader=self.loader,
                post=post,
                kind="post",
                owner=self.owner,
            )
            for post in typed_profile.get_posts()
        )


class PublicInstaloaderAdapter:
    """Keep upstream Instaloader calls and filesystem staging inside one boundary."""

    def __init__(
        self,
        *,
        data_root: Path,
        media_root: Path,
        jobs_root: Path,
        library: LibraryRepository,
        progress: ProgressCallback,
        loader_runtime: WorkerInstaloaderRuntime,
        profile_lookup_resolver: ProfileLookupResolver,
        issue: IssueCallback | None = None,
    ) -> None:
        self._data_root = data_root.resolve()
        self._media_root = self._require_data_subdirectory(media_root)
        self._jobs_root = self._require_data_subdirectory(jobs_root)
        self._library = library
        self._progress = progress
        self._progress_supports_phase = self._supports_phase(progress)
        self._issue = issue or (lambda _issue: None)
        self._loader_runtime = loader_runtime
        self._profile_lookup_resolver = profile_lookup_resolver

    def fetch_profile(self, username: str) -> PublicProfile:
        """Load normalized metadata for one publicly accessible profile."""
        loader, session_configured = self._acquire_loader(
            self._metadata_staging_directory()
        )
        try:
            profile = self._profile_lookup_resolver.resolve(
                loader.context,
                username,
            )
            return self._normalize_profile(
                profile,
                authenticated=session_configured,
            )
        except (AbortDownloadException, InstaloaderException) as error:
            raise PublicInstagramAdapterError(
                classify_instaloader_error(
                    error,
                    session_configured=session_configured,
                    target="profile",
                )
            ) from error

    def download_shortcode(
        self,
        shortcode: str,
        job_id: str,
        expected_kind: str | None = None,
        *,
        original_url: str | None = None,
    ) -> MediaSnapshot:
        """Download one public media item and persist its finalized local assets."""
        kind = self._normalize_kind(expected_kind)
        canonical_url = (
            original_url
            or self._canonical_original_url(shortcode, kind)
        )
        parsed: PostInput | ReelInput
        if kind == "reel":
            parsed = ReelInput(
                shortcode=shortcode,
                canonical_url=canonical_url,
            )
        else:
            parsed = PostInput(
                shortcode=shortcode,
                canonical_url=canonical_url,
            )
        return self.download_input(parsed, job_id)

    def download_input(
        self,
        parsed: DirectMediaInput,
        job_id: str,
    ) -> MediaSnapshot:
        """Resolve one typed media input and persist its finalized local assets."""
        staging_directory = self._staging_directory(job_id)
        self._recreate_directory(staging_directory)
        try:
            if isinstance(parsed, StoryInput):
                candidate, loader = self._direct_story_candidate(
                    parsed=parsed,
                    staging_directory=staging_directory,
                )
            else:
                loader, session_configured = self._acquire_loader(
                    staging_directory
                )
                candidate = self._direct_post_candidate(
                    loader=loader,
                    parsed=parsed,
                    staging_directory=staging_directory,
                    session_configured=session_configured,
                )
            result = self._processor(loader).process(candidate, job_id=job_id)
            if result.status == "existing":
                self._report(
                    len(result.media.assets),
                    len(result.media.assets),
                    "Media is already saved; skipped duplicate download.",
                )
            else:
                self._report(
                    len(result.media.assets),
                    len(result.media.assets),
                    "Saved public Instagram media.",
                )
            return result.media
        finally:
            self._remove_directory(staging_directory)

    def iter_story_candidates(
        self,
        profile: Profile,
        profile_id: str,
    ) -> Iterator[MediaCandidate]:
        """Yield lightweight current-Story identities for one stored profile."""
        stored_profile = self._library.get_profile(profile_id)
        if stored_profile is None:
            raise PublicInstagramAdapterError(
                "The requested profile no longer exists."
            )
        loader = self._acquire_required_loader(
            self._metadata_staging_directory()
        )
        yield from self._iter_story_candidates(
            loader=loader,
            profile=profile,
            owner=stored_profile,
        )

    def _iter_story_candidates(
        self,
        *,
        loader: Instaloader,
        profile: Profile,
        owner: ProfileSnapshot,
    ) -> Iterator[MediaCandidate]:
        for story in loader.get_stories(userids=[profile.userid]):
            for item in story.get_items():
                identity = MediaIdentity("story_media_id", str(item.mediaid))
                yield self._story_candidate(
                    loader=loader,
                    item=item,
                    owner=owner,
                    identity=identity,
                    original_url=self._canonical_story_url(
                        owner.username,
                        identity.value,
                    ),
                )

    def _direct_post_candidate(
        self,
        *,
        loader: Instaloader,
        parsed: PostInput | ReelInput,
        staging_directory: Path,
        session_configured: bool,
    ) -> MediaCandidate:
        identity = MediaIdentity("shortcode", parsed.shortcode)
        owner = self._existing_media_owner(identity)
        if owner is not None and owner.status != "active":
            raise self._media_item_failure(
                _InactiveMediaOwnerError(
                    "The requested Instagram media owner was not found."
                ),
                identity=identity,
                kind=parsed.kind,
                session_configured=session_configured,
            )
        return MediaCandidate(
            identity=identity,
            kind=parsed.kind,
            session_configured=session_configured,
            resolve=lambda: self._resolve_direct_post(
                loader=loader,
                owner=owner,
                parsed=parsed,
                staging_directory=staging_directory,
            ),
        )

    def _resolve_direct_post(
        self,
        *,
        loader: Instaloader,
        owner: ProfileSnapshot | None,
        parsed: PostInput | ReelInput,
        staging_directory: Path,
    ) -> ResolvedMedia:
        post = Post.from_shortcode(loader.context, parsed.shortcode)
        owner_profile = post.owner_profile
        resolved_owner = owner
        if resolved_owner is not None:
            self._validate_stable_owner(resolved_owner, owner_profile)
        else:
            resolved_owner = self._find_reusable_owner(owner_profile)
            if resolved_owner is None:
                resolved_owner = self._upsert_owner(
                    loader=loader,
                    profile=owner_profile,
                    staging_directory=staging_directory,
                )
        return self._resolve_post(
            loader=loader,
            post=post,
            kind=parsed.kind,
            original_url=parsed.canonical_url,
            owner=resolved_owner,
        )

    def _direct_story_candidate(
        self,
        *,
        parsed: StoryInput,
        staging_directory: Path,
    ) -> tuple[MediaCandidate, Instaloader]:
        identity = MediaIdentity(
            "story_media_id",
            parsed.story_media_id,
        )
        try:
            loader = self._loader_runtime.acquire_required_session(
                staging_directory
            )
        except InstagramSessionRevisionError as error:
            raise self._required_story_session_failure(
                error,
                identity=identity,
            ) from None
        except InstagramSessionStoreError:
            raise PublicInstagramAdapterError(
                "Instagram session storage is unreadable. An administrator must re-import the Cookie file."
            ) from None

        owner = self._existing_media_owner(identity)
        if owner is not None and owner.status != "active":
            raise self._media_item_failure(
                _InactiveMediaOwnerError(
                    "The requested Instagram media owner was not found."
                ),
                identity=identity,
                kind="story",
                session_configured=True,
            )
        if owner is not None and (
            owner.username.casefold() != parsed.username.casefold()
        ):
            raise self._media_item_failure(
                _StoryOwnerMismatchError(
                    "The requested Story was not found."
                ),
                identity=identity,
                kind="story",
                session_configured=True,
            )
        if owner is None:
            try:
                owner = self._find_local_profile_by_username(
                    parsed.username
                )
            except _InactiveMediaOwnerError as error:
                raise self._media_item_failure(
                    error,
                    identity=identity,
                    kind="story",
                    session_configured=True,
                ) from None

        return (
            MediaCandidate(
                identity=identity,
                kind="story",
                session_configured=True,
                resolve=lambda: self._resolve_direct_story(
                    loader=loader,
                    parsed=parsed,
                    owner=owner,
                    identity=identity,
                    staging_directory=staging_directory,
                ),
            ),
            loader,
        )

    def _resolve_direct_story(
        self,
        *,
        loader: Instaloader,
        parsed: StoryInput,
        owner: ProfileSnapshot | None,
        identity: MediaIdentity,
        staging_directory: Path,
    ) -> ResolvedMedia:
        item = self._lookup_direct_story(
            loader=loader,
            identity=identity,
        )
        owner_profile = item.owner_profile
        if (
            str(item.mediaid) != parsed.story_media_id
            or owner_profile.username.casefold() != parsed.username.casefold()
        ):
            raise _StoryOwnerMismatchError(
                "The requested Story was not found."
            )
        resolved_owner = owner
        if resolved_owner is not None:
            self._validate_stable_owner(resolved_owner, owner_profile)
        else:
            resolved_owner = self._find_reusable_owner(owner_profile)
            if resolved_owner is None:
                resolved_owner = self._upsert_owner(
                    loader=loader,
                    profile=owner_profile,
                    staging_directory=staging_directory,
                )
        return self._resolve_story(
            loader=loader,
            item=item,
            owner=resolved_owner,
            identity=identity,
            original_url=parsed.canonical_url,
        )

    def _lookup_direct_story(
        self,
        *,
        loader: Instaloader,
        identity: MediaIdentity,
    ) -> StoryItem:
        try:
            return StoryItem.from_mediaid(
                loader.context,
                int(identity.value),
            )
        except BadResponseException as error:
            if str(error) != _MISSING_STORY_METADATA:
                raise
            raise MediaItemFailure(
                SafeMediaIssue(
                    identity=identity,
                    kind="story",
                    error_code="instagram_not_found",
                    safe_message=MEDIA_NOT_FOUND,
                    exception_class_chain=(error.__class__.__name__,),
                )
            ) from None

    def _existing_media_owner(
        self,
        identity: MediaIdentity,
    ) -> ProfileSnapshot | None:
        media = self._library.find_media_by_identity(identity)
        if media is None:
            return None
        return self._library.get_profile(media.owner_profile_id)

    def _find_reusable_owner(
        self,
        profile: Profile,
    ) -> ProfileSnapshot | None:
        instagram_user_id = str(profile.userid)
        stored = self._library.find_profile_by_instagram_user_id(
            instagram_user_id
        )
        if stored is not None:
            self._require_active_owner(stored)
            return stored
        stored = self._find_local_profile_by_username(profile.username)
        if stored is None:
            return None
        if stored.instagram_user_id is None:
            return stored
        if stored.instagram_user_id != instagram_user_id:
            raise _MediaOwnerMismatchError(
                "The requested Instagram media item was not found."
            )
        return stored

    def _find_local_profile_by_username(
        self,
        username: str,
    ) -> ProfileSnapshot | None:
        stored = self._library.find_profile_by_username(username)
        if stored is not None:
            self._require_active_owner(stored)
            return stored
        normalized = username.casefold()
        matches = tuple(
            profile
            for profile in self._library.list_profiles()
            if profile.username.casefold() == normalized
        )
        active = next(
            (profile for profile in matches if profile.status == "active"),
            None,
        )
        if active is not None:
            return active
        if matches:
            raise _InactiveMediaOwnerError(
                "The requested Instagram media owner was not found."
            )
        return None

    @staticmethod
    def _require_active_owner(owner: ProfileSnapshot) -> None:
        if owner.status != "active":
            raise _InactiveMediaOwnerError(
                "The requested Instagram media owner was not found."
            )

    @staticmethod
    def _validate_stable_owner(
        owner: ProfileSnapshot,
        profile: Profile,
    ) -> None:
        PublicInstaloaderAdapter._require_active_owner(owner)
        matches = (
            owner.username.casefold() == profile.username.casefold()
            if owner.instagram_user_id is None
            else owner.instagram_user_id == str(profile.userid)
        )
        if not matches:
            raise _MediaOwnerMismatchError(
                "The requested Instagram media item was not found."
            )

    def _story_candidate(
        self,
        *,
        loader: Instaloader,
        item: StoryItem,
        owner: ProfileSnapshot,
        identity: MediaIdentity,
        original_url: str,
    ) -> MediaCandidate:
        return MediaCandidate(
            identity=identity,
            kind="story",
            session_configured=True,
            resolve=lambda: self._resolve_story(
                loader=loader,
                item=item,
                owner=owner,
                identity=identity,
                original_url=original_url,
            ),
        )

    def _resolve_story(
        self,
        *,
        loader: Instaloader,
        item: StoryItem,
        owner: ProfileSnapshot,
        identity: MediaIdentity,
        original_url: str,
    ) -> ResolvedMedia:
        owner_profile = item.owner_profile
        self._validate_stable_owner(owner, owner_profile)
        instagram_user_id = str(owner_profile.userid)
        published_at = self._as_utc(item.date_utc)
        expires_at = self._as_utc(item.expiring_utc)
        if expires_at <= datetime.now(UTC):
            raise _StoryExpiredError(
                "The requested Story was not found because it expired."
            )
        is_video = item.is_video
        caption = item.caption or ""

        def download(active_loader: Instaloader, target: str) -> None:
            active_loader.download_storyitem(item, target=target)

        return ResolvedMedia(
            identity=identity,
            kind="story",
            instagram_media_id=identity.value,
            shortcode=None,
            profile_id=owner.id,
            instagram_user_id=instagram_user_id,
            owner_username=owner_profile.username,
            caption=caption,
            accessibility_caption="",
            published_at=published_at,
            story_expires_at=expires_at,
            original_url=original_url,
            content_kinds=("video" if is_video else "image",),
            download=download,
        )

    @staticmethod
    def _media_item_failure(
        error: BaseException,
        *,
        identity: MediaIdentity,
        kind: str,
        session_configured: bool,
    ) -> MediaItemFailure:
        return MediaItemFailure(
            classify_media_issue(
                error,
                session_configured=session_configured,
                target="media",
                identity=identity,
                kind=kind,
            )
        )

    @staticmethod
    def _required_story_session_failure(
        error: InstagramSessionRevisionError,
        *,
        identity: MediaIdentity,
    ) -> MediaItemFailure:
        session_configured = error.session_configured is True
        return MediaItemFailure(
            SafeMediaIssue(
                identity=identity,
                kind="story",
                error_code=(
                    "instagram_session_rejected"
                    if session_configured
                    else "instagram_access_denied"
                ),
                safe_message=(
                    SESSION_REJECTED
                    if session_configured
                    else ANONYMOUS_REJECTED
                ),
                exception_class_chain=(error.__class__.__name__,),
            )
        )

    def sync_profile(self, profile_id: str, job_id: str) -> ProfileSyncResult:
        """Refresh one tracked profile and download its missing public media."""
        stored_profile = self._library.get_profile(profile_id)
        if stored_profile is None:
            raise PublicInstagramAdapterError("The requested profile no longer exists.")
        if not stored_profile.tracked or stored_profile.status != "active":
            self._report(
                0,
                0,
                "Profile synchronization is stopped.",
                phase="profile_preflight",
            )
            return ProfileSyncResult(
                processed=0,
                total=0,
                issue_count=0,
                stopped=True,
            )

        staging_directory = self._staging_directory(job_id)
        self._recreate_directory(staging_directory)
        try:
            loader = self._acquire_required_loader(staging_directory)
            self._report(
                0,
                None,
                "Refreshing public Instagram profile.",
                phase="profile_preflight",
            )
            profile = self._profile_lookup_resolver.resolve(
                loader.context,
                stored_profile.username,
            )
            profile_data = self._normalize_profile(
                profile,
                authenticated=True,
            )
            self._refresh_profile_avatar(
                loader=loader,
                profile=profile,
                profile_id=profile_id,
                staging_directory=staging_directory,
            )
            refreshed_profile = self._library.update_profile_metadata(
                profile_id=profile_id,
                instagram_user_id=str(profile_data.instagram_user_id),
                username=profile_data.username,
                full_name=profile_data.full_name,
                biography=profile_data.biography,
                profile_pic_url=profile_data.profile_pic_url,
                now=datetime.now(UTC),
            )
            result = ProfileSyncCoordinator(
                source=_PublicProfileMediaSource(
                    adapter=self,
                    loader=loader,
                    owner=refreshed_profile,
                ),
                processor=self._processor(loader),
                progress=lambda current, total, phase, status_text: self._report(
                    current,
                    total,
                    status_text,
                    phase=phase,
                ),
                record_issue=self._issue,
                is_syncable=lambda: self._profile_is_syncable(profile_id),
                monotonic=time.monotonic,
                pause_between_new_media=lambda: time.sleep(
                    random.uniform(1, 3)
                ),
                time_slice_seconds=_PROFILE_SYNC_TIME_SLICE_SECONDS,
            ).run(
                profile=profile,
                job_id=job_id,
            )
        except (AbortDownloadException, InstaloaderException) as error:
            self._record_sync_result(profile_id, succeeded=False)
            raise PublicInstagramAdapterError(
                classify_instaloader_error(
                    error,
                    session_configured=True,
                    target="profile",
                )
            ) from error
        except Exception:
            self._record_sync_result(profile_id, succeeded=False)
            raise
        else:
            self._record_sync_result(profile_id, succeeded=True)
            return result
        finally:
            self._remove_directory(staging_directory)

    def _refresh_profile_avatar(
        self,
        *,
        loader: Instaloader,
        profile: Profile,
        profile_id: str,
        staging_directory: Path,
    ) -> None:
        avatar_url = profile.profile_pic_url
        if not avatar_url:
            raise PublicInstagramAdapterError(
                "Instagram profile avatar metadata is incomplete."
            )

        response = loader.context.get_raw(avatar_url)
        try:
            content_type = (
                response.headers.get("Content-Type", "")
                .partition(";")[0]
                .strip()
                .casefold()
            )
            expected_prefix_kind = {
                PROFILE_AVATAR_MEDIA_TYPE: "jpeg",
                PROFILE_AVATAR_WEBP_MEDIA_TYPE: "webp",
            }.get(content_type)
            if expected_prefix_kind is None:
                _log_invalid_profile_avatar_response(
                    response=response,
                    avatar_url=avatar_url,
                    profile_id=profile_id,
                    normalized_content_type=content_type,
                )
                raise PublicInstagramAdapterError(
                    "Instagram returned an invalid profile avatar image."
                )

            try:
                prefix = response.raw.read(_AVATAR_DIAGNOSTIC_PREFIX_BYTES)
            except Exception:  # noqa: BLE001 - preserve the stable adapter error
                prefix = None
            if (
                not isinstance(prefix, bytes)
                or _avatar_prefix_kind(prefix) != expected_prefix_kind
            ):
                _log_invalid_profile_avatar_response(
                    response=response,
                    avatar_url=avatar_url,
                    profile_id=profile_id,
                    normalized_content_type=content_type,
                    prefix=prefix,
                )
                raise PublicInstagramAdapterError(
                    "Instagram returned an invalid profile avatar image."
                )

            final_path = profile_avatar_path(
                self._media_root,
                profile_id,
                content_type,
            )
            staged_path = self._contained_path(
                staging_directory,
                f"avatar{final_path.suffix}",
            )
            partial_path = self._contained_path(
                staging_directory,
                f".avatar{final_path.suffix}.partial",
            )
            with partial_path.open("xb") as avatar_file:
                avatar_file.write(prefix)
                shutil.copyfileobj(response.raw, avatar_file)
                avatar_file.flush()
                os.fsync(avatar_file.fileno())
            os.replace(partial_path, staged_path)

            final_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged_path, final_path)
            for candidate in profile_avatar_candidates(
                self._media_root,
                profile_id,
            ):
                if candidate.path != final_path:
                    candidate.path.unlink(missing_ok=True)
        finally:
            response.close()

    def _profile_post_candidate(
        self,
        *,
        loader: Instaloader,
        post: Post,
        kind: MediaKind,
        owner: ProfileSnapshot | None,
    ) -> MediaCandidate:
        return MediaCandidate(
            identity=MediaIdentity("shortcode", post.shortcode),
            kind=kind,
            session_configured=loader.context.is_logged_in,
            resolve=lambda: self._resolve_post(
                loader=loader,
                post=post,
                kind=kind,
                original_url=self._canonical_original_url(post.shortcode, kind),
                owner=owner,
            ),
            published_at_hint=self._as_utc(post.date_utc),
        )

    def _resolve_shortcode(
        self,
        *,
        loader: Instaloader,
        shortcode: str,
        kind: MediaKind,
        original_url: str | None,
    ) -> ResolvedMedia:
        self._report(0, None, "Loading public Instagram media.")
        post = Post.from_shortcode(loader.context, shortcode)
        return self._resolve_post(
            loader=loader,
            post=post,
            kind=kind,
            original_url=original_url,
        )

    def _resolve_post(
        self,
        *,
        loader: Instaloader,
        post: Post,
        kind: MediaKind,
        original_url: str | None,
        owner: ProfileSnapshot | None = None,
    ) -> ResolvedMedia:
        resolved_owner = owner or self._upsert_owner(
            loader=loader,
            profile=post.owner_profile,
            staging_directory=Path(loader.dirname_pattern),
        )
        instagram_user_id = str(post.owner_profile.userid)

        def download(active_loader: Instaloader, target: str) -> None:
            active_loader.download_post(post, target=target)

        return ResolvedMedia(
            identity=MediaIdentity("shortcode", post.shortcode),
            kind=kind,
            instagram_media_id=str(post.mediaid),
            shortcode=post.shortcode,
            profile_id=resolved_owner.id,
            instagram_user_id=instagram_user_id,
            owner_username=post.owner_profile.username,
            caption=post.caption or "",
            accessibility_caption=post.accessibility_caption or "",
            published_at=post.date_utc.replace(tzinfo=UTC),
            story_expires_at=None,
            original_url=(
                original_url
                or self._canonical_original_url(post.shortcode, kind)
            ),
            content_kinds=self._post_content_kinds(post),
            download=download,
        )

    @staticmethod
    def _post_content_kinds(post: Post) -> tuple[ContentKind, ...]:
        if post.typename == "GraphSidecar":
            return tuple(
                "video" if node.is_video else "image"
                for node in post.get_sidecar_nodes()
            )
        return ("video" if post.is_video else "image",)

    def _processor(self, loader: Instaloader) -> MediaProcessor:
        return MediaProcessor(
            data_root=self._data_root,
            media_root=self._media_root,
            jobs_root=self._jobs_root,
            library=self._library,
            loader=loader,
        )

    def _upsert_owner(
        self,
        *,
        loader: Instaloader,
        profile: Profile,
        staging_directory: Path,
    ) -> ProfileSnapshot:
        data = self._normalize_profile(
            profile,
            authenticated=loader.context.is_logged_in,
        )
        instagram_user_id = str(data.instagram_user_id)
        stored = self._library.find_profile_by_instagram_user_id(
            instagram_user_id
        )
        if stored is None:
            stored = self._library.find_profile_by_username(data.username)
        if stored is None:
            stored = self._library.upsert_profile_stub(
                username=data.username,
                tracked=False,
                now=datetime.now(UTC),
            )
        owner = self._library.update_profile_metadata(
            profile_id=stored.id,
            instagram_user_id=instagram_user_id,
            username=data.username,
            full_name=data.full_name,
            biography=data.biography,
            profile_pic_url=data.profile_pic_url,
            now=datetime.now(UTC),
        )
        if stored_profile_avatar(self._media_root, owner.id) is None:
            try:
                self._refresh_profile_avatar(
                    loader=loader,
                    profile=profile,
                    profile_id=owner.id,
                    staging_directory=staging_directory,
                )
            except (InstaloaderException, PublicInstagramAdapterError):
                self._report(
                    0,
                    None,
                    "Profile avatar was unavailable; continuing media download.",
                )
        return owner

    def _normalize_profile(
        self,
        profile: Profile,
        *,
        authenticated: bool,
    ) -> PublicProfile:
        if profile.is_private and (
            not authenticated or not profile.followed_by_viewer
        ):
            raise PublicInstagramAdapterError(
                "This private Instagram profile is not accessible to the imported account."
            )
        return PublicProfile(
            instagram_user_id=profile.userid,
            username=profile.username,
            full_name=profile.full_name,
            biography=profile.biography,
            profile_pic_url=profile.profile_pic_url,
        )

    def _profile_is_syncable(self, profile_id: str) -> bool:
        profile = self._library.get_profile(profile_id)
        return (
            profile is not None
            and profile.tracked
            and profile.status == "active"
        )

    def _record_sync_result(self, profile_id: str, *, succeeded: bool) -> None:
        if self._library.get_profile(profile_id) is not None:
            self._library.set_profile_sync_result(
                profile_id=profile_id,
                succeeded=succeeded,
                now=datetime.now(UTC),
            )

    def _acquire_loader(self, staging_directory: Path) -> tuple[Instaloader, bool]:
        try:
            return self._loader_runtime.acquire(staging_directory)
        except InstagramSessionStoreError:
            raise PublicInstagramAdapterError(
                "Instagram session storage is unreadable. An administrator must re-import the Cookie file."
            ) from None

    def _acquire_required_loader(self, staging_directory: Path) -> Instaloader:
        try:
            return self._loader_runtime.acquire_required_session(
                staging_directory
            )
        except InstagramSessionRevisionError as error:
            raise PublicInstagramAdapterError(
                SESSION_REJECTED
                if error.session_configured is True
                else ANONYMOUS_REJECTED
            ) from None
        except InstagramSessionStoreError:
            raise PublicInstagramAdapterError(
                "Instagram session storage is unreadable. An administrator must re-import the Cookie file."
            ) from None

    def _metadata_staging_directory(self) -> Path:
        return self._contained_path(self._jobs_root, "profile-metadata")

    def _staging_directory(self, job_id: str) -> Path:
        return self._contained_path(self._jobs_root, self._safe_component(job_id))

    def _require_data_subdirectory(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self._data_root):
            raise ValueError("Worker runtime directories must be below data_root.")
        return resolved

    @staticmethod
    def _contained_path(root: Path, *parts: str) -> Path:
        candidate = root.joinpath(*parts).resolve()
        if not candidate.is_relative_to(root.resolve()):
            raise PublicInstagramAdapterError(
                "Worker path escaped its runtime directory."
            )
        return candidate

    @staticmethod
    def _safe_component(value: str) -> str:
        if not value or Path(value).name != value or value in {".", ".."}:
            raise PublicInstagramAdapterError(
                "Invalid worker filesystem path component."
            )
        return value

    @staticmethod
    def _normalize_kind(expected_kind: str | None) -> MediaKind:
        return "reel" if expected_kind == "reel" else "post"

    @staticmethod
    def _canonical_original_url(shortcode: str, kind: MediaKind) -> str:
        route = "reel" if kind == "reel" else "p"
        return f"https://www.instagram.com/{route}/{shortcode}/"

    @staticmethod
    def _canonical_story_url(username: str, story_media_id: str) -> str:
        return (
            "https://www.instagram.com/stories/"
            f"{username}/{story_media_id}/"
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return (
            value.replace(tzinfo=UTC)
            if value.tzinfo is None
            else value.astimezone(UTC)
        )

    @staticmethod
    def _recreate_directory(directory: Path) -> None:
        PublicInstaloaderAdapter._remove_directory(directory)
        directory.mkdir(parents=True, exist_ok=False)

    @staticmethod
    def _remove_directory(directory: Path) -> None:
        if not directory.exists():
            return
        if not directory.is_dir():
            raise PublicInstagramAdapterError("Worker staging path is not a directory.")
        shutil.rmtree(directory)

    def _report(
        self,
        current: int,
        total: int | None,
        status_text: str,
        *,
        phase: str | None = None,
    ) -> None:
        if self._progress_supports_phase:
            self._progress(current, total, phase, status_text)
        else:
            self._progress(current, total, status_text)

    @staticmethod
    def _supports_phase(progress: ProgressCallback) -> bool:
        try:
            signature(progress).bind(0, None, "phase", "status")
        except (TypeError, ValueError):
            return False
        return True
