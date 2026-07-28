"""Add authenticated followee discovery and import batches.

Revision ID: 0007_followee_imports
Revises: 0006_public_library
Create Date: 2026-07-28 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_followee_imports"
down_revision: str | None = "0006_public_library"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_constraint("ck_jobs_type", type_="check")
        batch_op.create_check_constraint(
            "ck_jobs_type",
            "type IN ('profile_sync', 'single_media', 'delete_profile', "
            "'delete_media', 'followee_discovery')",
        )

    op.create_table(
        "followee_import_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("source_username", sa.String(length=64), nullable=False),
        sa.Column(
            "session_imported_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=False),
        sa.Column("importable_count", sa.Integer(), nullable=False),
        sa.Column("existing_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('pending', 'running', 'ready', 'imported', 'failed')",
            name="ck_followee_import_batches_state",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id"),
    )
    op.create_index(
        "ix_followee_import_batches_state_created_at",
        "followee_import_batches",
        ["state", "created_at"],
    )

    op.create_table(
        "followee_import_candidates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("instagram_user_id", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("profile_pic_url", sa.Text(), nullable=True),
        sa.Column("is_private", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["followee_import_batches.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "batch_id",
            "instagram_user_id",
            name="uq_followee_import_candidates_batch_user",
        ),
    )
    op.create_index(
        "ix_followee_import_candidates_batch_username",
        "followee_import_candidates",
        ["batch_id", "username"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_followee_import_candidates_batch_username",
        table_name="followee_import_candidates",
    )
    op.drop_table("followee_import_candidates")
    op.drop_index(
        "ix_followee_import_batches_state_created_at",
        table_name="followee_import_batches",
    )
    op.drop_table("followee_import_batches")
    op.execute(sa.text("DELETE FROM jobs WHERE type = 'followee_discovery'"))

    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_constraint("ck_jobs_type", type_="check")
        batch_op.create_check_constraint(
            "ck_jobs_type",
            "type IN ('profile_sync', 'single_media', 'delete_profile', 'delete_media')",
        )
