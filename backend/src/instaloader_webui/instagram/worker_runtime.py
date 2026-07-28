"""Process-local Instaloader clients for the persistent worker."""

from datetime import datetime
from pathlib import Path

from instaloader import Instaloader

from instaloader_webui.instagram.cookie_file import cookie_dict
from instaloader_webui.instagram.session_store import InstagramSessionStore

SessionRevision = tuple[str, datetime]


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
        if snapshot is None:
            self._discard_authenticated_loader()
            if self._anonymous_loader is None:
                self._anonymous_loader = self._build_loader(staging_directory)
            loader = self._anonymous_loader
            session_configured = False
        else:
            revision = (snapshot.username, snapshot.imported_at)
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
        )
