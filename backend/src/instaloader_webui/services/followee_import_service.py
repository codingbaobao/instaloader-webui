"""Application boundary for Cookie-revision-safe followee imports."""

from datetime import UTC, datetime

from instaloader_webui.db.followee_import_repositories import (
    FolloweeCommitSnapshot,
    FolloweeImportBatchSnapshot,
    FolloweeImportRepository,
)
from instaloader_webui.instagram.session_store import (
    InstagramSessionSnapshot,
    InstagramSessionStore,
    InstagramSessionStoreError,
)


class FolloweeImportSessionError(RuntimeError):
    """The current Cookie session cannot safely support this operation."""


class FolloweeImportService:
    def __init__(
        self,
        *,
        repository: FolloweeImportRepository,
        sessions: InstagramSessionStore,
    ) -> None:
        self._repository = repository
        self._sessions = sessions

    def create_or_get_active(self, now: datetime) -> FolloweeImportBatchSnapshot:
        snapshot = self._current_session()
        if snapshot is None:
            raise FolloweeImportSessionError(
                "Import an Instagram Cookie before scanning followed accounts."
            )
        return self._repository.create_or_get_active(
            source_username=snapshot.username,
            session_imported_at=snapshot.imported_at,
            now=now,
        )

    def get(self, batch_id: str) -> FolloweeImportBatchSnapshot | None:
        batch = self._repository.get(batch_id)
        if batch is None or batch.state != "ready":
            return batch
        snapshot = self._current_session()
        if snapshot is None or (
            batch.source_username.casefold() != snapshot.username.casefold()
            or _as_utc(batch.session_imported_at) != _as_utc(snapshot.imported_at)
        ):
            raise FolloweeImportSessionError(
                "The Instagram Cookie changed or was removed. Run followee discovery again."
            )
        return batch

    def commit(
        self,
        *,
        batch_id: str,
        candidate_ids: tuple[str, ...],
        now: datetime,
    ) -> FolloweeCommitSnapshot:
        snapshot = self._current_session()
        if snapshot is None:
            raise FolloweeImportSessionError(
                "The Instagram Cookie was removed. Run followee discovery again."
            )
        return self._repository.commit(
            batch_id=batch_id,
            candidate_ids=candidate_ids,
            source_username=snapshot.username,
            session_imported_at=snapshot.imported_at,
            now=now,
        )

    def _current_session(self) -> InstagramSessionSnapshot | None:
        try:
            return self._sessions.load()
        except InstagramSessionStoreError:
            raise FolloweeImportSessionError(
                "Instagram session storage is unreadable. Re-import the Cookie file."
            ) from None


def _as_utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=UTC)
        if value.tzinfo is None
        else value.astimezone(UTC)
    )
