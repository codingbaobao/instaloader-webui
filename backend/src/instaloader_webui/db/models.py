from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from instaloader_webui.db.base import Base


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class WebSession(Base):
    __tablename__ = "web_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    admin_user_id: Mapped[str] = mapped_column(
        ForeignKey("admin_users.id"), nullable=False
    )
    token_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginFailure(Base):
    __tablename__ = "login_failures"

    bucket_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False)
    first_failure_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_failure_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginAttemptReservation(Base):
    __tablename__ = "login_attempt_reservations"
    __table_args__ = (
        Index(
            "ix_login_attempt_reservations_account_active",
            "account_bucket_digest",
            "failure_valid",
            "expires_at",
        ),
        Index(
            "ix_login_attempt_reservations_ip_active",
            "ip_bucket_digest",
            "failure_valid",
            "expires_at",
        ),
        Index(
            "ix_login_attempt_reservations_global_active",
            "global_bucket_digest",
            "failure_valid",
            "expires_at",
        ),
        Index("ix_login_attempt_reservations_expires_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_bucket_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    ip_bucket_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    global_bucket_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    failure_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Profile(Base):
    __tablename__ = "profiles"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'deletion_pending', 'deletion_failed')",
            name="ck_profiles_status",
        ),
        Index("ix_profiles_tracked_status", "tracked", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    instagram_user_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    biography: Mapped[str] = mapped_column(Text, nullable=False)
    profile_pic_url: Mapped[str | None] = mapped_column(Text)
    tracked: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    last_sync_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_sync_succeeded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MediaItem(Base):
    __tablename__ = "media_items"
    __table_args__ = (
        UniqueConstraint(
            "identity_type",
            "identity_value",
            name="uq_media_items_identity",
        ),
        CheckConstraint(
            "identity_type IN ('shortcode', 'story_media_id')",
            name="ck_media_items_identity_type",
        ),
        CheckConstraint(
            "kind IN ('post', 'reel', 'story')", name="ck_media_items_kind"
        ),
        Index(
            "ix_media_items_owner_profile_published_at",
            "owner_profile_id",
            "published_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    instagram_media_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    shortcode: Mapped[str | None] = mapped_column(String(64))
    identity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    identity_value: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_profile_id: Mapped[str] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    caption: Mapped[str] = mapped_column(Text, nullable=False)
    accessibility_caption: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    original_url: Mapped[str] = mapped_column(Text, nullable=False)
    story_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MediaAsset(Base):
    __tablename__ = "media_assets"
    __table_args__ = (
        CheckConstraint("kind IN ('image', 'video')", name="ck_media_assets_kind"),
        CheckConstraint("role IN ('content', 'poster')", name="ck_media_assets_role"),
        Index("ix_media_assets_media_item_position", "media_item_id", "position"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    media_item_id: Mapped[str] = mapped_column(
        ForeignKey("media_items.id", ondelete="CASCADE"), nullable=False
    )
    relative_path: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "type IN ('profile_sync', 'single_media', 'delete_profile', 'delete_media', 'followee_discovery')",
            name="ck_jobs_type",
        ),
        CheckConstraint(
            "state IN ('pending', 'running', 'succeeded', 'failed', "
            "'completed_with_warnings')",
            name="ck_jobs_state",
        ),
        Index("ix_jobs_state_created_at", "state", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_text: Mapped[str] = mapped_column(Text, nullable=False)
    progress_current: Mapped[int] = mapped_column(Integer, nullable=False)
    progress_total: Mapped[int | None] = mapped_column(Integer)
    status_text: Mapped[str] = mapped_column(Text, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    phase: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class JobIssue(Base):
    __tablename__ = "job_issues"
    __table_args__ = (
        Index("ix_job_issues_job_id_occurred_at", "job_id", "occurred_at"),
        Index(
            "ix_job_issues_identity_type_identity_value",
            "identity_type",
            "identity_value",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    identity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    identity_value: Mapped[str] = mapped_column(String(64), nullable=False)
    media_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str] = mapped_column(String(64), nullable=False)
    safe_message: Mapped[str] = mapped_column(Text, nullable=False)
    exception_class_chain_text: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class FolloweeImportBatch(Base):
    __tablename__ = "followee_import_batches"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'running', 'ready', 'imported', 'failed')",
            name="ck_followee_import_batches_state",
        ),
        Index(
            "ix_followee_import_batches_state_created_at",
            "state",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    source_username: Mapped[str] = mapped_column(String(64), nullable=False)
    session_imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    total_count: Mapped[int] = mapped_column(Integer, nullable=False)
    importable_count: Mapped[int] = mapped_column(Integer, nullable=False)
    existing_count: Mapped[int] = mapped_column(Integer, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FolloweeImportCandidate(Base):
    __tablename__ = "followee_import_candidates"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "instagram_user_id",
            name="uq_followee_import_candidates_batch_user",
        ),
        Index(
            "ix_followee_import_candidates_batch_username",
            "batch_id",
            "username",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("followee_import_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    instagram_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    profile_pic_url: Mapped[str | None] = mapped_column(Text)
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False)


class AppSetting(Base):
    __tablename__ = "app_settings"
    __table_args__ = (
        CheckConstraint("id = 'global'", name="ck_app_settings_singleton"),
        CheckConstraint(
            "profile_sync_interval_minutes > 0",
            name="ck_app_settings_profile_sync_interval",
        ),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    profile_sync_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    next_sync_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
