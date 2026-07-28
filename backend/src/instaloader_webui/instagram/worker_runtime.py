"""Process-local Instaloader clients for the persistent worker."""

from datetime import UTC, datetime
from pathlib import Path

from instaloader import Instaloader, RateController, TooManyRequestsException

from instaloader_webui.instagram.cookie_file import cookie_dict
from instaloader_webui.instagram.session_store import (
    InstagramSessionSnapshot,
    InstagramSessionStore,
)

SessionRevision = tuple[str, datetime]


class InstagramSessionRevisionError(RuntimeError):
    """The worker no longer has the Cookie revision required by a queued job."""


def _revision(username: str, imported_at: datetime) -> SessionRevision:
    normalized = (
        imported_at.replace(tzinfo=UTC)
        if imported_at.tzinfo is None
        else imported_at.astimezone(UTC)
    )
    return username, normalized


class _WorkerRateController(RateController):
    """Release the single worker immediately when Instagram rate limits it."""

    def handle_429(self, query_type: str) -> None:
        raise TooManyRequestsException("Instagram worker request was rate limited.")


class WorkerInstaloaderRuntime:
    """Reuse anonymous and authenticated clients across sequential worker jobs."""

    def __init__(self, sessions: InstagramSessionStore) -> None:
        self._sessions = sessions
        self._anonymous_loader: Instaloader | None = None
        self._authenticated_loader: Instaloader | None = None
        self._authenticated_revision: SessionRevision | None = None
        self._closed = False

    def acquire(self, staging_directory: Path) -> tuple[Instaloader, bool]:
        """Return a client configured for one job and the session state it uses."""
        if self._closed:
            raise RuntimeError("Instaloader runtime is closed.")

        snapshot = self._sessions.load()
        return self._acquire_snapshot(snapshot, staging_directory)

    def _acquire_snapshot(
        self,
        snapshot: InstagramSessionSnapshot | None,
        staging_directory: Path,
    ) -> tuple[Instaloader, bool]:
        if snapshot is None:
            self._discard_authenticated_loader()
            if self._anonymous_loader is None:
                self._anonymous_loader = self._build_loader(staging_directory)
            loader = self._anonymous_loader
            session_configured = False
        else:
            revision = _revision(snapshot.username, snapshot.imported_at)
            if (
                self._authenticated_loader is None
                or self._authenticated_revision != revision
            ):
                replacement = self._build_loader(staging_directory)
                try:
                    replacement.load_session(
                        snapshot.username,
                        cookie_dict(snapshot.cookies),
                    )
                except Exception:
                    replacement.close()
                    raise
                stale_loader = self._authenticated_loader
                self._authenticated_loader = replacement
                self._authenticated_revision = revision
                if stale_loader is not None:
                    stale_loader.close()
            loader = self._authenticated_loader
            session_configured = True

        if loader is None:
            raise RuntimeError("Instaloader runtime did not provide a client.")
        loader.dirname_pattern = str(staging_directory)
        return loader, session_configured

    def acquire_authenticated(
        self,
        staging_directory: Path,
        *,
        expected_username: str,
        expected_imported_at: datetime,
    ) -> Instaloader:
        """Return the authenticated loader only for the requested Cookie revision."""
        if self._closed:
            raise RuntimeError("Instaloader runtime is closed.")
        snapshot = self._sessions.load()
        expected_revision = _revision(expected_username, expected_imported_at)
        if snapshot is None or _revision(
            snapshot.username,
            snapshot.imported_at,
        ) != expected_revision:
            self._discard_authenticated_loader()
            raise InstagramSessionRevisionError(
                "The Instagram Cookie changed or was removed. Run followee discovery again."
            )
        loader, session_configured = self._acquire_snapshot(
            snapshot,
            staging_directory,
        )
        if not session_configured:
            raise InstagramSessionRevisionError(
                "An Instagram Cookie is required. Run followee discovery again."
            )
        return loader

    def close(self) -> None:
        """Close all cached clients exactly once."""
        if self._closed:
            return
        self._closed = True
        authenticated_loader = self._authenticated_loader
        anonymous_loader = self._anonymous_loader
        self._authenticated_loader = None
        self._authenticated_revision = None
        self._anonymous_loader = None
        try:
            if authenticated_loader is not None:
                authenticated_loader.close()
        finally:
            if anonymous_loader is not None:
                anonymous_loader.close()

    def _discard_authenticated_loader(self) -> None:
        loader = self._authenticated_loader
        self._authenticated_loader = None
        self._authenticated_revision = None
        if loader is not None:
            loader.close()

    @staticmethod
    def _build_loader(staging_directory: Path) -> Instaloader:
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
            rate_controller=_WorkerRateController,
        )
