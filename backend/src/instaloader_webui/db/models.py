from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
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
