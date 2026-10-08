"""Create owner-scoped meal planning storage"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Triggers are part of this frozen schema revision, independent of current ORM models.
GUARDS: tuple[tuple[str, str, str, str], ...] = (
    ("sealed_version_update", "recipe_versions", "UPDATE", "OLD.sealed = 1"),
    ("sealed_version_delete", "recipe_versions", "DELETE", "OLD.sealed = 1"),
    ("ingredient_snapshot_update", "recipe_ingredients", "UPDATE", "1"),
    ("ingredient_snapshot_delete", "recipe_ingredients", "DELETE", "1"),
    (
        "ingredient_snapshot_insert",
        "recipe_ingredients",
        "INSERT",
        "(SELECT sealed FROM recipe_versions WHERE id = NEW.version_id) = 1",
    ),
    ("terminal_revision_update", "plan_revisions", "UPDATE", "OLD.state <> 'draft'"),
    ("terminal_revision_delete", "plan_revisions", "DELETE", "OLD.state <> 'draft'"),
    (
        "plan_delete",
        "plans",
        "DELETE",
        "EXISTS (SELECT 1 FROM plan_revisions WHERE plan_id = OLD.id)",
    ),
    (
        "plan_dates_update",
        "plans",
        "UPDATE",
        "NEW.start_date <> OLD.start_date OR NEW.days <> OLD.days OR NEW.owner_id <> OLD.owner_id",
    ),
    (
        "plan_pointer_update",
        "plans",
        "UPDATE",
        "NEW.current_revision_id IS NOT NULL AND COALESCE("
        "(SELECT state FROM plan_revisions WHERE id = NEW.current_revision_id), '') <> 'confirmed'",
    ),
    (
        "recipe_pointer_update",
        "recipes",
        "UPDATE",
        "NEW.current_version_id IS NOT NULL AND COALESCE("
        "(SELECT sealed FROM recipe_versions WHERE id = NEW.current_version_id), 0) <> 1",
    ),
    (
        "entry_insert",
        "menu_entries",
        "INSERT",
        "COALESCE((SELECT state FROM plan_revisions WHERE id = NEW.revision_id), '') <> 'draft' OR "
        "NEW.day >= (SELECT p.days FROM plans p JOIN plan_revisions r ON r.plan_id = p.id "
        "WHERE r.id = NEW.revision_id) OR "
        "COALESCE((SELECT sealed FROM recipe_versions WHERE id = NEW.version_id), 0) <> 1",
    ),
    (
        "entry_update",
        "menu_entries",
        "UPDATE",
        "COALESCE((SELECT state FROM plan_revisions WHERE id = OLD.revision_id), '') <> 'draft' OR "
        "COALESCE((SELECT state FROM plan_revisions WHERE id = NEW.revision_id), '') <> 'draft' OR "
        "NEW.day >= (SELECT p.days FROM plans p JOIN plan_revisions r ON r.plan_id = p.id "
        "WHERE r.id = NEW.revision_id) OR "
        "COALESCE((SELECT sealed FROM recipe_versions WHERE id = NEW.version_id), 0) <> 1",
    ),
    (
        "entry_delete",
        "menu_entries",
        "DELETE",
        "COALESCE((SELECT state FROM plan_revisions WHERE id = OLD.revision_id), '') <> 'draft'",
    ),
)


def _guards() -> None:
    for name, table, operation, condition in GUARDS:
        op.execute(
            f"CREATE TRIGGER {name} BEFORE {operation} ON {table} WHEN {condition} "
            "BEGIN SELECT RAISE(ABORT, 'immutable snapshot or invalid plan reference'); END"
        )


def _remove_guards() -> None:
    for name, _, _, _ in reversed(GUARDS):
        op.execute(f"DROP TRIGGER {name}")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("telegram_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "telegram_id IS NULL OR telegram_id > 0", name=op.f("ck_users_telegram_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("telegram_id", name=op.f("uq_users_telegram_id")),
    )
    op.create_table(
        "ingredients",
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_ingredients_name")),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_ingredients_owner_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingredients")),
        sa.UniqueConstraint("id", "owner_id", name=op.f("uq_ingredients_id_owner_id")),
    )
    op.create_index(op.f("ix_ingredients_owner_id"), "ingredients", ["owner_id"], unique=False)
    op.create_table(
        "recipes",
        sa.Column("current_version_id", sa.String(), nullable=True),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["current_version_id", "id", "owner_id"],
            ["recipe_versions.id", "recipe_versions.recipe_id", "recipe_versions.owner_id"],
            name=op.f("fk_recipes_current_version_id_recipe_versions"),
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name=op.f("fk_recipes_owner_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recipes")),
        sa.UniqueConstraint("id", "owner_id", name=op.f("uq_recipes_id_owner_id")),
    )
    op.create_index(op.f("ix_recipes_owner_id"), "recipes", ["owner_id"], unique=False)
    op.create_table(
        "recipe_versions",
        sa.Column("recipe_id", sa.String(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("yield_portions", sa.Integer(), nullable=False),
        sa.Column("sealed", sa.Boolean(), nullable=False),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("calories", sa.Text(), nullable=False),
        sa.Column("protein", sa.Text(), nullable=False),
        sa.Column("fat", sa.Text(), nullable=False),
        sa.Column("carbs", sa.Text(), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_recipe_versions_name")),
        sa.CheckConstraint(
            "number > 0 AND yield_portions > 0", name=op.f("ck_recipe_versions_positive_counts")
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_recipe_versions_owner_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["recipe_id", "owner_id"],
            ["recipes.id", "recipes.owner_id"],
            name=op.f("fk_recipe_versions_recipe_id_recipes"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recipe_versions")),
        sa.UniqueConstraint("id", "owner_id", name=op.f("uq_recipe_versions_id_owner_id")),
        sa.UniqueConstraint(
            "id", "recipe_id", "owner_id", name=op.f("uq_recipe_versions_id_recipe_id_owner_id")
        ),
        sa.UniqueConstraint(
            "recipe_id", "number", name=op.f("uq_recipe_versions_recipe_id_number")
        ),
    )
    op.create_index(
        op.f("ix_recipe_versions_owner_id"), "recipe_versions", ["owner_id"], unique=False
    )
    op.create_table(
        "recipe_ingredients",
        sa.Column("version_id", sa.String(), nullable=False),
        sa.Column("ingredient_id", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("quantity", sa.Text(), nullable=False),
        sa.Column(
            "unit",
            sa.Enum(
                "g",
                "kg",
                "ml",
                "l",
                "piece",
                name="unit",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_recipe_ingredients_name")),
        sa.CheckConstraint("position >= 0", name=op.f("ck_recipe_ingredients_position")),
        sa.ForeignKeyConstraint(
            ["ingredient_id", "owner_id"],
            ["ingredients.id", "ingredients.owner_id"],
            name=op.f("fk_recipe_ingredients_ingredient_id_ingredients"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_recipe_ingredients_owner_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["version_id", "owner_id"],
            ["recipe_versions.id", "recipe_versions.owner_id"],
            name=op.f("fk_recipe_ingredients_version_id_recipe_versions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recipe_ingredients")),
        sa.UniqueConstraint(
            "version_id", "position", name=op.f("uq_recipe_ingredients_version_id_position")
        ),
    )
    op.create_index(
        op.f("ix_recipe_ingredients_owner_id"), "recipe_ingredients", ["owner_id"], unique=False
    )
    op.create_table(
        "goals",
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("calories", sa.Text(), nullable=False),
        sa.Column("protein", sa.Text(), nullable=False),
        sa.Column("fat", sa.Text(), nullable=False),
        sa.Column("carbs", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name=op.f("fk_goals_owner_id_users")),
        sa.PrimaryKeyConstraint("owner_id", name=op.f("pk_goals")),
    )
    op.create_table(
        "plans",
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("current_revision_id", sa.String(), nullable=True),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.CheckConstraint("days IN (7, 28)", name=op.f("ck_plans_duration")),
        sa.ForeignKeyConstraint(
            ["current_revision_id", "id", "owner_id"],
            ["plan_revisions.id", "plan_revisions.plan_id", "plan_revisions.owner_id"],
            name=op.f("fk_plans_current_revision_id_plan_revisions"),
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name=op.f("fk_plans_owner_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plans")),
        sa.UniqueConstraint("id", "owner_id", name=op.f("uq_plans_id_owner_id")),
    )
    op.create_index(op.f("ix_plans_owner_id"), "plans", ["owner_id"], unique=False)
    op.create_table(
        "plan_revisions",
        sa.Column("plan_id", sa.String(), nullable=False),
        sa.Column("base_revision_id", sa.String(), nullable=True),
        sa.Column(
            "state",
            sa.Enum(
                "draft",
                "confirmed",
                "cancelled",
                name="revisionstate",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "mode",
            sa.Enum("ab", "limited", name="repeatmode", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column("repeat_limit", sa.Integer(), nullable=True),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("calories", sa.Text(), nullable=False),
        sa.Column("protein", sa.Text(), nullable=False),
        sa.Column("fat", sa.Text(), nullable=False),
        sa.Column("carbs", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "(mode = 'ab' AND repeat_limit IS NULL) OR "
            "(mode = 'limited' AND repeat_limit IS NOT NULL AND repeat_limit > 0)",
            name=op.f("ck_plan_revisions_mode_limit"),
        ),
        sa.CheckConstraint("number > 0", name=op.f("ck_plan_revisions_number")),
        sa.ForeignKeyConstraint(
            ["base_revision_id", "plan_id", "owner_id"],
            ["plan_revisions.id", "plan_revisions.plan_id", "plan_revisions.owner_id"],
            name=op.f("fk_plan_revisions_base_revision_id_plan_revisions"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_plan_revisions_owner_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["plan_id", "owner_id"],
            ["plans.id", "plans.owner_id"],
            name=op.f("fk_plan_revisions_plan_id_plans"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plan_revisions")),
        sa.UniqueConstraint("id", "owner_id", name=op.f("uq_plan_revisions_id_owner_id")),
        sa.UniqueConstraint(
            "id", "plan_id", "owner_id", name=op.f("uq_plan_revisions_id_plan_id_owner_id")
        ),
    )
    op.create_index(
        op.f("ix_plan_revisions_owner_id"), "plan_revisions", ["owner_id"], unique=False
    )
    op.create_table(
        "menu_entries",
        sa.Column("revision_id", sa.String(), nullable=False),
        sa.Column("day", sa.Integer(), nullable=False),
        sa.Column(
            "slot",
            sa.Enum(
                "breakfast",
                "first",
                "second",
                "dinner",
                name="slot",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("version_id", sa.String(), nullable=False),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.CheckConstraint("day >= 0 AND day < 28", name=op.f("ck_menu_entries_day")),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_menu_entries_owner_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["revision_id", "owner_id"],
            ["plan_revisions.id", "plan_revisions.owner_id"],
            name=op.f("fk_menu_entries_revision_id_plan_revisions"),
        ),
        sa.ForeignKeyConstraint(
            ["version_id", "owner_id"],
            ["recipe_versions.id", "recipe_versions.owner_id"],
            name=op.f("fk_menu_entries_version_id_recipe_versions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_menu_entries")),
        sa.UniqueConstraint(
            "revision_id", "day", "slot", name=op.f("uq_menu_entries_revision_id_day_slot")
        ),
    )
    op.create_index(op.f("ix_menu_entries_owner_id"), "menu_entries", ["owner_id"], unique=False)
    op.create_table(
        "reminders",
        sa.Column(
            "kind",
            sa.Enum(
                "cooking", "thawing", name="reminderkind", native_enum=False, create_constraint=True
            ),
            nullable=False,
        ),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("at", sa.Time(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("timezone", sa.String(), nullable=False),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.CheckConstraint("timezone = 'Europe/Moscow'", name=op.f("ck_reminders_timezone")),
        sa.CheckConstraint("weekday >= 0 AND weekday <= 6", name=op.f("ck_reminders_weekday")),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_reminders_owner_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reminders")),
        sa.UniqueConstraint(
            "owner_id", "kind", "weekday", "at", name=op.f("uq_reminders_owner_id_kind_weekday_at")
        ),
    )
    op.create_index(op.f("ix_reminders_owner_id"), "reminders", ["owner_id"], unique=False)
    op.create_table(
        "shopping_checks",
        sa.Column("revision_id", sa.String(), nullable=False),
        sa.Column("week", sa.Integer(), nullable=False),
        sa.Column("ingredient_id", sa.String(), nullable=False),
        sa.Column(
            "unit",
            sa.Enum(
                "g",
                "kg",
                "ml",
                "l",
                "piece",
                name="unit",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("checked", sa.Boolean(), nullable=False),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.CheckConstraint(
            "unit IN ('g', 'ml', 'piece')", name=op.f("ck_shopping_checks_canonical_unit")
        ),
        sa.CheckConstraint("week >= 0 AND week < 4", name=op.f("ck_shopping_checks_week")),
        sa.ForeignKeyConstraint(
            ["ingredient_id", "owner_id"],
            ["ingredients.id", "ingredients.owner_id"],
            name=op.f("fk_shopping_checks_ingredient_id_ingredients"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_shopping_checks_owner_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["revision_id", "owner_id"],
            ["plan_revisions.id", "plan_revisions.owner_id"],
            name=op.f("fk_shopping_checks_revision_id_plan_revisions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shopping_checks")),
        sa.UniqueConstraint(
            "revision_id",
            "week",
            "ingredient_id",
            "unit",
            name=op.f("uq_shopping_checks_revision_id_week_ingredient_id_unit"),
        ),
    )
    op.create_index(
        op.f("ix_shopping_checks_owner_id"), "shopping_checks", ["owner_id"], unique=False
    )
    op.create_table(
        "cooking_checks",
        sa.Column("revision_id", sa.String(), nullable=False),
        sa.Column("week", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.String(), nullable=False),
        sa.Column("checked", sa.Boolean(), nullable=False),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.CheckConstraint("week >= 0 AND week < 4", name=op.f("ck_cooking_checks_week")),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_cooking_checks_owner_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["revision_id", "owner_id"],
            ["plan_revisions.id", "plan_revisions.owner_id"],
            name=op.f("fk_cooking_checks_revision_id_plan_revisions"),
        ),
        sa.ForeignKeyConstraint(
            ["version_id", "owner_id"],
            ["recipe_versions.id", "recipe_versions.owner_id"],
            name=op.f("fk_cooking_checks_version_id_recipe_versions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cooking_checks")),
        sa.UniqueConstraint(
            "revision_id",
            "week",
            "version_id",
            name=op.f("uq_cooking_checks_revision_id_week_version_id"),
        ),
    )
    op.create_index(
        op.f("ix_cooking_checks_owner_id"), "cooking_checks", ["owner_id"], unique=False
    )
    _guards()


def downgrade() -> None:
    _remove_guards()
    op.execute("UPDATE recipes SET current_version_id = NULL")
    op.execute("UPDATE plans SET current_revision_id = NULL")
    op.execute("UPDATE plan_revisions SET base_revision_id = NULL")
    op.drop_table("shopping_checks")
    op.drop_table("cooking_checks")
    op.drop_table("reminders")
    op.drop_table("menu_entries")
    op.drop_table("recipe_ingredients")
    op.drop_table("goals")
    op.drop_table("recipe_versions")
    op.drop_table("recipes")
    op.drop_table("plan_revisions")
    op.drop_table("plans")
    op.drop_table("ingredients")
    op.drop_table("users")
