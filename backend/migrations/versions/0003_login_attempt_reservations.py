"""Create bounded in-flight login-attempt reservations.

Revision ID: 0003_login_attempt_reservations
Revises: 0002_login_failures
Create Date: 2026-07-26 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_login_attempt_reservations"
down_revision: str | None = "0002_login_failures"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "login_attempt_reservations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("bucket_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_login_attempt_reservations_bucket_digest",
        "login_attempt_reservations",
        ["bucket_digest"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_login_attempt_reservations_bucket_digest",
        table_name="login_attempt_reservations",
    )
    op.drop_table("login_attempt_reservations")
