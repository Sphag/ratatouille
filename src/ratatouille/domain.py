"""Validated values and immutable snapshots, independent of storage and HTTP."""

from dataclasses import dataclass
from datetime import date, time
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext
from enum import StrEnum
from fractions import Fraction


class ValidationError(ValueError):
    """A supplied value cannot represent the requested domain object."""


class NotFoundError(LookupError):
    """The object does not exist in this owner's data."""


class ConflictError(RuntimeError):
    """A stale or completed draft cannot accept this operation."""


type Number = Decimal | str | int


def decimal_value(value: Number, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, str, int)):
        raise ValidationError("Используйте десятичную строку, Decimal или целое число.")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValidationError("Некорректное число.") from error
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ValidationError(
            "Число должно быть конечным и неотрицательным; количество — больше нуля."
        )
    return result


def positive_integer(value: int) -> int:
    if type(value) is not int or value <= 0:
        raise ValidationError("Ожидается положительное целое число.")
    return value


def nonempty(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("Поле не должно быть пустым.")
    return value.strip()


def fraction_decimal(value: Fraction) -> Decimal:
    """Use a fixed local precision; callers' Decimal contexts cannot change results."""
    precision = max(50, len(str(abs(value.numerator))) + len(str(value.denominator)) + 2)
    with localcontext(Context(prec=precision, rounding=ROUND_HALF_EVEN)):
        return Decimal(value.numerator) / Decimal(value.denominator)


class Unit(StrEnum):
    GRAM = "g"
    KILOGRAM = "kg"
    MILLILITER = "ml"
    LITER = "l"
    PIECE = "piece"


class Slot(StrEnum):
    BREAKFAST = "breakfast"
    FIRST = "first"
    SECOND = "second"
    DINNER = "dinner"


class RepeatMode(StrEnum):
    AB = "ab"
    LIMITED = "limited"


class RevisionState(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class ReminderKind(StrEnum):
    COOKING = "cooking"
    THAWING = "thawing"


@dataclass(frozen=True)
class Nutrition:
    calories: Decimal = Decimal(0)
    protein: Decimal = Decimal(0)
    fat: Decimal = Decimal(0)
    carbs: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        for field in ("calories", "protein", "fat", "carbs"):
            object.__setattr__(self, field, decimal_value(getattr(self, field)))


@dataclass(frozen=True)
class IngredientInput:
    ingredient_id: str
    quantity: Decimal
    unit: Unit

    def __post_init__(self) -> None:
        nonempty(self.ingredient_id)
        object.__setattr__(self, "quantity", decimal_value(self.quantity, positive=True))
        if not isinstance(self.unit, Unit):
            raise ValidationError("Неизвестная единица измерения.")


@dataclass(frozen=True)
class RecipeInput:
    name: str
    instructions: str
    yield_portions: int
    nutrition: Nutrition
    ingredients: tuple[IngredientInput, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", nonempty(self.name))
        positive_integer(self.yield_portions)
        if not isinstance(self.instructions, str) or not isinstance(self.nutrition, Nutrition):
            raise ValidationError("Некорректные поля рецепта.")
        if not isinstance(self.ingredients, tuple) or not self.ingredients:
            raise ValidationError("Добавьте ингредиенты рецепта.")
        if any(not isinstance(item, IngredientInput) for item in self.ingredients):
            raise ValidationError("Некорректный ингредиент рецепта.")


@dataclass(frozen=True)
class IngredientSnapshot:
    ingredient_id: str
    name: str
    quantity: Decimal
    unit: Unit


@dataclass(frozen=True)
class RecipeSnapshot:
    version_id: str
    recipe_id: str
    name: str
    instructions: str
    yield_portions: int
    nutrition: Nutrition
    ingredients: tuple[IngredientSnapshot, ...]


@dataclass(frozen=True)
class MenuEntry:
    day: int
    slot: Slot
    recipe: RecipeSnapshot


@dataclass(frozen=True)
class PlanRevision:
    id: str
    plan_id: str
    start_date: date
    days: int
    state: RevisionState
    mode: RepeatMode
    repeat_limit: int | None
    number: int
    targets: Nutrition
    entries: tuple[MenuEntry, ...]


@dataclass(frozen=True)
class Schedule:
    kind: ReminderKind
    weekday: int
    at: time
    enabled: bool = False
    timezone: str = "Europe/Moscow"

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ReminderKind):
            raise ValidationError("Неизвестный вид напоминания.")
        if type(self.weekday) is not int or not 0 <= self.weekday <= 6:
            raise ValidationError("День недели должен быть от 0 до 6 (понедельник — 0).")
        if not isinstance(self.at, time) or self.at.tzinfo is not None:
            raise ValidationError("Введите местное время без смещения часового пояса.")
        if self.at.second or self.at.microsecond or type(self.enabled) is not bool:
            raise ValidationError(
                "Время задаётся с точностью до минуты, включение — логическим значением."
            )
        if self.timezone != "Europe/Moscow":
            raise ValidationError("В первой версии используется Europe/Moscow.")
