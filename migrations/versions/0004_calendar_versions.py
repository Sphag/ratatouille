"""Persist calendar version per owner's plan."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "calendar_versions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("plan_id", sa.String(), nullable=False),
        sa.Column("digest", sa.String(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["plan_id", "owner_id"], ["plans.id", "plans.owner_id"]),
        sa.UniqueConstraint("owner_id", "plan_id"),
        sa.CheckConstraint("sequence >= 0", name="calendar_sequence"),
    )
    op.create_index("ix_calendar_versions_owner_id", "calendar_versions", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_calendar_versions_owner_id", table_name="calendar_versions")
    op.drop_table("calendar_versions")
