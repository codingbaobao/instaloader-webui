"""Track whether reserved attempts may still record a failure.

Revision ID: 0004_reservation_failure_validity
Revises: 0003_login_attempt_reservations
Create Date: 2026-07-26 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_reservation_failure_validity"
down_revision: str | None = "0003_login_attempt_reservations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("login_attempt_reservations") as batch_op:
        batch_op.add_column(
            sa.Column(
                "failure_valid",
                sa.Boolean(),
                server_default=sa.true(),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("login_attempt_reservations") as batch_op:
        batch_op.drop_column("failure_valid")
