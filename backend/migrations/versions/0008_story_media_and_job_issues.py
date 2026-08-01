"""Add Story media identities, asset roles, and job issues.

Revision ID: 0008_story_media_and_job_issues
Revises: 0007_followee_imports
Create Date: 2026-07-31 12:00:00

Downgrading removes Story media, poster assets, and media identities that cannot
be represented by the earlier non-null, unique shortcode column. It backfills a
missing shortcode from a shortcode identity only when doing so is unique. It
also removes jobs completed with warnings (including their dependent
followee-import batches) because those values cannot satisfy the earlier schema
constraints.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_story_media_and_job_issues"
down_revision: str | None = "0007_followee_imports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "media_items",
        sa.Column("identity_type", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "media_items",
        sa.Column("identity_value", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "media_items",
        sa.Column("story_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE media_items "
            "SET identity_type = 'shortcode', identity_value = shortcode "
            "WHERE shortcode IS NOT NULL"
        )
    )

    with op.batch_alter_table(
        "media_items",
        recreate="always",
        naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"},
    ) as batch_op:
        batch_op.drop_constraint("ck_media_items_kind", type_="check")
        batch_op.drop_constraint("uq_media_items_shortcode", type_="unique")
        batch_op.alter_column(
            "shortcode",
            existing_type=sa.String(length=64),
            nullable=True,
        )
        batch_op.alter_column(
            "identity_type",
            existing_type=sa.String(length=32),
            nullable=False,
        )
        batch_op.alter_column(
            "identity_value",
            existing_type=sa.String(length=64),
            nullable=False,
        )
        batch_op.create_unique_constraint(
            "uq_media_items_identity", ["identity_type", "identity_value"]
        )
        batch_op.create_check_constraint(
            "ck_media_items_identity_type",
            "identity_type IN ('shortcode', 'story_media_id')",
        )
        batch_op.create_check_constraint(
            "ck_media_items_kind",
            "kind IN ('post', 'reel', 'story')",
        )

    op.add_column(
        "media_assets",
        sa.Column(
            "role",
            sa.String(length=16),
            nullable=False,
            server_default="content",
        ),
    )
    with op.batch_alter_table("media_assets", recreate="always") as batch_op:
        batch_op.alter_column(
            "role",
            existing_type=sa.String(length=16),
            server_default=None,
        )
        batch_op.create_check_constraint(
            "ck_media_assets_role", "role IN ('content', 'poster')"
        )

    with op.batch_alter_table("jobs", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_jobs_state", type_="check")
        batch_op.alter_column(
            "state",
            existing_type=sa.String(length=16),
            type_=sa.String(length=32),
        )
        batch_op.add_column(sa.Column("phase", sa.String(length=32), nullable=True))
        batch_op.create_check_constraint(
            "ck_jobs_state",
            "state IN ('pending', 'running', 'succeeded', 'failed', "
            "'completed_with_warnings')",
        )

    op.create_table(
        "job_issues",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("identity_type", sa.String(length=32), nullable=False),
        sa.Column("identity_value", sa.String(length=64), nullable=False),
        sa.Column("media_kind", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=False),
        sa.Column("safe_message", sa.Text(), nullable=False),
        sa.Column("exception_class_chain_text", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_job_issues_job_id_occurred_at",
        "job_issues",
        ["job_id", "occurred_at"],
    )
    op.create_index(
        "ix_job_issues_identity_type_identity_value",
        "job_issues",
        ["identity_type", "identity_value"],
    )


def downgrade() -> None:
    op.drop_index("ix_job_issues_identity_type_identity_value", table_name="job_issues")
    op.drop_index("ix_job_issues_job_id_occurred_at", table_name="job_issues")
    op.drop_table("job_issues")

    op.execute(sa.text("DELETE FROM media_assets WHERE role = 'poster'"))
    op.execute(sa.text("DELETE FROM media_items WHERE kind = 'story'"))
    op.execute(
        sa.text(
            "UPDATE media_items "
            "SET shortcode = identity_value "
            "WHERE identity_type = 'shortcode' "
            "AND shortcode IS NULL "
            "AND NOT EXISTS ("
            "SELECT 1 FROM media_items AS existing "
            "WHERE existing.shortcode = media_items.identity_value "
            "AND existing.id != media_items.id"
            ")"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM media_items "
            "WHERE identity_type = 'story_media_id' OR shortcode IS NULL"
        )
    )
    with op.batch_alter_table("media_assets", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_media_assets_role", type_="check")
        batch_op.drop_column("role")

    op.execute(
        sa.text(
            "DELETE FROM followee_import_batches "
            "WHERE job_id IN ("
            "SELECT id FROM jobs WHERE state = 'completed_with_warnings'"
            ")"
        )
    )
    op.execute(sa.text("DELETE FROM jobs WHERE state = 'completed_with_warnings'"))
    with op.batch_alter_table("jobs", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_jobs_state", type_="check")
        batch_op.drop_column("phase")
        batch_op.alter_column(
            "state",
            existing_type=sa.String(length=32),
            type_=sa.String(length=16),
        )
        batch_op.create_check_constraint(
            "ck_jobs_state",
            "state IN ('pending', 'running', 'succeeded', 'failed')",
        )

    with op.batch_alter_table("media_items", recreate="always") as batch_op:
        batch_op.drop_constraint("uq_media_items_identity", type_="unique")
        batch_op.drop_constraint("ck_media_items_identity_type", type_="check")
        batch_op.drop_constraint("ck_media_items_kind", type_="check")
        batch_op.alter_column(
            "shortcode",
            existing_type=sa.String(length=64),
            nullable=False,
        )
        batch_op.drop_column("story_expires_at")
        batch_op.drop_column("identity_value")
        batch_op.drop_column("identity_type")
        batch_op.create_unique_constraint("uq_media_items_shortcode", ["shortcode"])
        batch_op.create_check_constraint(
            "ck_media_items_kind", "kind IN ('post', 'reel')"
        )
