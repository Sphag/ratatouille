"""Manual menu rules, exact totals, atomic drafts and owner isolation."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier

import pytest
from pydantic import ValidationError as ContractError
from test_storage import setup_recipe

from ratatouille.catalog import NutritionFields
from ratatouille.domain import (
    ConflictError,
    NotFoundError,
    Nutrition,
    RepeatMode,
    Slot,
    ValidationError,
)
from ratatouille.menus import ConfigureMenu, CreateMenu, Menus, MenuView, Position, SetPositions
from ratatouille.storage import Store


def targets(value: str = "2000") -> NutritionFields:
    return NutritionFields(calories=value, protein="100", fat="70", carbs="250")


def new_menu(store: Store, *, days: int = 7, limited: bool = False) -> tuple[Menus, str, MenuView]:
    owner = store.create_user()
    menus = Menus(store)
    data = CreateMenu(
        start_date=date(2026, 12, 28),
        days=days,
        targets=targets(),
        mode=RepeatMode.LIMITED if limited else RepeatMode.AB,
    )
    return menus, owner, menus.create(owner, data)


def fill(menus: Menus, owner: str, menu: MenuView, versions: list[str]) -> MenuView:
    return menus.set_positions(
        owner,
        menu.id,
        SetPositions(
            expected_number=menu.number,
            positions=[
                Position(day=day, slot=slot, version_id=versions[day % len(versions)])
                for day in range(menu.days)
                for slot in (Slot.BREAKFAST, Slot.SECOND, Slot.DINNER)
            ],
        ),
    )


@pytest.mark.parametrize("days", [7, 28])
def test_ab_whole_portions_exact_totals_dates_and_soft_targets(store: Store, days: int) -> None:
    menus, owner, menu = new_menu(store, days=days)
    recipe = setup_recipe(store, owner)
    assert menu.mode == "ab" and menu.repeat_limit is None
    assert menu.start_date.isoformat() == "2026-12-28"
    assert menu.totals[-1].date.isoformat() == ("2027-01-03" if days == 7 else "2027-01-24")
    menu = fill(menus, owner, menu, [recipe.version_id])
    assert not menu.problems  # Same recipe may occupy all slots in A/B; no limited-mode cap.
    assert menu.totals[0].total.calories == "370.37036703703703670370370367"
    assert menu.totals[0].difference.calories == "-1629.62963296296296329629629633"
    confirmed = menus.confirm(owner, menu.id, menu.number)
    assert confirmed.state == "confirmed"
    assert menus.confirm(owner, menu.id, menu.number) == confirmed
    assert menus.list_plans(owner)[0].confirmed_id == menu.id
    assert not menus.list_plans(owner)[0].drafts


def test_failed_batch_is_atomic_and_stale_update_is_rejected(store: Store) -> None:
    menus, owner, menu = new_menu(store)
    recipe = setup_recipe(store, owner)
    other = store.create_user()
    foreign = setup_recipe(store, other)
    for invalid in (
        Position(day=7, slot=Slot.DINNER, version_id=recipe.version_id),
        Position(day=1, slot=Slot.DINNER, version_id=foreign.version_id),
    ):
        with pytest.raises((ValidationError, NotFoundError)):
            menus.set_positions(
                owner,
                menu.id,
                SetPositions(
                    expected_number=menu.number,
                    positions=[
                        Position(day=0, slot=Slot.BREAKFAST, version_id=recipe.version_id),
                        invalid,
                    ],
                ),
            )
        assert store.get_revision(owner, menu.id).number == menu.number
        assert not store.get_revision(owner, menu.id).entries
    menu = fill(menus, owner, menu, [recipe.version_id])
    with pytest.raises(ConflictError):
        menus.set_positions(
            owner,
            menu.id,
            SetPositions(
                expected_number=1, positions=[Position(day=0, slot=Slot.BREAKFAST, version_id=None)]
            ),
        )
    assert len(store.get_revision(owner, menu.id).entries) == 21


def test_ab_has_four_a_three_b_with_independent_templates_each_week(store: Store) -> None:
    menus, owner, menu = new_menu(store, days=28)
    recipes = [setup_recipe(store, owner) for _ in range(8)]
    menu = menus.set_positions(
        owner,
        menu.id,
        SetPositions(
            expected_number=menu.number,
            positions=[
                Position(
                    day=day, slot=slot, version_id=recipes[2 * (day // 7) + day % 7 % 2].version_id
                )
                for day in range(28)
                for slot in (Slot.BREAKFAST, Slot.SECOND, Slot.DINNER)
            ],
        ),
    )
    assert not menu.problems
    saved = menus.confirm(owner, menu.id, menu.number)
    for week in range(4):
        entries = [e for e in saved.entries if e.day // 7 == week and e.slot == Slot.BREAKFAST]
        assert sum(e.version_id == recipes[2 * week].version_id for e in entries) == 4
        assert sum(e.version_id == recipes[2 * week + 1].version_id for e in entries) == 3


def test_required_slots_and_ab_mismatch_block_confirmation_without_mutating(store: Store) -> None:
    menus, owner, menu = new_menu(store)
    with pytest.raises(ValidationError, match="21 слотов"):
        menus.confirm(owner, menu.id, menu.number)
    assert store.get_revision(owner, menu.id).number == menu.number
    recipe = setup_recipe(store, owner)
    another = setup_recipe(store, owner)
    menu = fill(menus, owner, menu, [recipe.version_id])
    menu = menus.set_positions(
        owner,
        menu.id,
        SetPositions(
            expected_number=menu.number,
            positions=[Position(day=0, slot=Slot.FIRST, version_id=another.version_id)],
        ),
    )
    with pytest.raises(ValidationError, match="A/B"):
        menus.confirm(owner, menu.id, menu.number)
    assert store.get_confirmed(owner, menu.plan_id) is None


def test_limit_counts_all_slots_and_recipe_versions_separately_each_week(store: Store) -> None:
    menus, owner, menu = new_menu(store, days=28, limited=True)
    recipes = [setup_recipe(store, owner) for _ in range(7)]
    menu = fill(menus, owner, menu, [r.version_id for r in recipes])
    assert menu.repeat_limit == 3 and not menu.problems
    # Exactly three occurrences of each recipe per week, repeated in all four weeks.
    saved = menus.confirm(owner, menu.id, menu.number)
    draft = store.create_draft(owner, plan_id=menu.plan_id)
    from ratatouille.domain import IngredientInput, RecipeInput

    updated = store.edit_recipe(
        owner,
        recipes[0].recipe_id,
        RecipeInput(
            "Новая версия",
            "",
            1,
            Nutrition(),
            (
                IngredientInput(
                    recipes[0].ingredients[0].ingredient_id,
                    Decimal(1),
                    recipes[0].ingredients[0].unit,
                ),
            ),
        ),
    )
    bad = menus.set_positions(
        owner,
        draft.id,
        SetPositions(
            expected_number=draft.number,
            positions=[Position(day=1, slot=Slot.FIRST, version_id=updated.version_id)],
        ),
    )
    assert any("Неделя 1" in p and "лимит" in p for p in bad.problems)
    with pytest.raises(ValidationError, match="лимит"):
        menus.confirm(owner, bad.id, bad.number)
    assert store.get_confirmed(owner, menu.plan_id).id == saved.id  # type: ignore[union-attr]


def test_configure_and_cancel_preserve_saved_menu_and_personal_goals(store: Store) -> None:
    menus, owner, menu = new_menu(store)
    store.save_goals(owner, Nutrition(calories=Decimal(1800)))
    recipe = setup_recipe(store, owner)
    menu = fill(menus, owner, menu, [recipe.version_id])
    saved = menus.confirm(owner, menu.id, menu.number)
    store.set_recipe_flags(owner, recipe.recipe_id, archived=True)
    draft = store.create_draft(owner, plan_id=menu.plan_id)
    changed = menus.configure(
        owner,
        draft.id,
        ConfigureMenu(
            expected_number=draft.number,
            mode=RepeatMode.LIMITED,
            repeat_limit=30,
            targets=targets("100"),
        ),
    )
    assert changed.totals[0].difference.calories == "270.37036703703703670370370367"
    assert store.get_goals(owner).calories == Decimal(1800)
    # Retained archived snapshots survive, but newly adding one is prohibited.
    with pytest.raises(ValidationError, match="Архивный"):
        menus.set_positions(
            owner,
            changed.id,
            SetPositions(
                expected_number=changed.number,
                positions=[Position(day=0, slot=Slot.FIRST, version_id=recipe.version_id)],
            ),
        )
    store.cancel(owner, changed.id, expected_number=changed.number)
    assert store.get_confirmed(owner, saved.plan_id).id == saved.id  # type: ignore[union-attr]
    assert not menus.list_plans(owner)[0].drafts


def test_two_confirmations_share_one_base_and_only_one_wins(store: Store) -> None:
    menus, owner, menu = new_menu(store)
    recipe = setup_recipe(store, owner)
    menu = fill(menus, owner, menu, [recipe.version_id])
    menus.confirm(owner, menu.id, menu.number)
    drafts = [store.create_draft(owner, plan_id=menu.plan_id) for _ in range(2)]
    barrier = Barrier(2)

    def confirm(index: int) -> str:
        barrier.wait()
        try:
            return menus.confirm(owner, drafts[index].id, drafts[index].number).state
        except ConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(confirm, range(2))) == ["confirmed", "conflict"]
    # A retry of the old confirmation never rolls the plan pointer back.
    menus.confirm(owner, menu.id, menu.number)
    assert store.get_confirmed(owner, menu.plan_id).id != menu.id  # type: ignore[union-attr]


def test_owner_scoping_and_cancelled_new_plan_hidden(store: Store) -> None:
    menus, owner, menu = new_menu(store)
    other = store.create_user()
    assert menus.list_plans(other) == []
    with pytest.raises(NotFoundError):
        menus.configure(other, menu.id, ConfigureMenu(expected_number=1, targets=targets()))
    store.cancel(owner, menu.id, expected_number=menu.number)
    assert menus.list_plans(owner) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"days": 8},
        {"days": True},
        {"start_date": date.max},
        {"repeat_limit": 3},
        {"mode": "limited", "repeat_limit": 0},
        {"mode": "limited", "repeat_limit": True},
    ],
)
def test_invalid_dates_durations_and_limits(changes: dict[str, object]) -> None:
    with pytest.raises(ContractError):
        CreateMenu.model_validate({"start_date": date(2026, 1, 1), "targets": targets(), **changes})


def test_duplicate_batch_positions_rejected() -> None:
    with pytest.raises(ContractError):
        SetPositions(
            expected_number=1, positions=[Position(day=0, slot=Slot.DINNER, version_id=None)] * 2
        )
