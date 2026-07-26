"""Create persistent administrator login-failure buckets.

Revision ID: 0002_login_failures
Revises: 0001_admin_and_sessions
Create Date: 2026-07-26 08:15:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_login_failures"
down_revision: str | None = "0001_admin_and_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "login_failures",
        sa.Column("bucket_digest", sa.String(length=64), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("first_failure_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("bucket_digest"),
    )


def downgrade() -> None:
    op.drop_table("login_failures")
