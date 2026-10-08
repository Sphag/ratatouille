"""Exact aggregation of the actual menu; no eligibility or menu selection engine."""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from ratatouille.domain import (
    MenuEntry,
    Nutrition,
    PlanRevision,
    Unit,
    fraction_decimal,
)

UNIT_FACTORS: dict[Unit, tuple[Unit, int]] = {
    Unit.GRAM: (Unit.GRAM, 1),
    Unit.KILOGRAM: (Unit.GRAM, 1000),
    Unit.MILLILITER: (Unit.MILLILITER, 1),
    Unit.LITER: (Unit.MILLILITER, 1000),
    Unit.PIECE: (Unit.PIECE, 1),
}
NUTRITION_FIELDS = ("calories", "protein", "fat", "carbs")


@dataclass(frozen=True)
class DayNutrition:
    day: int
    total: Nutrition
    targets: Nutrition
    # Signed differences are not Nutrition, which only permits nonnegative inputs.
    difference: tuple[Decimal, Decimal, Decimal, Decimal]


@dataclass(frozen=True)
class ShoppingItem:
    week: int
    ingredient_id: str
    name: str
    unit: Unit
    quantity: Decimal


@dataclass(frozen=True)
class CookingBatch:
    week: int
    version_id: str
    name: str
    portions: int
    ingredients: tuple[ShoppingItem, ...]


@dataclass(frozen=True)
class MenuCalculation:
    days: tuple[DayNutrition, ...]
    shopping: tuple[ShoppingItem, ...]
    cooking: tuple[CookingBatch, ...]


def nutrition_total(entries: tuple[MenuEntry, ...]) -> Nutrition:
    values = [
        fraction_decimal(
            sum((Fraction(getattr(e.recipe.nutrition, f)) for e in entries), Fraction())
        )
        for f in NUTRITION_FIELDS
    ]
    return Nutrition(*values)


def shopping_total(entries: tuple[MenuEntry, ...]) -> tuple[ShoppingItem, ...]:
    quantities: dict[tuple[int, str, Unit], Fraction] = defaultdict(Fraction)
    names: dict[tuple[int, str, Unit], str] = {}
    # Sorting makes the snapshot name deterministic if an ingredient was renamed.
    for entry in sorted(entries, key=lambda e: (e.day, e.slot.value, e.recipe.version_id)):
        for ingredient in entry.recipe.ingredients:
            unit, factor = UNIT_FACTORS[ingredient.unit]
            key = (entry.day // 7, ingredient.ingredient_id, unit)
            quantities[key] += Fraction(ingredient.quantity) * factor / entry.recipe.yield_portions
            names.setdefault(key, ingredient.name)
    return tuple(
        ShoppingItem(week, identifier, names[key], unit, fraction_decimal(quantities[key]))
        for key in sorted(quantities)
        for week, identifier, unit in [key]
    )


def calculate_menu(revision: PlanRevision) -> MenuCalculation:
    days: list[DayNutrition] = []
    for day in range(revision.days):
        entries = tuple(e for e in revision.entries if e.day == day)
        total = nutrition_total(entries)
        difference = tuple(
            fraction_decimal(Fraction(getattr(total, f)) - Fraction(getattr(revision.targets, f)))
            for f in NUTRITION_FIELDS
        )
        days.append(
            DayNutrition(
                day,
                total,
                revision.targets,
                (
                    difference[0],
                    difference[1],
                    difference[2],
                    difference[3],
                ),
            )
        )
    batches: dict[tuple[int, str], list[MenuEntry]] = defaultdict(list)
    for entry in revision.entries:
        batches[entry.day // 7, entry.recipe.version_id].append(entry)
    cooking = tuple(
        CookingBatch(
            week, version_id, batch[0].recipe.name, len(batch), shopping_total(tuple(batch))
        )
        for (week, version_id), batch in sorted(batches.items())
    )
    return MenuCalculation(tuple(days), shopping_total(revision.entries), cooking)
