"""Atomic persistence for authenticated followee discovery and import."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from instaloader_webui.db.library_repositories import JobSnapshot, job_snapshot
from instaloader_webui.db.models import (
    FolloweeImportBatch,
    FolloweeImportCandidate,
    Job,
    Profile,
)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class DiscoveredFollowee:
    instagram_user_id: str
    username: str
    full_name: str
    profile_pic_url: str | None
    is_private: bool


@dataclass(frozen=True, slots=True)
class FolloweeCandidateSnapshot:
    id: str
    instagram_user_id: str
    username: str
    full_name: str
    profile_pic_url: str | None
    is_private: bool
    already_exists: bool


@dataclass(frozen=True, slots=True)
class FolloweeImportBatchSnapshot:
    id: str
    state: str
    source_username: str
    session_imported_at: datetime
    job_id: str
    total_count: int
    importable_count: int
    existing_count: int
    error: str | None
    created_at: datetime
    completed_at: datetime | None
    imported_at: datetime | None
    candidates: tuple[FolloweeCandidateSnapshot, ...]


@dataclass(frozen=True, slots=True)
class FolloweeCommitSnapshot:
    batch: FolloweeImportBatchSnapshot
    imported_count: int
    existing_count: int
    jobs: tuple[JobSnapshot, ...]


class FolloweeImportNotReadyError(ValueError):
    """The requested batch cannot be committed in its current state."""


class FolloweeCandidatesNotFoundError(ValueError):
    """One or more candidate ids do not belong to the ready batch."""


class FolloweeImportRevisionError(ValueError):
    """The ready batch does not match the currently configured Cookie."""


def _profile_identity_maps(
    session: Session,
) -> tuple[dict[str, Profile], dict[str, Profile]]:
    profiles = session.scalars(select(Profile)).all()
    by_user_id = {
        profile.instagram_user_id: profile
        for profile in profiles
        if profile.instagram_user_id is not None
    }
    by_username = {profile.username.casefold(): profile for profile in profiles}
    return by_user_id, by_username


def _candidate_exists(
    candidate: FolloweeImportCandidate,
    by_user_id: dict[str, Profile],
    by_username: dict[str, Profile],
) -> bool:
    if candidate.instagram_user_id in by_user_id:
        return True
    return candidate.username.casefold() in by_username


def _candidate_snapshot(
    model: FolloweeImportCandidate,
    *,
    already_exists: bool,
) -> FolloweeCandidateSnapshot:
    return FolloweeCandidateSnapshot(
        id=model.id,
        instagram_user_id=model.instagram_user_id,
        username=model.username,
        full_name=model.full_name,
        profile_pic_url=model.profile_pic_url,
        is_private=model.is_private,
        already_exists=already_exists,
    )


def _batch_snapshot(
    model: FolloweeImportBatch,
    candidates: tuple[FolloweeCandidateSnapshot, ...] | None = None,
) -> FolloweeImportBatchSnapshot:
    existing_count = model.existing_count
    importable_count = model.importable_count
    if model.state == "ready" and candidates is not None:
        existing_count = sum(candidate.already_exists for candidate in candidates)
        importable_count = len(candidates) - existing_count
    return FolloweeImportBatchSnapshot(
        id=model.id,
        state=model.state,
        source_username=model.source_username,
        session_imported_at=_as_utc(model.session_imported_at),
        job_id=model.job_id,
        total_count=model.total_count,
        importable_count=importable_count,
        existing_count=existing_count,
        error=model.error,
        created_at=_as_utc(model.created_at),
        completed_at=(
            _as_utc(model.completed_at) if model.completed_at is not None else None
        ),
        imported_at=(
            _as_utc(model.imported_at) if model.imported_at is not None else None
        ),
        candidates=candidates or (),
    )


class FolloweeImportRepository:
    """Keep discovery batches and their queue operations transactionally aligned."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_or_get_active(
        self,
        *,
        source_username: str,
        session_imported_at: datetime,
        now: datetime,
    ) -> FolloweeImportBatchSnapshot:
        current_time = _as_utc(now)
        revision_time = _as_utc(session_imported_at)
        with self._session_factory() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            active = session.scalar(
                select(FolloweeImportBatch)
                .where(FolloweeImportBatch.state.in_(("pending", "running")))
                .order_by(FolloweeImportBatch.created_at, FolloweeImportBatch.id)
                .limit(1)
            )
            if active is not None:
                same_revision = (
                    active.source_username.casefold()
                    == source_username.casefold()
                    and _as_utc(active.session_imported_at) == revision_time
                )
                if same_revision:
                    snapshot = _batch_snapshot(active)
                    session.commit()
                    return snapshot
                active.state = "failed"
                active.error = (
                    "The Instagram Cookie changed. Run followee discovery again."
                )
                active.completed_at = current_time

            ready = session.scalar(
                select(FolloweeImportBatch)
                .where(FolloweeImportBatch.state == "ready")
                .order_by(
                    FolloweeImportBatch.created_at.desc(),
                    FolloweeImportBatch.id.desc(),
                )
                .limit(1)
            )
            if ready is not None:
                same_revision = (
                    ready.source_username.casefold()
                    == source_username.casefold()
                    and _as_utc(ready.session_imported_at) == revision_time
                )
                if same_revision:
                    snapshot = _batch_snapshot(ready)
                    session.commit()
                    return snapshot
                session.delete(ready)

            batch_id = str(uuid4())
            job_id = str(uuid4())
            job = Job(
                id=job_id,
                type="followee_discovery",
                state="pending",
                payload_text=json.dumps(
                    {"batch_id": batch_id},
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                progress_current=0,
                progress_total=None,
                status_text="Queued Instagram followee discovery.",
                error=None,
                created_at=current_time,
                started_at=None,
                completed_at=None,
                updated_at=current_time,
            )
            batch = FolloweeImportBatch(
                id=batch_id,
                state="pending",
                source_username=source_username,
                session_imported_at=revision_time,
                job_id=job_id,
                total_count=0,
                importable_count=0,
                existing_count=0,
                error=None,
                created_at=current_time,
                completed_at=None,
                imported_at=None,
            )
            session.add_all((job, batch))
            session.flush()
            snapshot = _batch_snapshot(batch)
            session.commit()
            return snapshot

    def get(self, batch_id: str) -> FolloweeImportBatchSnapshot | None:
        with self._session_factory() as session:
            batch = session.get(FolloweeImportBatch, batch_id)
            if batch is None:
                return None
            candidates: tuple[FolloweeCandidateSnapshot, ...] = ()
            if batch.state == "ready":
                models = session.scalars(
                    select(FolloweeImportCandidate)
                    .where(FolloweeImportCandidate.batch_id == batch.id)
                    .order_by(
                        FolloweeImportCandidate.username,
                        FolloweeImportCandidate.id,
                    )
                ).all()
                by_user_id, by_username = _profile_identity_maps(session)
                candidates = tuple(
                    _candidate_snapshot(
                        candidate,
                        already_exists=_candidate_exists(
                            candidate,
                            by_user_id,
                            by_username,
                        ),
                    )
                    for candidate in models
                )
            return _batch_snapshot(batch, candidates)

    def start_discovery(self, *, batch_id: str, job_id: str) -> None:
        with self._session_factory.begin() as session:
            batch = session.get(FolloweeImportBatch, batch_id)
            if (
                batch is None
                or batch.job_id != job_id
                or batch.state not in ("pending", "running")
            ):
                raise FolloweeImportNotReadyError(
                    "Followee discovery batch is no longer active."
                )
            batch.state = "running"
            batch.error = None

    def complete_discovery(
        self,
        *,
        batch_id: str,
        followees: tuple[DiscoveredFollowee, ...],
        now: datetime,
    ) -> FolloweeImportBatchSnapshot:
        current_time = _as_utc(now)
        with self._session_factory() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            batch = session.get(FolloweeImportBatch, batch_id)
            if batch is None or batch.state != "running":
                session.rollback()
                raise FolloweeImportNotReadyError(
                    "Followee discovery batch is no longer active."
                )
            session.execute(
                delete(FolloweeImportCandidate).where(
                    FolloweeImportCandidate.batch_id == batch_id
                )
            )
            by_user_id, by_username = _profile_identity_maps(session)
            seen_user_ids: set[str] = set()
            candidates: list[FolloweeImportCandidate] = []
            existing_count = 0
            for followee in followees:
                if followee.instagram_user_id in seen_user_ids:
                    continue
                seen_user_ids.add(followee.instagram_user_id)
                candidate = FolloweeImportCandidate(
                    id=str(uuid4()),
                    batch_id=batch_id,
                    instagram_user_id=followee.instagram_user_id,
                    username=followee.username,
                    full_name=followee.full_name,
                    profile_pic_url=followee.profile_pic_url,
                    is_private=followee.is_private,
                )
                if _candidate_exists(candidate, by_user_id, by_username):
                    existing_count += 1
                candidates.append(candidate)
            session.add_all(candidates)
            batch.state = "ready"
            batch.total_count = len(candidates)
            batch.existing_count = existing_count
            batch.importable_count = len(candidates) - existing_count
            batch.error = None
            batch.completed_at = current_time
            session.flush()
            snapshot = _batch_snapshot(batch)
            session.commit()
            return snapshot

    def fail_discovery(
        self,
        *,
        batch_id: str,
        error: str,
        now: datetime,
    ) -> None:
        current_time = _as_utc(now)
        with self._session_factory.begin() as session:
            batch = session.get(FolloweeImportBatch, batch_id)
            if batch is None or batch.state not in ("pending", "running"):
                return
            session.execute(
                delete(FolloweeImportCandidate).where(
                    FolloweeImportCandidate.batch_id == batch_id
                )
            )
            batch.state = "failed"
            batch.total_count = 0
            batch.importable_count = 0
            batch.existing_count = 0
            batch.error = error
            batch.completed_at = current_time

    def commit(
        self,
        *,
        batch_id: str,
        candidate_ids: tuple[str, ...],
        source_username: str,
        session_imported_at: datetime,
        now: datetime,
    ) -> FolloweeCommitSnapshot:
        current_time = _as_utc(now)
        if len(candidate_ids) != len(set(candidate_ids)):
            raise FolloweeCandidatesNotFoundError(
                "Candidate ids must be unique."
            )
        with self._session_factory() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            batch = session.get(FolloweeImportBatch, batch_id)
            if batch is None or batch.state != "ready":
                session.rollback()
                raise FolloweeImportNotReadyError(
                    "Followee import batch is not ready."
                )
            if (
                batch.source_username != source_username
                or _as_utc(batch.session_imported_at)
                != _as_utc(session_imported_at)
            ):
                session.rollback()
                raise FolloweeImportRevisionError(
                    "The Instagram Cookie changed. Run followee discovery again."
                )
            all_candidate_models = session.scalars(
                select(FolloweeImportCandidate).where(
                    FolloweeImportCandidate.batch_id == batch_id
                )
            ).all()
            candidates_by_id = {
                candidate.id: candidate for candidate in all_candidate_models
            }
            if any(candidate_id not in candidates_by_id for candidate_id in candidate_ids):
                session.rollback()
                raise FolloweeCandidatesNotFoundError(
                    "One or more followee candidates were not found."
                )

            by_user_id, by_username = _profile_identity_maps(session)
            jobs: list[JobSnapshot] = []
            imported_count = 0
            existing_count = sum(
                _candidate_exists(candidate, by_user_id, by_username)
                for candidate in all_candidate_models
            )
            for candidate_id in candidate_ids:
                candidate = candidates_by_id[candidate_id]
                if _candidate_exists(candidate, by_user_id, by_username):
                    continue
                profile = Profile(
                    id=str(uuid4()),
                    instagram_user_id=candidate.instagram_user_id,
                    username=candidate.username,
                    full_name=candidate.full_name,
                    biography="",
                    profile_pic_url=candidate.profile_pic_url,
                    tracked=True,
                    status="active",
                    last_sync_attempted_at=None,
                    last_sync_succeeded_at=None,
                    created_at=current_time,
                    updated_at=current_time,
                )
                job = Job(
                    id=str(uuid4()),
                    type="profile_sync",
                    state="pending",
                    payload_text=json.dumps(
                        {"profile_id": profile.id},
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    progress_current=0,
                    progress_total=None,
                    status_text="Queued initial profile synchronization.",
                    error=None,
                    created_at=current_time,
                    started_at=None,
                    completed_at=None,
                    updated_at=current_time,
                )
                session.add_all((profile, job))
                session.flush()
                jobs.append(job_snapshot(job))
                by_user_id[candidate.instagram_user_id] = profile
                by_username[profile.username.casefold()] = profile
                imported_count += 1

            session.execute(
                delete(FolloweeImportCandidate).where(
                    FolloweeImportCandidate.batch_id == batch_id
                )
            )
            batch.state = "imported"
            batch.total_count = len(all_candidate_models)
            batch.existing_count = existing_count
            batch.importable_count = len(all_candidate_models) - existing_count
            batch.error = None
            batch.imported_at = current_time
            session.flush()
            snapshot = _batch_snapshot(batch)
            result = FolloweeCommitSnapshot(
                batch=snapshot,
                imported_count=imported_count,
                existing_count=existing_count,
                jobs=tuple(jobs),
            )
            session.commit()
            return result
