import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from instaloader_webui.db.models import (
    AppSetting,
    Job,
    MediaAsset,
    MediaItem,
    Profile,
)

GLOBAL_APP_SETTINGS_ID = "global"


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class AssetSnapshot:
    id: str
    media_id: str
    relative_path: str
    mime_type: str
    kind: str
    position: int
    file_size: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ProfileSnapshot:
    id: str
    instagram_user_id: str | None
    username: str
    full_name: str
    biography: str
    profile_pic_url: str | None
    tracked: bool
    status: str
    last_sync_attempted_at: datetime | None
    last_sync_succeeded_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class MediaSnapshot:
    id: str
    instagram_media_id: str | None
    shortcode: str
    owner_profile_id: str
    kind: str
    caption: str
    accessibility_caption: str
    published_at: datetime
    original_url: str
    downloaded_at: datetime | None
    created_at: datetime
    updated_at: datetime
    assets: tuple[AssetSnapshot, ...]


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    id: str
    type: str
    state: str
    payload: dict[str, object]
    progress_current: int
    progress_total: int | None
    status_text: str
    error: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AppSettingsSnapshot:
    id: str
    profile_sync_interval_minutes: int
    next_sync_at: datetime
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class NormalizedAsset:
    relative_path: str
    mime_type: str
    kind: str
    position: int
    file_size: int


@dataclass(frozen=True, slots=True)
class NormalizedMedia:
    instagram_media_id: str | None
    shortcode: str
    kind: str
    caption: str
    accessibility_caption: str
    published_at: datetime
    original_url: str


def _asset_snapshot(model: MediaAsset) -> AssetSnapshot:
    return AssetSnapshot(
        id=model.id,
        media_id=model.media_item_id,
        relative_path=model.relative_path,
        mime_type=model.mime_type,
        kind=model.kind,
        position=model.position,
        file_size=model.file_size,
        created_at=_as_utc(model.created_at),
    )


def _profile_snapshot(model: Profile) -> ProfileSnapshot:
    return ProfileSnapshot(
        id=model.id,
        instagram_user_id=model.instagram_user_id,
        username=model.username,
        full_name=model.full_name,
        biography=model.biography,
        profile_pic_url=model.profile_pic_url,
        tracked=model.tracked,
        status=model.status,
        last_sync_attempted_at=(
            _as_utc(model.last_sync_attempted_at)
            if model.last_sync_attempted_at is not None
            else None
        ),
        last_sync_succeeded_at=(
            _as_utc(model.last_sync_succeeded_at)
            if model.last_sync_succeeded_at is not None
            else None
        ),
        created_at=_as_utc(model.created_at),
        updated_at=_as_utc(model.updated_at),
    )


def _media_snapshot(model: MediaItem, assets: list[MediaAsset]) -> MediaSnapshot:
    return MediaSnapshot(
        id=model.id,
        instagram_media_id=model.instagram_media_id,
        shortcode=model.shortcode,
        owner_profile_id=model.owner_profile_id,
        kind=model.kind,
        caption=model.caption,
        accessibility_caption=model.accessibility_caption,
        published_at=_as_utc(model.published_at),
        original_url=model.original_url,
        downloaded_at=(
            _as_utc(model.downloaded_at) if model.downloaded_at is not None else None
        ),
        created_at=_as_utc(model.created_at),
        updated_at=_as_utc(model.updated_at),
        assets=tuple(_asset_snapshot(asset) for asset in assets),
    )


def _job_snapshot(model: Job) -> JobSnapshot:
    payload = json.loads(model.payload_text)
    if not isinstance(payload, dict):
        raise ValueError("Persisted job payload must be a JSON object.")
    return JobSnapshot(
        id=model.id,
        type=model.type,
        state=model.state,
        payload=payload,
        progress_current=model.progress_current,
        progress_total=model.progress_total,
        status_text=model.status_text,
        error=model.error,
        created_at=_as_utc(model.created_at),
        started_at=_as_utc(model.started_at) if model.started_at is not None else None,
        completed_at=(
            _as_utc(model.completed_at) if model.completed_at is not None else None
        ),
        updated_at=_as_utc(model.updated_at),
    )


def _settings_snapshot(model: AppSetting) -> AppSettingsSnapshot:
    return AppSettingsSnapshot(
        id=model.id,
        profile_sync_interval_minutes=model.profile_sync_interval_minutes,
        next_sync_at=_as_utc(model.next_sync_at),
        created_at=_as_utc(model.created_at),
        updated_at=_as_utc(model.updated_at),
    )


class LibraryRepository:
    """Persist public-library records without leaking mutable ORM objects."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def list_profiles(self) -> tuple[ProfileSnapshot, ...]:
        with self._session_factory() as session:
            profiles = session.scalars(
                select(Profile).order_by(Profile.tracked.desc(), Profile.username)
            ).all()
            return tuple(_profile_snapshot(profile) for profile in profiles)

    def get_profile(self, profile_id: str) -> ProfileSnapshot | None:
        with self._session_factory() as session:
            model = session.get(Profile, profile_id)
            return _profile_snapshot(model) if model is not None else None

    def find_profile_by_username(self, username: str) -> ProfileSnapshot | None:
        with self._session_factory() as session:
            model = session.scalar(select(Profile).where(Profile.username == username))
            return _profile_snapshot(model) if model is not None else None

    def upsert_profile_stub(
        self, *, username: str, tracked: bool, now: datetime
    ) -> ProfileSnapshot:
        current_time = _as_utc(now)
        with self._session_factory.begin() as session:
            model = session.scalar(select(Profile).where(Profile.username == username))
            if model is None:
                model = Profile(
                    id=str(uuid4()),
                    instagram_user_id=None,
                    username=username,
                    full_name="",
                    biography="",
                    profile_pic_url=None,
                    tracked=tracked,
                    status="active",
                    last_sync_attempted_at=None,
                    last_sync_succeeded_at=None,
                    created_at=current_time,
                    updated_at=current_time,
                )
                session.add(model)
            else:
                model.tracked = model.tracked or tracked
                model.status = "active"
                model.updated_at = current_time
            session.flush()
            return _profile_snapshot(model)

    def update_profile_metadata(
        self,
        *,
        profile_id: str,
        instagram_user_id: str,
        username: str,
        full_name: str,
        biography: str,
        profile_pic_url: str | None,
        now: datetime,
    ) -> ProfileSnapshot:
        with self._session_factory.begin() as session:
            model = session.get(Profile, profile_id)
            if model is None:
                raise LookupError(f"Profile {profile_id} does not exist.")
            model.instagram_user_id = instagram_user_id
            model.username = username
            model.full_name = full_name
            model.biography = biography
            model.profile_pic_url = profile_pic_url
            model.updated_at = _as_utc(now)
            session.flush()
            return _profile_snapshot(model)

    def set_profile_sync_result(
        self, *, profile_id: str, succeeded: bool, now: datetime
    ) -> ProfileSnapshot:
        current_time = _as_utc(now)
        with self._session_factory.begin() as session:
            model = session.get(Profile, profile_id)
            if model is None:
                raise LookupError(f"Profile {profile_id} does not exist.")
            model.last_sync_attempted_at = current_time
            if succeeded:
                model.last_sync_succeeded_at = current_time
            model.updated_at = current_time
            session.flush()
            return _profile_snapshot(model)

    def mark_profile_for_deletion(
        self, profile_id: str, now: datetime
    ) -> ProfileSnapshot | None:
        with self._session_factory.begin() as session:
            model = session.get(Profile, profile_id)
            if model is None:
                return None
            model.status = "deletion_pending"
            model.updated_at = _as_utc(now)
            session.flush()
            return _profile_snapshot(model)

    def delete_profile_records(self, profile_id: str) -> tuple[str, ...]:
        with self._session_factory.begin() as session:
            media_ids = list(
                session.scalars(
                    select(MediaItem.id).where(MediaItem.owner_profile_id == profile_id)
                )
            )
            paths = tuple(
                session.scalars(
                    select(MediaAsset.relative_path)
                    .where(MediaAsset.media_item_id.in_(media_ids))
                    .order_by(MediaAsset.relative_path)
                )
            ) if media_ids else ()
            if media_ids:
                session.execute(
                    delete(MediaAsset).where(MediaAsset.media_item_id.in_(media_ids))
                )
                session.execute(
                    delete(MediaItem).where(MediaItem.id.in_(media_ids))
                )
            session.execute(delete(Profile).where(Profile.id == profile_id))
            return paths

    def list_media(
        self,
        *,
        profile_id: str | None = None,
        kind: str | None = None,
        limit: int = 100,
    ) -> tuple[MediaSnapshot, ...]:
        query = select(MediaItem).order_by(MediaItem.published_at.desc()).limit(limit)
        if profile_id is not None:
            query = query.where(MediaItem.owner_profile_id == profile_id)
        if kind is not None:
            query = query.where(MediaItem.kind == kind)
        with self._session_factory() as session:
            media_items = session.scalars(query).all()
            return self._media_snapshots(session, media_items)

    def get_media(self, media_id: str) -> MediaSnapshot | None:
        with self._session_factory() as session:
            model = session.get(MediaItem, media_id)
            if model is None:
                return None
            return self._media_snapshots(session, [model])[0]

    def find_media_by_shortcode(self, shortcode: str) -> MediaSnapshot | None:
        with self._session_factory() as session:
            model = session.scalar(
                select(MediaItem).where(MediaItem.shortcode == shortcode)
            )
            if model is None:
                return None
            return self._media_snapshots(session, [model])[0]

    def upsert_media(
        self,
        *,
        normalized: NormalizedMedia,
        profile_id: str,
        assets: tuple[NormalizedAsset, ...],
        now: datetime,
    ) -> MediaSnapshot:
        current_time = _as_utc(now)
        with self._session_factory.begin() as session:
            model = session.scalar(
                select(MediaItem).where(MediaItem.shortcode == normalized.shortcode)
            )
            if model is None:
                model = MediaItem(
                    id=str(uuid4()),
                    instagram_media_id=normalized.instagram_media_id,
                    shortcode=normalized.shortcode,
                    owner_profile_id=profile_id,
                    kind=normalized.kind,
                    caption=normalized.caption,
                    accessibility_caption=normalized.accessibility_caption,
                    published_at=_as_utc(normalized.published_at),
                    original_url=normalized.original_url,
                    downloaded_at=current_time,
                    created_at=current_time,
                    updated_at=current_time,
                )
                session.add(model)
                session.flush()
            else:
                model.instagram_media_id = normalized.instagram_media_id
                model.owner_profile_id = profile_id
                model.kind = normalized.kind
                model.caption = normalized.caption
                model.accessibility_caption = normalized.accessibility_caption
                model.published_at = _as_utc(normalized.published_at)
                model.original_url = normalized.original_url
                model.downloaded_at = current_time
                model.updated_at = current_time
                session.execute(
                    delete(MediaAsset).where(MediaAsset.media_item_id == model.id)
                )
            asset_models = [
                MediaAsset(
                    id=str(uuid4()),
                    media_item_id=model.id,
                    relative_path=asset.relative_path,
                    mime_type=asset.mime_type,
                    kind=asset.kind,
                    position=asset.position,
                    file_size=asset.file_size,
                    created_at=current_time,
                )
                for asset in assets
            ]
            session.add_all(asset_models)
            session.flush()
            return _media_snapshot(model, asset_models)

    def delete_media_records(self, media_id: str) -> tuple[str, ...]:
        with self._session_factory.begin() as session:
            paths = tuple(
                session.scalars(
                    select(MediaAsset.relative_path)
                    .where(MediaAsset.media_item_id == media_id)
                    .order_by(MediaAsset.relative_path)
                )
            )
            session.execute(delete(MediaAsset).where(MediaAsset.media_item_id == media_id))
            session.execute(delete(MediaItem).where(MediaItem.id == media_id))
            return paths

    @staticmethod
    def _media_snapshots(
        session: Session, media_items: list[MediaItem]
    ) -> tuple[MediaSnapshot, ...]:
        if not media_items:
            return ()
        assets_by_media_id: dict[str, list[MediaAsset]] = {
            media.id: [] for media in media_items
        }
        assets = session.scalars(
            select(MediaAsset)
            .where(MediaAsset.media_item_id.in_(assets_by_media_id))
            .order_by(MediaAsset.position, MediaAsset.created_at)
        ).all()
        for asset in assets:
            assets_by_media_id[asset.media_item_id].append(asset)
        return tuple(
            _media_snapshot(media, assets_by_media_id[media.id]) for media in media_items
        )


class JobRepository:
    """Persist worker jobs with atomic SQLite claiming semantics."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def enqueue(
        self,
        *,
        job_type: str,
        payload: dict[str, object],
        status_text: str,
        now: datetime,
    ) -> JobSnapshot:
        current_time = _as_utc(now)
        model = Job(
            id=str(uuid4()),
            type=job_type,
            state="pending",
            payload_text=json.dumps(payload, separators=(",", ":"), sort_keys=True),
            progress_current=0,
            progress_total=None,
            status_text=status_text,
            error=None,
            created_at=current_time,
            started_at=None,
            completed_at=None,
            updated_at=current_time,
        )
        with self._session_factory.begin() as session:
            session.add(model)
            session.flush()
            return _job_snapshot(model)

    def list(self, limit: int = 100) -> tuple[JobSnapshot, ...]:
        with self._session_factory() as session:
            jobs = session.scalars(
                select(Job).order_by(Job.created_at.desc()).limit(limit)
            ).all()
            return tuple(_job_snapshot(job) for job in jobs)

    def get(self, job_id: str) -> JobSnapshot | None:
        with self._session_factory() as session:
            model = session.get(Job, job_id)
            return _job_snapshot(model) if model is not None else None

    def claim_next(self, now: datetime) -> JobSnapshot | None:
        current_time = _as_utc(now)
        with self._session_factory() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            model = session.scalar(
                select(Job)
                .where(Job.state == "pending")
                .order_by(Job.created_at, Job.id)
                .limit(1)
            )
            if model is None:
                session.commit()
                return None
            model.state = "running"
            model.started_at = current_time
            model.updated_at = current_time
            session.flush()
            snapshot = _job_snapshot(model)
            session.commit()
            return snapshot

    def update_progress(
        self,
        *,
        job_id: str,
        current: int,
        total: int | None,
        status_text: str,
        now: datetime,
    ) -> None:
        with self._session_factory.begin() as session:
            session.execute(
                update(Job)
                .where(Job.id == job_id, Job.state == "running")
                .values(
                    progress_current=current,
                    progress_total=total,
                    status_text=status_text,
                    updated_at=_as_utc(now),
                )
            )

    def succeed(self, *, job_id: str, status_text: str, now: datetime) -> None:
        current_time = _as_utc(now)
        with self._session_factory.begin() as session:
            session.execute(
                update(Job)
                .where(Job.id == job_id, Job.state == "running")
                .values(
                    state="succeeded",
                    status_text=status_text,
                    error=None,
                    completed_at=current_time,
                    updated_at=current_time,
                )
            )

    def fail(self, *, job_id: str, error: str, now: datetime) -> None:
        current_time = _as_utc(now)
        with self._session_factory.begin() as session:
            session.execute(
                update(Job)
                .where(Job.id == job_id, Job.state == "running")
                .values(
                    state="failed",
                    error=error,
                    completed_at=current_time,
                    updated_at=current_time,
                )
            )

    def recover_interrupted(self, now: datetime) -> int:
        with self._session_factory.begin() as session:
            result = session.execute(
                update(Job)
                .where(Job.state == "running")
                .values(
                    state="pending",
                    started_at=None,
                    updated_at=_as_utc(now),
                )
            )
            return result.rowcount

    def has_active_profile_sync(self, profile_id: str) -> bool:
        with self._session_factory() as session:
            payloads = session.scalars(
                select(Job.payload_text).where(
                    Job.type == "profile_sync", Job.state.in_(("pending", "running"))
                )
            )
            return any(
                isinstance(payload := json.loads(payload_text), dict)
                and payload.get("profile_id") == profile_id
                for payload_text in payloads
            )


class SettingsRepository:
    """Manage the singleton profile-sync schedule atomically."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get(self) -> AppSettingsSnapshot:
        with self._session_factory() as session:
            model = session.get(AppSetting, GLOBAL_APP_SETTINGS_ID)
            if model is None:
                raise RuntimeError("Global application settings are missing.")
            return _settings_snapshot(model)

    def update_interval(self, minutes: int, now: datetime) -> AppSettingsSnapshot:
        if minutes <= 0:
            raise ValueError("Profile synchronization interval must be positive.")
        current_time = _as_utc(now)
        with self._session_factory.begin() as session:
            model = session.get(AppSetting, GLOBAL_APP_SETTINGS_ID)
            if model is None:
                raise RuntimeError("Global application settings are missing.")
            model.profile_sync_interval_minutes = minutes
            model.next_sync_at = current_time + timedelta(minutes=minutes)
            model.updated_at = current_time
            session.flush()
            return _settings_snapshot(model)

    def claim_due_sync(self, now: datetime) -> bool:
        current_time = _as_utc(now)
        with self._session_factory() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            model = session.get(AppSetting, GLOBAL_APP_SETTINGS_ID)
            if model is None:
                session.commit()
                raise RuntimeError("Global application settings are missing.")
            if _as_utc(model.next_sync_at) > current_time:
                session.commit()
                return False
            model.next_sync_at = current_time + timedelta(
                minutes=model.profile_sync_interval_minutes
            )
            model.updated_at = current_time
            session.commit()
            return True
