"""Resolve public Instaloader inputs for the shared local media processor."""

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from instaloader import Instaloader, InstaloaderException, Post, Profile

from instaloader_webui.db.library_repositories import (
    LibraryRepository,
    MediaIdentity,
    MediaSnapshot,
    ProfileSnapshot,
)
from instaloader_webui.instagram.errors import classify_instaloader_error
from instaloader_webui.instagram.media_processor import MediaProcessor
from instaloader_webui.instagram.media_types import (
    ContentKind,
    MediaCandidate,
    ResolvedMedia,
)
from instaloader_webui.instagram.session_store import InstagramSessionStoreError
from instaloader_webui.instagram.worker_runtime import WorkerInstaloaderRuntime
from instaloader_webui.services.profile_avatars import (
    PROFILE_AVATAR_MEDIA_TYPE,
    profile_avatar_path,
)

MediaKind = Literal["post", "reel"]
ProgressCallback = Callable[[int, int | None, str], None]


class PublicInstagramAdapterError(RuntimeError):
    """A concise, user-safe failure while accessing public Instagram content."""


@dataclass(frozen=True, slots=True)
class PublicProfile:
    instagram_user_id: int
    username: str
    full_name: str
    biography: str
    profile_pic_url: str | None


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
    ) -> None:
        self._data_root = data_root.resolve()
        self._media_root = self._require_data_subdirectory(media_root)
        self._jobs_root = self._require_data_subdirectory(jobs_root)
        self._library = library
        self._progress = progress
        self._loader_runtime = loader_runtime

    def fetch_profile(self, username: str) -> PublicProfile:
        """Load normalized metadata for one publicly accessible profile."""
        loader, session_configured = self._acquire_loader(
            self._metadata_staging_directory()
        )
        try:
            profile = Profile.from_username(loader.context, username)
            return self._normalize_profile(
                profile,
                authenticated=session_configured,
            )
        except InstaloaderException as error:
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
        staging_directory = self._staging_directory(job_id)
        self._recreate_directory(staging_directory)
        try:
            loader, session_configured = self._acquire_loader(staging_directory)
            candidate = MediaCandidate(
                identity=MediaIdentity("shortcode", shortcode),
                kind=kind,
                session_configured=session_configured,
                resolve=lambda: self._resolve_shortcode(
                    loader=loader,
                    shortcode=shortcode,
                    kind=kind,
                    original_url=original_url,
                ),
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

    def sync_profile(self, profile_id: str, job_id: str) -> int:
        """Refresh one tracked profile and download its missing public media."""
        stored_profile = self._library.get_profile(profile_id)
        if stored_profile is None:
            raise PublicInstagramAdapterError("The requested profile no longer exists.")
        if not stored_profile.tracked or stored_profile.status != "active":
            self._report(0, 0, "Profile synchronization is stopped.")
            return 0

        staging_directory = self._staging_directory(job_id)
        self._recreate_directory(staging_directory)
        try:
            loader, session_configured = self._acquire_loader(staging_directory)
            self._report(0, None, "Refreshing public Instagram profile.")
            profile = Profile.from_username(loader.context, stored_profile.username)
            profile_data = self._normalize_profile(
                profile,
                authenticated=session_configured,
            )
            self._refresh_profile_avatar(
                loader=loader,
                profile=profile,
                profile_id=profile_id,
                staging_directory=staging_directory,
            )
            self._library.update_profile_metadata(
                profile_id=profile_id,
                instagram_user_id=str(profile_data.instagram_user_id),
                username=profile_data.username,
                full_name=profile_data.full_name,
                biography=profile_data.biography,
                profile_pic_url=profile_data.profile_pic_url,
                now=datetime.now(UTC),
            )
            inspected = self._sync_iterators(
                loader=loader,
                profile=profile,
                profile_id=profile_id,
                job_id=job_id,
            )
        except InstaloaderException as error:
            self._record_sync_result(profile_id, succeeded=False)
            raise PublicInstagramAdapterError(
                classify_instaloader_error(
                    error,
                    session_configured=session_configured,
                    target="profile",
                )
            ) from error
        except Exception:
            self._record_sync_result(profile_id, succeeded=False)
            raise
        else:
            self._record_sync_result(profile_id, succeeded=True)
            return inspected
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
            if content_type != PROFILE_AVATAR_MEDIA_TYPE:
                raise PublicInstagramAdapterError(
                    "Instagram returned an invalid profile avatar image."
                )

            staged_path = self._contained_path(staging_directory, "avatar.jpg")
            partial_path = self._contained_path(
                staging_directory, ".avatar.jpg.partial"
            )
            with partial_path.open("xb") as avatar_file:
                shutil.copyfileobj(response.raw, avatar_file)
                avatar_file.flush()
                os.fsync(avatar_file.fileno())
            os.replace(partial_path, staged_path)

            final_path = profile_avatar_path(self._media_root, profile_id)
            final_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged_path, final_path)
        finally:
            response.close()

    def _sync_iterators(
        self,
        *,
        loader: Instaloader,
        profile: Profile,
        profile_id: str,
        job_id: str,
    ) -> int:
        inspected = 0
        for kind, iterator_factory in (
            ("post", profile.get_posts),
            ("reel", profile.get_reels),
        ):
            if not self._profile_is_syncable(profile_id):
                self._report(
                    inspected,
                    inspected,
                    "Profile synchronization stopped before the next media item.",
                )
                return inspected
            iterator = iter(iterator_factory())
            while True:
                if not self._profile_is_syncable(profile_id):
                    self._report(
                        inspected,
                        inspected,
                        "Profile synchronization stopped before the next media item.",
                    )
                    return inspected
                try:
                    post = next(iterator)
                except StopIteration:
                    break
                inspected += 1
                self._sync_post(
                    loader=loader,
                    post=post,
                    job_id=job_id,
                    kind=self._normalize_kind(kind),
                )
                self._report(
                    inspected,
                    None,
                    f"Inspected public Instagram {kind} {inspected}.",
                )
        return inspected

    def _sync_post(
        self, *, loader: Instaloader, post: Post, job_id: str, kind: MediaKind
    ) -> None:
        candidate = MediaCandidate(
            identity=MediaIdentity("shortcode", post.shortcode),
            kind=kind,
            session_configured=loader.context.is_logged_in,
            resolve=lambda: self._resolve_post(
                loader=loader,
                post=post,
                kind=kind,
                original_url=self._canonical_original_url(post.shortcode, kind),
            ),
        )
        self._processor(loader).process(candidate, job_id=job_id)

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
    ) -> ResolvedMedia:
        owner = self._upsert_owner(
            loader=loader,
            profile=post.owner_profile,
            staging_directory=Path(loader.dirname_pattern),
        )
        if not owner.instagram_user_id:
            raise PublicInstagramAdapterError(
                "Instagram media owner metadata is incomplete."
            )

        def download(active_loader: Instaloader, target: str) -> None:
            active_loader.download_post(post, target=target)

        return ResolvedMedia(
            identity=MediaIdentity("shortcode", post.shortcode),
            kind=kind,
            instagram_media_id=str(post.mediaid),
            shortcode=post.shortcode,
            profile_id=owner.id,
            instagram_user_id=owner.instagram_user_id,
            owner_username=owner.username,
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
        if not profile_avatar_path(self._media_root, owner.id).is_file():
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

    def _report(self, current: int, total: int | None, status_text: str) -> None:
        self._progress(current, total, status_text)
