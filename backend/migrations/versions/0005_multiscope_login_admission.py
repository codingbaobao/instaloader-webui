"""Use bounded account, client-IP, and global login admission scopes.

Revision ID: 0005_multiscope_login_admission
Revises: 0004_reservation_failure_validity
Create Date: 2026-07-26 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_multiscope_login_admission"
down_revision: str | None = "0004_reservation_failure_validity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_multiscope_reservations() -> None:
    op.create_table(
        "login_attempt_reservations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_bucket_digest", sa.String(length=64), nullable=False),
        sa.Column("ip_bucket_digest", sa.String(length=64), nullable=False),
        sa.Column("global_bucket_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "failure_valid",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_login_attempt_reservations_account_active",
        "login_attempt_reservations",
        ["account_bucket_digest", "failure_valid", "expires_at"],
    )
    op.create_index(
        "ix_login_attempt_reservations_ip_active",
        "login_attempt_reservations",
        ["ip_bucket_digest", "failure_valid", "expires_at"],
    )
    op.create_index(
        "ix_login_attempt_reservations_global_active",
        "login_attempt_reservations",
        ["global_bucket_digest", "failure_valid", "expires_at"],
    )
    op.create_index(
        "ix_login_attempt_reservations_expires_at",
        "login_attempt_reservations",
        ["expires_at"],
    )


def _create_legacy_reservations() -> None:
    op.create_table(
        "login_attempt_reservations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("bucket_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "failure_valid",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_login_attempt_reservations_bucket_digest",
        "login_attempt_reservations",
        ["bucket_digest"],
    )


def upgrade() -> None:
    # Reservations live for only 30 seconds and cannot be translated safely. Existing
    # combined-key failures likewise reveal neither constituent scope, so reset them.
    op.drop_index(
        "ix_login_attempt_reservations_bucket_digest",
        table_name="login_attempt_reservations",
    )
    op.drop_table("login_attempt_reservations")
    op.execute(sa.text("DELETE FROM login_failures"))
    _create_multiscope_reservations()


def downgrade() -> None:
    for index_name in (
        "ix_login_attempt_reservations_account_active",
        "ix_login_attempt_reservations_ip_active",
        "ix_login_attempt_reservations_global_active",
        "ix_login_attempt_reservations_expires_at",
    ):
        op.drop_index(index_name, table_name="login_attempt_reservations")
    op.drop_table("login_attempt_reservations")
    op.execute(sa.text("DELETE FROM login_failures"))
    _create_legacy_reservations()
