"""SQLAlchemy schema with owner-scoped references and exact decimal storage."""

from datetime import date, time
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    MetaData,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SQLenum,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from ratatouille.domain import (
    ReminderKind,
    RepeatMode,
    RevisionState,
    Slot,
    Unit,
    decimal_value,
)


def new_id() -> str:
    return str(uuid4())


class ExactDecimal(TypeDecorator[Decimal]):
    """SQLite NUMERIC would coerce decimal strings to binary floating point."""

    impl = Text
    cache_ok = True

    def __init__(self, *, positive: bool = False) -> None:
        self.positive = positive
        super().__init__()

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> str | None:
        return None if value is None else str(decimal_value(value, positive=self.positive))

    def process_result_value(self, value: str | None, dialect: Dialect) -> Decimal | None:
        return None if value is None else decimal_value(value)


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class Owned:
    id: Mapped[str] = mapped_column(primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)


class NutritionColumns:
    calories: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal(0))
    protein: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal(0))
    fat: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal(0))
    carbs: Mapped[Decimal] = mapped_column(ExactDecimal(), default=Decimal(0))


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(primary_key=True, default=new_id)
    telegram_id: Mapped[int | None] = mapped_column(unique=True)
    __table_args__ = (
        CheckConstraint("telegram_id IS NULL OR telegram_id > 0", name="telegram_id"),
    )


class Ingredient(Owned, Base):
    __tablename__ = "ingredients"
    name: Mapped[str]
    __table_args__ = (
        UniqueConstraint("id", "owner_id"),
        CheckConstraint("length(trim(name)) > 0", name="name"),
    )


class Recipe(Owned, Base):
    __tablename__ = "recipes"
    current_version_id: Mapped[str | None]
    eligible: Mapped[bool] = mapped_column(default=False)
    archived: Mapped[bool] = mapped_column(default=False)
    __table_args__ = (
        UniqueConstraint("id", "owner_id"),
        ForeignKeyConstraint(
            ["current_version_id", "id", "owner_id"],
            ["recipe_versions.id", "recipe_versions.recipe_id", "recipe_versions.owner_id"],
            deferrable=True,
            initially="DEFERRED",
        ),
    )


class RecipeVersion(Owned, NutritionColumns, Base):
    __tablename__ = "recipe_versions"
    recipe_id: Mapped[str]
    number: Mapped[int]
    name: Mapped[str]
    instructions: Mapped[str] = mapped_column(Text)
    yield_portions: Mapped[int]
    sealed: Mapped[bool] = mapped_column(default=False)
    __table_args__ = (
        UniqueConstraint("id", "owner_id"),
        UniqueConstraint("id", "recipe_id", "owner_id"),
        UniqueConstraint("recipe_id", "number"),
        ForeignKeyConstraint(["recipe_id", "owner_id"], ["recipes.id", "recipes.owner_id"]),
        CheckConstraint("number > 0 AND yield_portions > 0", name="positive_counts"),
        CheckConstraint("length(trim(name)) > 0", name="name"),
    )


class RecipeIngredient(Owned, Base):
    __tablename__ = "recipe_ingredients"
    version_id: Mapped[str]
    ingredient_id: Mapped[str]
    position: Mapped[int]
    name: Mapped[str]
    quantity: Mapped[Decimal] = mapped_column(ExactDecimal(positive=True))
    unit: Mapped[Unit] = mapped_column(
        SQLenum(
            Unit,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        )
    )
    __table_args__ = (
        UniqueConstraint("version_id", "position"),
        ForeignKeyConstraint(
            ["version_id", "owner_id"], ["recipe_versions.id", "recipe_versions.owner_id"]
        ),
        ForeignKeyConstraint(
            ["ingredient_id", "owner_id"], ["ingredients.id", "ingredients.owner_id"]
        ),
        CheckConstraint("position >= 0", name="position"),
        CheckConstraint("length(trim(name)) > 0", name="name"),
    )


class Goals(NutritionColumns, Base):
    __tablename__ = "goals"
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)


class Plan(Owned, Base):
    __tablename__ = "plans"
    start_date: Mapped[date]
    days: Mapped[int]
    current_revision_id: Mapped[str | None]
    __table_args__ = (
        UniqueConstraint("id", "owner_id"),
        CheckConstraint("days IN (7, 28)", name="duration"),
        ForeignKeyConstraint(
            ["current_revision_id", "id", "owner_id"],
            ["plan_revisions.id", "plan_revisions.plan_id", "plan_revisions.owner_id"],
            deferrable=True,
            initially="DEFERRED",
        ),
    )


class Revision(Owned, NutritionColumns, Base):
    __tablename__ = "plan_revisions"
    plan_id: Mapped[str]
    base_revision_id: Mapped[str | None]
    state: Mapped[RevisionState] = mapped_column(
        SQLenum(
            RevisionState,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        )
    )
    mode: Mapped[RepeatMode] = mapped_column(
        SQLenum(
            RepeatMode,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        )
    )
    repeat_limit: Mapped[int | None]
    number: Mapped[int] = mapped_column(default=1)
    __table_args__ = (
        UniqueConstraint("id", "owner_id"),
        UniqueConstraint("id", "plan_id", "owner_id"),
        ForeignKeyConstraint(["plan_id", "owner_id"], ["plans.id", "plans.owner_id"]),
        ForeignKeyConstraint(
            ["base_revision_id", "plan_id", "owner_id"],
            ["plan_revisions.id", "plan_revisions.plan_id", "plan_revisions.owner_id"],
        ),
        CheckConstraint("number > 0", name="number"),
        CheckConstraint(
            "(mode = 'ab' AND repeat_limit IS NULL) OR "
            "(mode = 'limited' AND repeat_limit IS NOT NULL AND repeat_limit > 0)",
            name="mode_limit",
        ),
    )


class Entry(Owned, Base):
    __tablename__ = "menu_entries"
    revision_id: Mapped[str]
    day: Mapped[int]
    slot: Mapped[Slot] = mapped_column(
        SQLenum(
            Slot,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        )
    )
    version_id: Mapped[str]
    __table_args__ = (
        UniqueConstraint("revision_id", "day", "slot"),
        ForeignKeyConstraint(
            ["revision_id", "owner_id"], ["plan_revisions.id", "plan_revisions.owner_id"]
        ),
        ForeignKeyConstraint(
            ["version_id", "owner_id"], ["recipe_versions.id", "recipe_versions.owner_id"]
        ),
        CheckConstraint("day >= 0 AND day < 28", name="day"),
    )


class ShoppingCheck(Owned, Base):
    __tablename__ = "shopping_checks"
    revision_id: Mapped[str]
    week: Mapped[int]
    ingredient_id: Mapped[str]
    unit: Mapped[Unit] = mapped_column(
        SQLenum(
            Unit,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        )
    )
    checked: Mapped[bool] = mapped_column(default=False)
    __table_args__ = (
        UniqueConstraint("revision_id", "week", "ingredient_id", "unit"),
        ForeignKeyConstraint(
            ["revision_id", "owner_id"], ["plan_revisions.id", "plan_revisions.owner_id"]
        ),
        ForeignKeyConstraint(
            ["ingredient_id", "owner_id"], ["ingredients.id", "ingredients.owner_id"]
        ),
        CheckConstraint("week >= 0 AND week < 4", name="week"),
        CheckConstraint("unit IN ('g', 'ml', 'piece')", name="canonical_unit"),
    )


class CookingCheck(Owned, Base):
    __tablename__ = "cooking_checks"
    revision_id: Mapped[str]
    week: Mapped[int]
    version_id: Mapped[str]
    checked: Mapped[bool] = mapped_column(default=False)
    __table_args__ = (
        UniqueConstraint("revision_id", "week", "version_id"),
        ForeignKeyConstraint(
            ["revision_id", "owner_id"], ["plan_revisions.id", "plan_revisions.owner_id"]
        ),
        ForeignKeyConstraint(
            ["version_id", "owner_id"], ["recipe_versions.id", "recipe_versions.owner_id"]
        ),
        CheckConstraint("week >= 0 AND week < 4", name="week"),
    )


class Reminder(Owned, Base):
    __tablename__ = "reminders"
    kind: Mapped[ReminderKind] = mapped_column(
        SQLenum(
            ReminderKind,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        )
    )
    weekday: Mapped[int]
    at: Mapped[time]
    enabled: Mapped[bool] = mapped_column(default=False)
    timezone: Mapped[str] = mapped_column(default="Europe/Moscow")
    __table_args__ = (
        UniqueConstraint("owner_id", "kind", "weekday", "at"),
        CheckConstraint("weekday >= 0 AND weekday <= 6", name="weekday"),
        CheckConstraint("timezone = 'Europe/Moscow'", name="timezone"),
    )
