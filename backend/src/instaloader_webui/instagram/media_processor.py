"""Atomically validate, finalize, and persist one Instagram media item."""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from instaloader import AbortDownloadException, Instaloader, InstaloaderException

from instaloader_webui.db.library_repositories import (
    AssetSnapshot,
    LibraryRepository,
    MediaSnapshot,
    NormalizedAsset,
    NormalizedMedia,
)
from instaloader_webui.instagram.media_types import (
    MediaCandidate,
    MediaProcessResult,
    ResolvedMedia,
)
from instaloader_webui.instagram.safe_issues import (
    ASSET_VALIDATION_FAILED,
    MediaItemFailure,
    SafeMediaIssue,
    classify_media_issue,
)

_SUPPORTED_SUFFIXES = frozenset({".jpg", ".mp4", ".webp"})
_SEQUENCE_SUFFIX = re.compile(r"_(?P<sequence>[1-9][0-9]*)\Z")


@dataclass(frozen=True, slots=True)
class _StagedAsset:
    path: Path
    mime_type: Literal["image/jpeg", "image/webp", "video/mp4"]
    kind: Literal["image", "video"]
    role: Literal["content", "poster"]
    position: int


@dataclass(frozen=True, slots=True)
class _AssetFinalization:
    assets: tuple[NormalizedAsset, ...]
    final_directory: Path
    backup_directory: Path | None


class MediaProcessor:
    """Run one candidate inside an isolated staging and persistence boundary."""

    def __init__(
        self,
        *,
        data_root: Path,
        media_root: Path,
        jobs_root: Path,
        library: LibraryRepository,
        loader: Instaloader,
    ) -> None:
        self._data_root = data_root.resolve()
        self._media_root = self._require_data_subdirectory(media_root)
        self._jobs_root = self._require_data_subdirectory(jobs_root)
        self._library = library
        self._loader = loader

    def process(
        self,
        candidate: MediaCandidate,
        *,
        job_id: str,
        before_network: Callable[[], None] | None = None,
    ) -> MediaProcessResult:
        """Return a complete existing item or atomically replace it from staging."""
        existing = self._library.find_media_by_identity(candidate.identity)
        if existing is not None and self._has_complete_local_assets(existing):
            reconciled = self._reconcile_existing_kind(candidate, existing)
            return MediaProcessResult(status="existing", media=reconciled)

        if before_network is not None:
            before_network()
        staging_directory = self._staging_directory(candidate, job_id)
        self._recreate_directory(staging_directory)
        self._loader.dirname_pattern = str(staging_directory)
        try:
            resolved = self._resolve(candidate)
            self._validate_resolution(candidate, resolved)
            self._download(candidate, resolved)
            staged_assets = self._map_staged_assets(candidate, resolved)
            finalization = self._finalize_assets(
                candidate=candidate,
                resolved=resolved,
                staged_assets=staged_assets,
                job_id=job_id,
            )
            try:
                persisted = self._library.upsert_media(
                    normalized=NormalizedMedia(
                        identity=resolved.identity,
                        instagram_media_id=resolved.instagram_media_id,
                        shortcode=resolved.shortcode,
                        kind=resolved.kind,
                        caption=resolved.caption,
                        accessibility_caption=resolved.accessibility_caption,
                        published_at=resolved.published_at,
                        story_expires_at=resolved.story_expires_at,
                        original_url=resolved.original_url,
                    ),
                    profile_id=resolved.profile_id,
                    assets=finalization.assets,
                    now=datetime.now(UTC),
                )
            except Exception:
                self._rollback_asset_finalization(finalization)
                raise
            self._commit_asset_finalization(finalization)
            return MediaProcessResult(status="saved", media=persisted)
        finally:
            self._remove_directory(staging_directory)

    @staticmethod
    def _resolve(candidate: MediaCandidate) -> ResolvedMedia:
        try:
            return candidate.resolve()
        except (AbortDownloadException, InstaloaderException) as error:
            raise MediaItemFailure(
                classify_media_issue(
                    error,
                    session_configured=candidate.session_configured,
                    target="media",
                    identity=candidate.identity,
                    kind=candidate.kind,
                )
            ) from None

    def _download(
        self,
        candidate: MediaCandidate,
        resolved: ResolvedMedia,
    ) -> None:
        try:
            resolved.download(self._loader, resolved.owner_username)
        except (AbortDownloadException, InstaloaderException) as error:
            raise MediaItemFailure(
                classify_media_issue(
                    error,
                    session_configured=candidate.session_configured,
                    target="media",
                    identity=candidate.identity,
                    kind=candidate.kind,
                )
            ) from None

    @staticmethod
    def _validate_resolution(
        candidate: MediaCandidate,
        resolved: ResolvedMedia,
    ) -> None:
        if (
            resolved.identity != candidate.identity
            or resolved.kind != candidate.kind
            or not resolved.instagram_user_id
            or not resolved.owner_username
        ):
            raise ValueError("Resolved media does not match its candidate.")

    def _map_staged_assets(
        self,
        candidate: MediaCandidate,
        resolved: ResolvedMedia,
    ) -> tuple[_StagedAsset, ...]:
        if not resolved.content_kinds or any(
            kind not in {"image", "video"} for kind in resolved.content_kinds
        ):
            raise self._asset_validation_failure(candidate)

        supported_files = tuple(
            sorted(
                (
                    path
                    for path in Path(self._loader.dirname_pattern).iterdir()
                    if path.is_file()
                    and path.suffix.casefold() in _SUPPORTED_SUFFIXES
                ),
                key=lambda path: path.name,
            )
        )
        mapped: list[_StagedAsset] = []
        seen_descriptors: set[tuple[str, str, int]] = set()
        for path in supported_files:
            position = self._logical_position(path, resolved)
            suffix = path.suffix.casefold()
            if suffix == ".webp" and (
                position is None
                or position < 0
                or position >= len(resolved.content_kinds)
                or resolved.content_kinds[position] != "image"
            ):
                continue
            if (
                position is None
                or position < 0
                or position >= len(resolved.content_kinds)
            ):
                raise self._asset_validation_failure(candidate)
            expected_kind = resolved.content_kinds[position]
            if expected_kind == "image" and suffix in {".jpg", ".webp"}:
                kind: Literal["image", "video"] = "image"
                role: Literal["content", "poster"] = "content"
                mime_type: Literal[
                    "image/jpeg", "image/webp", "video/mp4"
                ] = ("image/webp" if suffix == ".webp" else "image/jpeg")
            elif expected_kind == "video" and suffix == ".mp4":
                kind = "video"
                role = "content"
                mime_type = "video/mp4"
            elif expected_kind == "video" and suffix == ".jpg":
                kind = "image"
                role = "poster"
                mime_type = "image/jpeg"
            else:
                raise self._asset_validation_failure(candidate)
            descriptor = (kind, role, position)
            if descriptor in seen_descriptors:
                raise self._asset_validation_failure(candidate)
            seen_descriptors.add(descriptor)
            mapped.append(
                _StagedAsset(
                    path=path,
                    mime_type=mime_type,
                    kind=kind,
                    role=role,
                    position=position,
                )
            )

        content_positions = {
            asset.position for asset in mapped if asset.role == "content"
        }
        if content_positions != set(range(len(resolved.content_kinds))):
            raise self._asset_validation_failure(candidate)
        return tuple(
            sorted(
                mapped,
                key=lambda asset: (
                    asset.position,
                    0 if asset.role == "content" else 1,
                    asset.path.name,
                ),
            )
        )

    @staticmethod
    def _logical_position(path: Path, resolved: ResolvedMedia) -> int | None:
        stem = path.stem
        if resolved.shortcode is not None:
            if stem == resolved.shortcode:
                return 0
            prefix = f"{resolved.shortcode}_"
            if not stem.startswith(prefix):
                return None
            sequence_text = stem.removeprefix(prefix)
            if not sequence_text.isdigit():
                return None
            sequence = int(sequence_text)
            return sequence - 1 if sequence >= 1 else None
        if len(resolved.content_kinds) == 1:
            return 0
        match = _SEQUENCE_SUFFIX.search(stem)
        return int(match.group("sequence")) - 1 if match is not None else None

    def _finalize_assets(
        self,
        *,
        candidate: MediaCandidate,
        resolved: ResolvedMedia,
        staged_assets: tuple[_StagedAsset, ...],
        job_id: str,
    ) -> _AssetFinalization:
        final_directory = self._media_directory(
            resolved.instagram_user_id,
            candidate.identity.value,
        )
        replacement_directory = self._replacement_directory(
            final_directory,
            candidate.identity.value,
            job_id,
        )
        backup_directory = self._backup_directory(
            final_directory,
            candidate.identity.value,
            job_id,
        )
        self._remove_path(backup_directory)
        self._recreate_directory(replacement_directory)
        preserved_directory: Path | None = None
        new_final_installed = False
        moved_assets: list[_StagedAsset] = []
        try:
            for staged_asset in staged_assets:
                replacement_path = replacement_directory / staged_asset.path.name
                shutil.move(str(staged_asset.path), str(replacement_path))
                moved_assets.append(
                    _StagedAsset(
                        path=replacement_path,
                        mime_type=staged_asset.mime_type,
                        kind=staged_asset.kind,
                        role=staged_asset.role,
                        position=staged_asset.position,
                    )
                )
            if final_directory.exists():
                final_directory.replace(backup_directory)
                preserved_directory = backup_directory
            replacement_directory.replace(final_directory)
            new_final_installed = True
            assets = tuple(
                NormalizedAsset(
                    relative_path=(
                        final_directory / staged_asset.path.name
                    ).relative_to(self._media_root).as_posix(),
                    mime_type=staged_asset.mime_type,
                    kind=staged_asset.kind,
                    role=staged_asset.role,
                    position=staged_asset.position,
                    file_size=(
                        final_directory / staged_asset.path.name
                    ).stat().st_size,
                )
                for staged_asset in moved_assets
            )
        except Exception:
            self._remove_directory(replacement_directory)
            if new_final_installed or preserved_directory is not None:
                self._rollback_asset_finalization(
                    _AssetFinalization(
                        assets=(),
                        final_directory=final_directory,
                        backup_directory=preserved_directory,
                    )
                )
            raise
        return _AssetFinalization(
            assets=assets,
            final_directory=final_directory,
            backup_directory=preserved_directory,
        )

    def _has_complete_local_assets(self, media: MediaSnapshot) -> bool:
        if not media.assets:
            return False
        contents: dict[int, AssetSnapshot] = {}
        posters: dict[int, AssetSnapshot] = {}
        for asset in media.assets:
            path = self._asset_path(asset.relative_path)
            if (
                not path.is_file()
                or asset.position < 0
                or asset.file_size <= 0
                or path.stat().st_size != asset.file_size
                or not self._asset_metadata_matches_path(asset, path)
                or self._stored_logical_position(path, media) != asset.position
            ):
                return False
            selected = contents if asset.role == "content" else posters
            if asset.position in selected:
                return False
            selected[asset.position] = asset
        if set(contents) != set(range(len(contents))):
            return False
        return all(
            poster.kind == "image"
            and poster.mime_type == "image/jpeg"
            and position in contents
            and contents[position].kind == "video"
            for position, poster in posters.items()
        )

    @staticmethod
    def _asset_metadata_matches_path(asset: AssetSnapshot, path: Path) -> bool:
        suffix = path.suffix.casefold()
        if asset.role not in {"content", "poster"}:
            return False
        if asset.role == "poster" and asset.kind != "image":
            return False
        return (
            asset.kind == "image"
            and asset.mime_type == "image/jpeg"
            and suffix == ".jpg"
        ) or (
            asset.kind == "image"
            and asset.role == "content"
            and asset.mime_type == "image/webp"
            and suffix == ".webp"
        ) or (
            asset.kind == "video"
            and asset.role == "content"
            and asset.mime_type == "video/mp4"
            and suffix == ".mp4"
        )

    @staticmethod
    def _stored_logical_position(path: Path, media: MediaSnapshot) -> int | None:
        if media.shortcode is None:
            return 0 if all(asset.position == 0 for asset in media.assets) else None
        if path.stem == media.shortcode:
            return 0
        prefix = f"{media.shortcode}_"
        if not path.stem.startswith(prefix):
            return None
        sequence_text = path.stem.removeprefix(prefix)
        return int(sequence_text) - 1 if sequence_text.isdigit() else None

    def _reconcile_existing_kind(
        self,
        candidate: MediaCandidate,
        existing: MediaSnapshot,
    ) -> MediaSnapshot:
        if (
            candidate.kind == "reel"
            and existing.kind != "reel"
            and candidate.identity.identity_type == "shortcode"
        ):
            updated = self._library.set_media_kind(
                shortcode=candidate.identity.value,
                kind="reel",
                now=datetime.now(UTC),
            )
            if updated is not None:
                return updated
        return existing

    @staticmethod
    def _asset_validation_failure(candidate: MediaCandidate) -> MediaItemFailure:
        return MediaItemFailure(
            SafeMediaIssue(
                identity=candidate.identity,
                kind=candidate.kind,
                error_code="asset_validation_failed",
                safe_message=ASSET_VALIDATION_FAILED,
                exception_class_chain=("AssetValidationError",),
            )
        )

    def _staging_directory(
        self,
        candidate: MediaCandidate,
        job_id: str,
    ) -> Path:
        return self._contained_path(
            self._jobs_root,
            self._safe_component(job_id),
            self._safe_component(candidate.identity.value),
        )

    def _media_directory(
        self,
        instagram_user_id: str,
        identity_value: str,
    ) -> Path:
        return self._contained_path(
            self._media_root,
            "profiles",
            self._safe_component(instagram_user_id),
            self._safe_component(identity_value),
        )

    def _replacement_directory(
        self,
        final_directory: Path,
        identity_value: str,
        job_id: str,
    ) -> Path:
        return self._contained_path(
            final_directory.parent,
            (
                f".{self._safe_component(identity_value)}-"
                f"{self._safe_component(job_id)}.partial"
            ),
        )

    def _backup_directory(
        self,
        final_directory: Path,
        identity_value: str,
        job_id: str,
    ) -> Path:
        return self._contained_path(
            final_directory.parent,
            (
                f".{self._safe_component(identity_value)}-"
                f"{self._safe_component(job_id)}.backup"
            ),
        )

    def _asset_path(self, relative_path: str) -> Path:
        candidate = (self._media_root / relative_path).resolve()
        if not candidate.is_relative_to(self._media_root):
            raise ValueError("Stored media path is outside the library.")
        return candidate

    def _require_data_subdirectory(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self._data_root):
            raise ValueError("Media processor directories must be below data_root.")
        return resolved

    @staticmethod
    def _contained_path(root: Path, *parts: str) -> Path:
        candidate = root.joinpath(*parts).resolve()
        if not candidate.is_relative_to(root.resolve()):
            raise ValueError("Media processor path escaped its runtime directory.")
        return candidate

    @staticmethod
    def _safe_component(value: str) -> str:
        if not value or Path(value).name != value or value in {".", ".."}:
            raise ValueError("Invalid media processor filesystem path component.")
        return value

    @staticmethod
    def _rollback_asset_finalization(finalization: _AssetFinalization) -> None:
        MediaProcessor._remove_path(finalization.final_directory)
        if (
            finalization.backup_directory is not None
            and finalization.backup_directory.exists()
        ):
            finalization.backup_directory.replace(finalization.final_directory)

    @staticmethod
    def _commit_asset_finalization(finalization: _AssetFinalization) -> None:
        if finalization.backup_directory is not None:
            MediaProcessor._remove_path(finalization.backup_directory)

    @staticmethod
    def _recreate_directory(directory: Path) -> None:
        MediaProcessor._remove_directory(directory)
        directory.mkdir(parents=True, exist_ok=False)

    @staticmethod
    def _remove_directory(directory: Path) -> None:
        if not directory.exists():
            return
        if not directory.is_dir():
            raise NotADirectoryError(directory)
        shutil.rmtree(directory)

    @staticmethod
    def _remove_path(path: Path) -> None:
        if not path.exists():
            return
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
