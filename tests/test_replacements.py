"""Replacement scope, exact totals/shopping, admission, stale and concurrent writes."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from fractions import Fraction
from threading import Barrier

import pytest
from test_menus import fill, new_menu
from test_storage import setup_recipe

from ratatouille.domain import (
    ConflictError,
    IngredientInput,
    NotFoundError,
    Nutrition,
    RecipeInput,
    Slot,
    ValidationError,
)
from ratatouille.menus import MenuView
from ratatouille.replacements import (
    ApplyReplacement,
    ReplacementLocation,
    ReplacementRequest,
    Replacements,
    Scenario,
)
from ratatouille.storage import Store


def request(number: int, version: str, **extra: object) -> ReplacementRequest:
    return ReplacementRequest.model_validate(
        dict(expected_number=number, day=0, slot="breakfast", version_id=version, **extra)
    )


def apply(service: Replacements, owner: str, identifier: str, data: ReplacementRequest) -> MenuView:
    preview = service.preview(owner, identifier, data)
    return service.apply(
        owner, identifier, ApplyReplacement(**data.model_dump(), digest=preview.digest)
    )


@pytest.mark.parametrize("limited", [False, True])
def test_keep_preserves_other_positions_and_weeks_exact_shopping_and_saved_plan(
    store: Store, limited: bool
) -> None:
    menus, owner, menu = new_menu(store, days=28, limited=limited)
    recipes = [
        setup_recipe(store, owner, quantity="0.125", yield_portions=2)
        for _ in range(7 if limited else 1)
    ]
    menu = fill(menus, owner, menu, [r.version_id for r in recipes])
    menu = menus.confirm(owner, menu.id, menu.number)
    saved = store.get_confirmed(owner, menu.plan_id)
    draft = store.create_draft(owner, plan_id=menu.plan_id)
    selected = setup_recipe(store, owner, quantity="0.5", yield_portions=2)
    selected = store.edit_recipe(
        owner,
        selected.recipe_id,
        RecipeInput(
            "Новая порция",
            "",
            2,
            Nutrition(Decimal(500), Decimal(20), Decimal(10), Decimal(50)),
            tuple(
                IngredientInput(i.ingredient_id, i.quantity, i.unit) for i in selected.ingredients
            ),
        ),
    )
    data = request(draft.number, selected.version_id)
    service = Replacements(menus)
    preview = service.preview(owner, draft.id, data)
    assert store.get_revision(owner, draft.id) == draft
    days = {0} if limited else {0, 2, 4, 6}
    assert {(p.day, p.slot) for p in preview.changes} == {(d, Slot.BREAKFAST) for d in days}
    assert sum(Decimal(s.quantity) for s in preview.menu.shopping if s.week == 0) == Decimal(
        "62.5"
    ) * 21 + Decimal("187.5") * len(days)
    changed = apply(service, owner, draft.id, data)
    assert changed.number == draft.number + 1 and changed.state == "draft"
    assert changed.totals[0].total != menu.totals[0].total
    assert changed.totals == preview.menu.totals and changed.shopping == preview.menu.shopping
    assert store.get_confirmed(owner, menu.plan_id) == saved
    store.cancel(owner, draft.id, expected_number=changed.number)
    assert store.get_confirmed(owner, menu.plan_id) == saved


@pytest.mark.parametrize("limited", [False, True])
def test_rebalance_locks_selected_and_other_weeks_and_improves_goals(
    store: Store, limited: bool
) -> None:
    menus, owner, menu = new_menu(store, days=28, limited=limited)
    recipes = [setup_recipe(store, owner) for _ in range(10)]
    high = setup_recipe(store, owner)
    high = store.edit_recipe(
        owner,
        high.recipe_id,
        RecipeInput(
            "Ближе к целям",
            "",
            1,
            Nutrition(Decimal(600), Decimal(30), Decimal(20), Decimal(70)),
            tuple(IngredientInput(i.ingredient_id, i.quantity, i.unit) for i in high.ingredients),
        ),
    )
    for r in [*recipes, high]:
        store.set_recipe_flags(owner, r.recipe_id, eligible=True)
    menu = fill(menus, owner, menu, [r.version_id for r in recipes[: 7 if limited else 1]])
    service = Replacements(menus)
    data = request(menu.number, recipes[8].version_id, scenario="rebalance")
    keep = service.preview(owner, menu.id, data.model_copy(update={"scenario": Scenario.KEEP}))
    preview = service.preview(owner, menu.id, data)
    assert not preview.menu.problems
    assert [e for e in preview.menu.entries if e.day >= 7] == [
        e for e in menu.entries if e.day >= 7
    ]
    selected_days = {0} if limited else {0, 2, 4, 6}
    assert all(
        next(e for e in preview.menu.entries if e.day == d and e.slot == Slot.BREAKFAST).version_id
        == recipes[8].version_id
        for d in selected_days
    )

    def deviation(value: MenuView) -> Fraction:
        return sum(
            (
                abs(Fraction(getattr(d.difference, field)))
                / max(Fraction(getattr(value.targets, field)), Fraction(1))
                for d in value.totals[:7]
                for field in ("calories", "protein", "fat", "carbs")
            ),
            Fraction(),
        )

    assert deviation(preview.menu) < deviation(keep.menu)
    assert store.get_revision(owner, menu.id).number == menu.number
    assert apply(service, owner, menu.id, data).state == "draft"


def test_rebalance_repairs_limited_conflict_without_loosening_limit(store: Store) -> None:
    menus, owner, menu = new_menu(store, limited=True)
    recipes = [setup_recipe(store, owner) for _ in range(8)]
    for r in recipes:
        store.set_recipe_flags(owner, r.recipe_id, eligible=True)
    menu = fill(menus, owner, menu, [r.version_id for r in recipes[:7]])
    data = request(menu.number, recipes[1].version_id)
    service = Replacements(menus)
    with pytest.raises(ValidationError, match="лимит"):
        service.preview(owner, menu.id, data)
    data = ReplacementRequest.model_validate(data.model_dump() | {"scenario": "rebalance"})
    preview = service.preview(owner, menu.id, data)
    assert not preview.menu.problems and preview.menu.repeat_limit == 3
    assert (
        next(e for e in preview.menu.entries if e.day == 0 and e.slot == Slot.BREAKFAST).version_id
        == recipes[1].version_id
    )


def test_automatic_options_are_admitted_bounded_and_read_only(store: Store) -> None:
    menus, owner, menu = new_menu(store)
    original = setup_recipe(store, owner)
    menu = fill(menus, owner, menu, [original.version_id])
    selected = [setup_recipe(store, owner) for _ in range(7)]
    for r in selected:
        store.set_recipe_flags(owner, r.recipe_id, eligible=True)
    archived = setup_recipe(store, owner)
    store.set_recipe_flags(owner, archived.recipe_id, eligible=True, archived=True)
    foreign_owner = store.create_user()
    foreign = setup_recipe(store, foreign_owner)
    store.set_recipe_flags(foreign_owner, foreign.recipe_id, eligible=True)
    before = store.get_revision(owner, menu.id)
    options = Replacements(menus).suggestions(
        owner, menu.id, ReplacementLocation(expected_number=menu.number, day=0, slot=Slot.BREAKFAST)
    )
    assert len(options) == 5
    assert {
        e.version_id for m in options for e in m.entries if e.day == 0 and e.slot == Slot.BREAKFAST
    } <= {r.version_id for r in selected}
    assert store.get_revision(owner, menu.id) == before


@pytest.mark.parametrize("change", ["withdraw", "archive", "edit", "draft", "cancel", "tamper"])
def test_stale_replacement_is_atomic(store: Store, change: str) -> None:
    menus, owner, menu = new_menu(store)
    old = setup_recipe(store, owner)
    selected = setup_recipe(store, owner)
    store.set_recipe_flags(owner, selected.recipe_id, eligible=True)
    menu = fill(menus, owner, menu, [old.version_id])
    data = request(menu.number, selected.version_id, automatic=True)
    service = Replacements(menus)
    preview = service.preview(owner, menu.id, data)
    if change == "withdraw":
        store.set_recipe_flags(owner, selected.recipe_id, eligible=False)
    elif change == "archive":
        store.set_recipe_flags(owner, selected.recipe_id, archived=True)
    elif change == "edit":
        store.edit_recipe(
            owner,
            selected.recipe_id,
            RecipeInput(
                "Новое",
                "",
                1,
                Nutrition(Decimal(1), Decimal(1), Decimal(1), Decimal(1)),
                tuple(
                    IngredientInput(i.ingredient_id, i.quantity, i.unit)
                    for i in selected.ingredients
                ),
            ),
        )
    elif change == "draft":
        fill(menus, owner, menu, [old.version_id])
    elif change == "cancel":
        store.cancel(owner, menu.id, expected_number=menu.number)
    before = store.get_revision(owner, menu.id)
    with pytest.raises(ConflictError):
        service.apply(
            owner,
            menu.id,
            ApplyReplacement(
                **data.model_dump(), digest="bad" if change == "tamper" else preview.digest
            ),
        )
    assert store.get_revision(owner, menu.id) == before


def test_foreign_selection_and_concurrent_apply(store: Store) -> None:
    menus, owner, menu = new_menu(store)
    old = setup_recipe(store, owner)
    menu = fill(menus, owner, menu, [old.version_id])
    other = store.create_user()
    foreign = setup_recipe(store, other)
    service = Replacements(menus)
    with pytest.raises(NotFoundError):
        service.preview(owner, menu.id, request(menu.number, foreign.version_id))
    selected = setup_recipe(store, owner)
    data = request(menu.number, selected.version_id)
    preview = service.preview(owner, menu.id, data)
    barrier = Barrier(2)

    def action(_: int) -> str:
        barrier.wait()
        try:
            return service.apply(
                owner, menu.id, ApplyReplacement(**data.model_dump(), digest=preview.digest)
            ).state
        except ConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(action, range(2))) == ["conflict", "draft"]
