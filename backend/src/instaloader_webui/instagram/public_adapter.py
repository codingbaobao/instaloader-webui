"""Normalize public Instaloader downloads into the local media library."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import shutil
from typing import Literal

from instaloader import Instaloader, InstaloaderException, Post, Profile

from instaloader_webui.db.library_repositories import (
    LibraryRepository,
    MediaSnapshot,
    NormalizedAsset,
    NormalizedMedia,
    ProfileSnapshot,
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


@dataclass(frozen=True, slots=True)
class AssetFinalization:
    assets: tuple[NormalizedAsset, ...]
    final_directory: Path
    backup_directory: Path | None


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
    ) -> None:
        self._data_root = data_root.resolve()
        self._media_root = self._require_data_subdirectory(media_root)
        self._jobs_root = self._require_data_subdirectory(jobs_root)
        self._library = library
        self._progress = progress

    def fetch_profile(self, username: str) -> PublicProfile:
        """Load normalized metadata for one publicly accessible profile."""
        loader = self._new_loader(self._metadata_staging_directory())
        try:
            profile = Profile.from_username(loader.context, username)
            return self._normalize_profile(profile)
        except InstaloaderException as error:
            raise PublicInstagramAdapterError(
                "This Instagram profile is unavailable or private."
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
        staging_directory = self._staging_directory(job_id)
        self._recreate_directory(staging_directory)
        loader = self._new_loader(staging_directory)
        kind = self._normalize_kind(expected_kind)
        try:
            self._report(0, None, "Loading public Instagram media.")
            post = Post.from_shortcode(loader.context, shortcode)
            return self._download_post(
                loader=loader,
                post=post,
                job_id=job_id,
                kind=kind,
                original_url=original_url,
            )
        except InstaloaderException as error:
            raise PublicInstagramAdapterError(
                "This Instagram media item is unavailable."
            ) from error
        finally:
            self._remove_directory(staging_directory)

    def sync_profile(self, profile_id: str, job_id: str) -> int:
        """Refresh one tracked profile and download its missing public media."""
        stored_profile = self._library.get_profile(profile_id)
        if stored_profile is None:
            raise PublicInstagramAdapterError("The requested profile no longer exists.")
        if not stored_profile.tracked or stored_profile.status != "active":
            self._report(0, 0, "Profile synchronization is no longer active.")
            return 0

        staging_directory = self._staging_directory(job_id)
        self._recreate_directory(staging_directory)
        try:
            loader = self._new_loader(staging_directory)
            self._report(0, None, "Refreshing public Instagram profile.")
            profile = Profile.from_username(loader.context, stored_profile.username)
            profile_data = self._normalize_profile(profile)
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
                "This Instagram profile is unavailable or private."
            ) from error
        except Exception:
            self._record_sync_result(profile_id, succeeded=False)
            raise
        else:
            self._record_sync_result(profile_id, succeeded=True)
            return inspected
        finally:
            self._remove_directory(staging_directory)

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
                return inspected
            iterator = iter(iterator_factory())
            while self._profile_is_syncable(profile_id):
                try:
                    post = next(iterator)
                except StopIteration:
                    break
                inspected += 1
                self._sync_post(
                    loader=loader,
                    post=post,
                    job_id=job_id,
                    kind=kind,
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
        existing = self._library.find_media_by_shortcode(post.shortcode)
        if existing is not None and self._has_local_assets(existing):
            if kind == "reel" and existing.kind != "reel":
                self._library.set_media_kind(
                    shortcode=post.shortcode,
                    kind="reel",
                    now=datetime.now(UTC),
                )
            return
        self._download_post(
            loader=loader,
            post=post,
            job_id=job_id,
            kind=kind,
            original_url=self._canonical_original_url(post.shortcode, kind),
        )

    def _download_post(
        self,
        *,
        loader: Instaloader,
        post: Post,
        job_id: str,
        kind: MediaKind,
        original_url: str | None,
    ) -> MediaSnapshot:
        staging_directory = self._staging_directory(job_id)
        self._recreate_directory(staging_directory)
        owner = self._upsert_owner(post.owner_profile)
        self._report(0, None, f"Downloading public Instagram media {post.shortcode}.")
        loader.download_post(post, target=owner.username)
        staged_assets = self._discover_assets(staging_directory, post.shortcode)
        if not staged_assets:
            raise PublicInstagramAdapterError(
                "Instagram did not produce a local media file."
            )
        finalization = self._finalize_assets(
            staged_assets=staged_assets,
            instagram_user_id=owner.instagram_user_id,
            shortcode=post.shortcode,
            job_id=job_id,
        )
        try:
            persisted = self._library.upsert_media(
                normalized=NormalizedMedia(
                    instagram_media_id=str(post.mediaid),
                    shortcode=post.shortcode,
                    kind=kind,
                    caption=post.caption or "",
                    accessibility_caption=post.accessibility_caption or "",
                    published_at=post.date_utc.replace(tzinfo=UTC),
                    original_url=original_url
                    or self._canonical_original_url(post.shortcode, kind),
                ),
                profile_id=owner.id,
                assets=finalization.assets,
                now=datetime.now(UTC),
            )
        except Exception:
            self._rollback_asset_finalization(finalization)
            raise
        self._commit_asset_finalization(finalization)
        self._report(
            len(finalization.assets),
            len(finalization.assets),
            "Saved public Instagram media.",
        )
        return persisted

    def _upsert_owner(self, profile: Profile) -> ProfileSnapshot:
        data = self._normalize_profile(profile)
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
        return self._library.update_profile_metadata(
            profile_id=stored.id,
            instagram_user_id=instagram_user_id,
            username=data.username,
            full_name=data.full_name,
            biography=data.biography,
            profile_pic_url=data.profile_pic_url,
            now=datetime.now(UTC),
        )

    def _normalize_profile(self, profile: Profile) -> PublicProfile:
        if profile.is_private:
            raise PublicInstagramAdapterError(
                "This Instagram profile is unavailable or private."
            )
        return PublicProfile(
            instagram_user_id=profile.userid,
            username=profile.username,
            full_name=profile.full_name,
            biography=profile.biography,
            profile_pic_url=profile.profile_pic_url,
        )

    def _discover_assets(self, directory: Path, shortcode: str) -> tuple[Path, ...]:
        files = tuple(
            path
            for path in directory.iterdir()
            if path.is_file()
            and path.suffix.casefold() in {".jpg", ".mp4"}
            and path.name.startswith(shortcode)
        )
        return tuple(
            sorted(files, key=lambda path: self._asset_sort_key(path, shortcode))
        )

    def _finalize_assets(
        self,
        *,
        staged_assets: Iterable[Path],
        instagram_user_id: str | None,
        shortcode: str,
        job_id: str,
    ) -> AssetFinalization:
        if not instagram_user_id:
            raise PublicInstagramAdapterError(
                "Instagram media owner metadata is incomplete."
            )
        final_directory = self._media_directory(instagram_user_id, shortcode)
        replacement_directory = self._replacement_directory(
            final_directory, shortcode, job_id
        )
        backup_directory = self._backup_directory(
            final_directory, shortcode, job_id
        )
        self._remove_path(backup_directory)
        self._recreate_directory(replacement_directory)
        finalized_paths: list[Path] = []
        preserved_directory: Path | None = None
        new_final_installed = False
        try:
            for staged_asset in staged_assets:
                final_path = replacement_directory / staged_asset.name
                shutil.move(str(staged_asset), str(final_path))
                finalized_paths.append(final_path)
            if final_directory.exists():
                final_directory.replace(backup_directory)
                preserved_directory = backup_directory
            replacement_directory.replace(final_directory)
            new_final_installed = True
            completed_paths = tuple(
                final_directory / path.name for path in finalized_paths
            )
            assets = tuple(
                NormalizedAsset(
                    relative_path=path.relative_to(self._media_root).as_posix(),
                    mime_type=(
                        "image/jpeg"
                        if path.suffix.casefold() == ".jpg"
                        else "video/mp4"
                    ),
                    kind=(
                        "image"
                        if path.suffix.casefold() == ".jpg"
                        else "video"
                    ),
                    position=position,
                    file_size=path.stat().st_size,
                )
                for position, path in enumerate(completed_paths)
            )
        except Exception:
            self._remove_directory(replacement_directory)
            if new_final_installed or preserved_directory is not None:
                self._rollback_asset_finalization(
                    AssetFinalization(
                        assets=(),
                        final_directory=final_directory,
                        backup_directory=preserved_directory,
                    )
                )
            raise
        return AssetFinalization(
            assets=assets,
            final_directory=final_directory,
            backup_directory=preserved_directory,
        )

    @staticmethod
    def _rollback_asset_finalization(finalization: AssetFinalization) -> None:
        PublicInstaloaderAdapter._remove_path(finalization.final_directory)
        if (
            finalization.backup_directory is not None
            and finalization.backup_directory.exists()
        ):
            finalization.backup_directory.replace(
                finalization.final_directory
            )

    @staticmethod
    def _commit_asset_finalization(finalization: AssetFinalization) -> None:
        if finalization.backup_directory is not None:
            PublicInstaloaderAdapter._remove_path(
                finalization.backup_directory
            )

    def _profile_is_syncable(self, profile_id: str) -> bool:
        profile = self._library.get_profile(profile_id)
        return (
            profile is not None
            and profile.tracked
            and profile.status == "active"
        )

    def _has_local_assets(self, media: MediaSnapshot) -> bool:
        return bool(media.assets) and all(
            self._asset_path(asset.relative_path).is_file() for asset in media.assets
        )

    def _record_sync_result(self, profile_id: str, *, succeeded: bool) -> None:
        if self._library.get_profile(profile_id) is not None:
            self._library.set_profile_sync_result(
                profile_id=profile_id,
                succeeded=succeeded,
                now=datetime.now(UTC),
            )

    def _new_loader(self, staging_directory: Path) -> Instaloader:
        return Instaloader(
            dirname_pattern=str(staging_directory),
            filename_pattern="{shortcode}",
            download_pictures=True,
            download_videos=True,
            download_video_thumbnails=True,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            post_metadata_txt_pattern="",
            quiet=True,
        )

    def _metadata_staging_directory(self) -> Path:
        return self._contained_path(self._jobs_root, "profile-metadata")

    def _staging_directory(self, job_id: str) -> Path:
        return self._contained_path(self._jobs_root, self._safe_component(job_id))

    def _media_directory(self, instagram_user_id: str, shortcode: str) -> Path:
        return self._contained_path(
            self._media_root,
            "profiles",
            self._safe_component(instagram_user_id),
            self._safe_component(shortcode),
        )

    def _replacement_directory(
        self, final_directory: Path, shortcode: str, job_id: str
    ) -> Path:
        return self._contained_path(
            final_directory.parent,
            (
                f".{self._safe_component(shortcode)}-"
                f"{self._safe_component(job_id)}.partial"
            ),
        )

    def _backup_directory(
        self, final_directory: Path, shortcode: str, job_id: str
    ) -> Path:
        return self._contained_path(
            final_directory.parent,
            (
                f".{self._safe_component(shortcode)}-"
                f"{self._safe_component(job_id)}.backup"
            ),
        )

    def _asset_path(self, relative_path: str) -> Path:
        candidate = (self._media_root / relative_path).resolve()
        if not candidate.is_relative_to(self._media_root):
            raise PublicInstagramAdapterError(
                "Stored media path is outside the library."
            )
        return candidate

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
    def _asset_sort_key(path: Path, shortcode: str) -> tuple[int, int, str]:
        suffix = path.stem.removeprefix(shortcode)
        if suffix.startswith("_") and suffix[1:].isdigit():
            sequence = int(suffix[1:])
        else:
            sequence = 0
        extension_rank = 0 if path.suffix.casefold() == ".jpg" else 1
        return sequence, extension_rank, path.name

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

    @staticmethod
    def _remove_path(path: Path) -> None:
        if not path.exists():
            return
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()

    def _report(self, current: int, total: int | None, status_text: str) -> None:
        self._progress(current, total, status_text)
