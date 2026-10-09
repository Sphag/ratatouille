"""Strict modes, exact targets, feasible capacities and deterministic diverse proposals."""

from collections import Counter
from dataclasses import replace
from datetime import date
from decimal import Decimal, localcontext

import pytest

from ratatouille.calculations import calculate_menu
from ratatouille.domain import (
    Nutrition,
    PlanRevision,
    RecipeSnapshot,
    RepeatMode,
    RevisionState,
    Slot,
    ValidationError,
)
from ratatouille.generation import generate
from ratatouille.menus import problems


def recipe(index: int, calories: str = "10") -> RecipeSnapshot:
    return RecipeSnapshot(
        f"version-{index}",
        f"recipe-{index}",
        f"Блюдо {index}",
        "",
        4,
        Nutrition(calories=Decimal(calories)),
        (),
    )


def revision(
    *, limited: bool = False, limit: int = 3, days: int = 7, calories: str = "30"
) -> PlanRevision:
    return PlanRevision(
        "draft",
        "plan",
        date(2026, 10, 12),
        days,
        RevisionState.DRAFT,
        RepeatMode.LIMITED if limited else RepeatMode.AB,
        limit if limited else None,
        1,
        Nutrition(calories=Decimal(calories)),
        (),
    )


@pytest.mark.parametrize("days", [7, 28])
@pytest.mark.parametrize("limited", [False, True])
def test_modes_whole_portions_exact_targets_and_determinism(days: int, limited: bool) -> None:
    source = revision(limited=limited, days=days)
    recipes = tuple(recipe(i) for i in range(12))
    generated = generate(source, recipes)
    assert not source.entries
    assert not problems(generated)
    assert generated == generate(source, tuple(reversed(recipes)))
    assert len(generated.entries) == days * 3
    for daily in calculate_menu(generated).days:
        assert daily.total.calories == 30 and daily.difference[0] == 0
    for week in range(days // 7):
        counts = Counter(e.recipe.recipe_id for e in generated.entries if e.day // 7 == week)
        if limited:
            assert max(counts.values()) <= 3
        else:
            for offsets in ((0, 2, 4, 6), (1, 3, 5)):
                menus = [
                    tuple(
                        (e.slot, e.recipe.version_id)
                        for e in generated.entries
                        if e.day == week * 7 + offset
                    )
                    for offset in offsets
                ]
                assert all(m == menus[0] for m in menus)


def test_ab_ignores_limited_cap_and_optional_first_added_only_when_useful() -> None:
    source = revision(calories="40")
    generated = generate(source, (recipe(0),))
    assert len(generated.entries) == 28
    assert all(d.total.calories == 40 for d in calculate_menu(generated).days)
    generated = generate(revision(calories="30"), (recipe(0),))
    assert len(generated.entries) == 21
    assert not any(e.slot == Slot.FIRST for e in generated.entries)


def test_strict_limit_wins_over_exact_targets_and_capacity_resets_every_week() -> None:
    source = revision(limited=True, days=28)
    recipes = (recipe(0), *(recipe(i, "1000") for i in range(1, 7)))
    generated = generate(source, recipes)
    assert not problems(generated)
    for week in range(4):
        count = Counter(e.recipe.recipe_id for e in generated.entries if e.day // 7 == week)
        assert sorted(count.values()) == [3] * 7
    assert any(d.difference[0] > 0 for d in calculate_menu(generated).days)


@pytest.mark.parametrize("count,limit", [(0, 3), (6, 3), (20, 1)])
def test_impossible_capacity_has_explanation_and_never_relaxes(count: int, limit: int) -> None:
    with pytest.raises(ValidationError, match="допуск|Увеличьте лимит"):
        generate(revision(limited=True, limit=limit), tuple(recipe(i) for i in range(count)))


def test_limit_one_and_large_library_remain_feasible_after_candidate_bounding() -> None:
    generated = generate(revision(limited=True, limit=1), tuple(recipe(i) for i in range(80)))
    assert len(generated.entries) == 21
    assert len({e.recipe.recipe_id for e in generated.entries}) == 21
    assert not problems(generated)


def test_neighboring_weeks_prefer_unused_recipes_when_nutrition_equal() -> None:
    generated = generate(revision(days=28), tuple(recipe(i) for i in range(18)))
    sets = [
        {e.recipe.recipe_id for e in generated.entries if e.day // 7 == week} for week in range(4)
    ]
    assert all(len(s) == 6 for s in sets)
    assert all(not a & b for a, b in zip(sets, sets[1:], strict=False))


def test_nutrition_takes_priority_over_diversity_and_multistart_finds_exact_mix() -> None:
    source = revision(calories="100")
    recipes = (recipe(0, "50"), recipe(1, "30"), recipe(2, "20"))
    generated = generate(source, recipes)
    assert all(d.total.calories == 100 for d in calculate_menu(generated).days)
    exact = generate(revision(calories="30"), (recipe(0), recipe(1, "1000")))
    assert {e.recipe.recipe_id for e in exact.entries} == {"recipe-0"}


def test_decimal_context_and_input_order_do_not_change_search_or_totals() -> None:
    source = revision(calories="0.3")
    recipes = tuple(recipe(i, "0.1") for i in range(7))
    with localcontext() as ctx:
        ctx.prec = 2
        generated = generate(source, recipes)
    assert all(d.total.calories == Decimal("0.3") for d in calculate_menu(generated).days)


def test_equal_calories_are_distinguished_by_all_three_macronutrients() -> None:
    source = replace(
        revision(), targets=Nutrition(Decimal(30), Decimal(30), Decimal(6), Decimal(15))
    )
    poor = recipe(0)
    matching = replace(
        recipe(1), nutrition=Nutrition(Decimal(10), Decimal(10), Decimal(2), Decimal(5))
    )
    generated = generate(source, (poor, matching))
    assert {e.recipe.recipe_id for e in generated.entries} == {matching.recipe_id}
    assert all(day.total == source.targets for day in calculate_menu(generated).days)
