"""Arithmetic contracts: manual nutrition, exact totals, compatible units only."""

from dataclasses import replace
from datetime import date, time
from decimal import ROUND_UP, Decimal, Inexact, localcontext
from typing import Any

import pytest

from ratatouille.calculations import calculate_menu
from ratatouille.domain import (
    IngredientInput,
    IngredientSnapshot,
    MenuEntry,
    Nutrition,
    PlanRevision,
    RecipeInput,
    RecipeSnapshot,
    ReminderKind,
    RepeatMode,
    RevisionState,
    Schedule,
    Slot,
    Unit,
    ValidationError,
    decimal_value,
)


def recipe(
    ingredients: tuple[IngredientSnapshot, ...],
    *,
    yield_portions: int = 1,
    nutrition: Nutrition | None = None,
    version_id: str = "version",
) -> RecipeSnapshot:
    return RecipeSnapshot(
        version_id,
        "recipe",
        "Рецепт",
        "Инструкция",
        yield_portions,
        nutrition if nutrition is not None else Nutrition(),
        ingredients,
    )


def revision(entries: tuple[MenuEntry, ...], *, days: int = 7) -> PlanRevision:
    return PlanRevision(
        "revision",
        "plan",
        date(2026, 10, 12),
        days,
        RevisionState.DRAFT,
        RepeatMode.AB,
        None,
        1,
        Nutrition(Decimal(1000), Decimal(50), Decimal(30), Decimal(100)),
        entries,
    )


def test_compatible_units_and_fractional_quantities_do_not_mix_mass_with_volume() -> None:
    ingredients = (
        IngredientSnapshot("milk", "Молоко", Decimal("0.25"), Unit.KILOGRAM),
        IngredientSnapshot("milk", "Молоко", Decimal(125), Unit.GRAM),
        IngredientSnapshot("milk", "Молоко", Decimal(200), Unit.MILLILITER),
        IngredientSnapshot("milk", "Молоко", Decimal("0.1"), Unit.LITER),
        IngredientSnapshot("egg", "Яйцо", Decimal("0.5"), Unit.PIECE),
    )
    result = calculate_menu(revision((MenuEntry(0, Slot.BREAKFAST, recipe(ingredients)),)))
    assert {(row.ingredient_id, row.unit): row.quantity for row in result.shopping} == {
        ("milk", Unit.GRAM): Decimal(375),
        ("milk", Unit.MILLILITER): Decimal(300),
        ("egg", Unit.PIECE): Decimal("0.5"),
    }


def test_recipe_yield_division_is_aggregated_before_decimal_conversion() -> None:
    snapshot = recipe(
        (IngredientSnapshot("salt", "Соль", Decimal(1), Unit.GRAM),), yield_portions=3
    )
    entries = tuple(MenuEntry(day, Slot.DINNER, snapshot) for day in range(3))
    assert calculate_menu(revision(entries)).shopping[0].quantity == 1
    assert calculate_menu(revision(entries)).cooking[0].portions == 3
    assert calculate_menu(revision(entries)).cooking[0].ingredients[0].quantity == 1


def test_manual_nutrition_is_summed_by_day_with_signed_soft_target_difference() -> None:
    snapshot = recipe(
        (), nutrition=Nutrition(Decimal(600), Decimal("20.1"), Decimal(10), Decimal(40))
    )
    result = calculate_menu(revision(tuple(MenuEntry(0, slot, snapshot) for slot in Slot)))
    assert result.days[0].total == Nutrition(
        Decimal(2400), Decimal("80.4"), Decimal(40), Decimal(160)
    )
    assert result.days[0].difference == (Decimal(1400), Decimal("30.4"), Decimal(10), Decimal(60))
    assert result.days[1].total == Nutrition()
    assert result.days[1].difference[0] == -1000
    assert result.cooking[0].portions == 4


def test_four_week_shopping_and_batches_are_independent_and_versions_stay_separate() -> None:
    first = recipe((IngredientSnapshot("water", "Вода", Decimal(100), Unit.MILLILITER),))
    second = replace(first, version_id="new", name="Новая версия")
    entries = (
        MenuEntry(0, Slot.DINNER, first),
        MenuEntry(7, Slot.DINNER, first),
        MenuEntry(7, Slot.SECOND, second),
        MenuEntry(27, Slot.DINNER, first),
    )
    result = calculate_menu(revision(entries, days=28))
    assert [(row.week, row.quantity) for row in result.shopping] == [
        (0, Decimal(100)),
        (1, Decimal(200)),
        (3, Decimal(100)),
    ]
    assert len(result.cooking) == 4
    assert len(result.days) == 28


def test_calculations_do_not_depend_on_callers_decimal_precision() -> None:
    snapshot = recipe(
        (IngredientSnapshot("x", "Продукт", Decimal("0.123456789"), Unit.KILOGRAM),),
        nutrition=Nutrition(calories=Decimal("0.123456789")),
    )
    menu = revision(tuple(MenuEntry(day, Slot.DINNER, snapshot) for day in range(7)))
    expected = calculate_menu(menu)
    with localcontext() as context:
        context.prec = 2
        assert calculate_menu(menu) == expected


def test_recurring_fractions_do_not_inherit_rounding_or_inexact_traps() -> None:
    snapshot = recipe(
        (IngredientSnapshot("x", "Продукт", Decimal(1), Unit.GRAM),), yield_portions=3
    )
    menu = revision((MenuEntry(0, Slot.DINNER, snapshot),))
    expected = calculate_menu(menu)
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_UP
        context.traps[Inexact] = True
        assert calculate_menu(menu) == expected


@pytest.mark.parametrize(
    "value", ["NaN", "sNaN", "Infinity", "-Infinity", "-0.1", "", "oops", True, 0.1]
)
def test_invalid_decimal_inputs_are_rejected(value: Any) -> None:
    with pytest.raises(ValidationError):
        decimal_value(value)


@pytest.mark.parametrize("value", [Decimal(0), Decimal("-0"), Decimal(-1)])
def test_ingredient_quantity_must_be_positive(value: Decimal) -> None:
    with pytest.raises(ValidationError):
        IngredientInput("ingredient", value, Unit.GRAM)


@pytest.mark.parametrize("portions", [0, -1, True, 1.5])
def test_recipe_yield_must_be_positive_integer(portions: Any) -> None:
    with pytest.raises(ValidationError):
        RecipeInput(
            "Рецепт", "", portions, Nutrition(), (IngredientInput("x", Decimal(1), Unit.GRAM),)
        )


def test_unknown_units_empty_recipe_and_invalid_schedule_are_rejected() -> None:
    unknown_unit: Any = "spoon"
    with pytest.raises(ValidationError):
        IngredientInput("x", Decimal(1), unknown_unit)
    with pytest.raises(ValidationError):
        RecipeInput("  ", "", 1, Nutrition(), ())
    with pytest.raises(ValidationError):
        Schedule(ReminderKind.COOKING, 7, time(12))
    with pytest.raises(ValidationError):
        Schedule(ReminderKind.COOKING, 0, time(12, 0, 1))
