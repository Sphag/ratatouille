"""Persist reminder claims and outcomes independently of process lifetime."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reminder_deliveries",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("event_key", sa.String(), nullable=False),
        sa.Column("plan_id", sa.String(), nullable=False),
        sa.Column("revision_id", sa.String(), nullable=False),
        sa.Column("due_at", sa.Integer(), nullable=False),
        sa.Column("next_at", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_key"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["plan_id", "owner_id"], ["plans.id", "plans.owner_id"]),
        sa.ForeignKeyConstraint(
            ["revision_id", "plan_id", "owner_id"],
            ["plan_revisions.id", "plan_revisions.plan_id", "plan_revisions.owner_id"],
        ),
        sa.CheckConstraint(
            "state IN ('pending','sending','sent','failed','uncertain','cancelled')",
            name="delivery_state",
        ),
        sa.CheckConstraint("attempts >= 0", name="attempts"),
    )
    op.create_index("ix_reminder_deliveries_owner_id", "reminder_deliveries", ["owner_id"])

    op.create_table(
        "error_notices",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("window", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.UniqueConstraint("owner_id", "window"),
    )
    op.create_index("ix_error_notices_owner_id", "error_notices", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_error_notices_owner_id", table_name="error_notices")
    op.drop_table("error_notices")
    op.drop_index("ix_reminder_deliveries_owner_id", table_name="reminder_deliveries")
    op.drop_table("reminder_deliveries")
