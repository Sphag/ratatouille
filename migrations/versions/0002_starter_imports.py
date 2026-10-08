"""Record each owner's atomic starter-library import."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "starter_imports",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("library_id", sa.String(), nullable=False),
        sa.Column("digest", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name="fk_starter_imports_owner_id_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_starter_imports"),
        sa.UniqueConstraint(
            "owner_id", "library_id", name="uq_starter_imports_owner_id_library_id"
        ),
    )
    op.create_index("ix_starter_imports_owner_id", "starter_imports", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_starter_imports_owner_id", table_name="starter_imports")
    op.drop_table("starter_imports")
