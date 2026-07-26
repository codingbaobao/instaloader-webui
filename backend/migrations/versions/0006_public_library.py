"""Create persistence for the public Instagram media library.

Revision ID: 0006_public_library
Revises: 0005_multiscope_login_admission
Create Date: 2026-07-27 12:00:00
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0006_public_library"
down_revision: str | None = "0005_multiscope_login_admission"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("instagram_user_id", sa.String(length=64), nullable=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("biography", sa.Text(), nullable=False),
        sa.Column("profile_pic_url", sa.Text(), nullable=True),
        sa.Column("tracked", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_sync_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'deletion_pending', 'deletion_failed')",
            name="ck_profiles_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instagram_user_id"),
        sa.UniqueConstraint("username"),
    )
    op.create_index("ix_profiles_tracked_status", "profiles", ["tracked", "status"])

    op.create_table(
        "media_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("instagram_media_id", sa.String(length=64), nullable=True),
        sa.Column("shortcode", sa.String(length=64), nullable=False),
        sa.Column("owner_profile_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("caption", sa.Text(), nullable=False),
        sa.Column("accessibility_caption", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("original_url", sa.Text(), nullable=False),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('post', 'reel')", name="ck_media_items_kind"),
        sa.ForeignKeyConstraint(["owner_profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instagram_media_id"),
        sa.UniqueConstraint("shortcode"),
    )
    op.create_index(
        "ix_media_items_owner_profile_published_at",
        "media_items",
        ["owner_profile_id", "published_at"],
    )

    op.create_table(
        "media_assets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("media_item_id", sa.String(length=36), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('image', 'video')", name="ck_media_assets_kind"),
        sa.ForeignKeyConstraint(["media_item_id"], ["media_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("relative_path"),
    )
    op.create_index(
        "ix_media_assets_media_item_position",
        "media_assets",
        ["media_item_id", "position"],
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("payload_text", sa.Text(), nullable=False),
        sa.Column("progress_current", sa.Integer(), nullable=False),
        sa.Column("progress_total", sa.Integer(), nullable=True),
        sa.Column("status_text", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "type IN ('profile_sync', 'single_media', 'delete_profile', 'delete_media')",
            name="ck_jobs_type",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_jobs_state",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobs_state_created_at", "jobs", ["state", "created_at"])

    op.create_table(
        "app_settings",
        sa.Column("id", sa.String(length=16), nullable=False),
        sa.Column("profile_sync_interval_minutes", sa.Integer(), nullable=False),
        sa.Column("next_sync_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 'global'", name="ck_app_settings_singleton"),
        sa.CheckConstraint(
            "profile_sync_interval_minutes > 0",
            name="ck_app_settings_profile_sync_interval",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    due_at = datetime(1970, 1, 1, tzinfo=UTC)
    app_settings = sa.table(
        "app_settings",
        sa.column("id", sa.String()),
        sa.column("profile_sync_interval_minutes", sa.Integer()),
        sa.column("next_sync_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        app_settings,
        [
            {
                "id": "global",
                "profile_sync_interval_minutes": 360,
                "next_sync_at": due_at,
                "created_at": due_at,
                "updated_at": due_at,
            }
        ],
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_index("ix_jobs_state_created_at", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_media_assets_media_item_position", table_name="media_assets")
    op.drop_table("media_assets")
    op.drop_index(
        "ix_media_items_owner_profile_published_at", table_name="media_items"
    )
    op.drop_table("media_items")
    op.drop_index("ix_profiles_tracked_status", table_name="profiles")
    op.drop_table("profiles")
