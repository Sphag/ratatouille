"""Weekly exact outputs and revision-bound optimistic checklist behavior."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
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
    Unit,
)
from ratatouille.fulfilment import CookingRequest, Fulfilment, ShoppingRequest
from ratatouille.menus import Position, SetPositions
from ratatouille.storage import Store


@pytest.mark.parametrize("days", [7, 28])
def test_weekly_quantities_batches_and_distinct_units(store: Store, days: int) -> None:
    menus, owner, menu = new_menu(store, days=days)
    ingredient = store.create_ingredient(owner, "Продукт")
    a = store.create_recipe(
        owner,
        RecipeInput(
            "A",
            "",
            2,
            Nutrition(),
            (
                IngredientInput(ingredient, Decimal(1), Unit.KILOGRAM),
                IngredientInput(ingredient, Decimal(500), Unit.MILLILITER),
            ),
        ),
    )
    b = store.create_recipe(
        owner,
        RecipeInput(
            "B",
            "",
            1,
            Nutrition(),
            (
                IngredientInput(ingredient, Decimal(500), Unit.GRAM),
                IngredientInput(ingredient, Decimal(1), Unit.PIECE),
            ),
        ),
    )
    menu = fill(menus, owner, menu, [a.version_id])
    menu = menus.set_positions(
        owner,
        menu.id,
        SetPositions(
            expected_number=menu.number,
            positions=[
                Position(day=d, slot=Slot.DINNER, version_id=b.version_id) for d in range(days)
            ],
        ),
    )
    result = Fulfilment(store).get(owner, menu.id)
    assert result.state == "draft" and not any(s.checked for s in result.shopping)
    for week in range(days // 7):
        lines = {s.unit: Decimal(s.quantity) for s in result.shopping if s.week == week}
        assert lines == {
            Unit.GRAM: Decimal(10500),
            Unit.MILLILITER: Decimal(3500),
            Unit.PIECE: Decimal(7),
        }
        batches = {b.name: b for b in result.cooking if b.week == week}
        assert batches["A"].portions == 14 and batches["B"].portions == 7
        assert {s.unit: Decimal(s.quantity) for s in batches["A"].ingredients} == {
            Unit.GRAM: Decimal(7000),
            Unit.MILLILITER: Decimal(3500),
        }


def test_checks_persist_stale_updates_rejected_and_new_revision_unchecked(store: Store) -> None:
    menus, owner, menu = new_menu(store, days=28)
    r = setup_recipe(store, owner)
    menu = fill(menus, owner, menu, [r.version_id])
    service = Fulfilment(store)
    item = service.get(owner, menu.id).shopping[0]
    shopping = ShoppingRequest(
        week=item.week,
        ingredient_id=item.ingredient_id,
        unit=item.unit,
        checked=True,
        expected_checked=False,
    )
    with pytest.raises(ConflictError):
        service.mark(owner, menu.id, shopping)
    menu = menus.confirm(owner, menu.id, menu.number)
    before = store.get_revision(owner, menu.id)
    result = service.mark(owner, menu.id, shopping)
    assert result.shopping[0].checked
    cooking = CookingRequest(week=0, version_id=r.version_id, checked=True, expected_checked=False)
    assert service.mark(owner, menu.id, cooking).cooking[0].checked
    restarted = Fulfilment(Store(store.engine)).get(owner, menu.id)
    assert restarted.shopping[0].checked and restarted.cooking[0].checked
    assert not any(s.checked for s in restarted.shopping if s.week > 0)
    with pytest.raises(ConflictError, match="другой вкладке"):
        service.mark(owner, menu.id, shopping)
    assert store.get_revision(owner, menu.id) == before
    draft = store.create_draft(owner, plan_id=menu.plan_id)
    changed = menus.confirm(owner, draft.id, draft.number)
    new = service.get(owner, changed.id)
    assert not any(s.checked for s in new.shopping) and not any(b.checked for b in new.cooking)
    assert service.get(owner, menu.id) == restarted


@pytest.mark.parametrize("kind", ["shopping", "cooking"])
def test_foreign_nonexistent_and_concurrent_check_updates(store: Store, kind: str) -> None:
    menus, owner, menu = new_menu(store)
    r = setup_recipe(store, owner)
    menu = fill(menus, owner, menu, [r.version_id])
    menu = menus.confirm(owner, menu.id, menu.number)
    service = Fulfilment(store)
    item = service.get(owner, menu.id).shopping[0]
    data = (
        ShoppingRequest(
            week=0,
            ingredient_id=item.ingredient_id,
            unit=item.unit,
            checked=True,
            expected_checked=False,
        )
        if kind == "shopping"
        else CookingRequest(week=0, version_id=r.version_id, checked=True, expected_checked=False)
    )
    other = store.create_user()
    with pytest.raises(NotFoundError):
        service.get(other, menu.id)
    with pytest.raises(NotFoundError):
        service.mark(other, menu.id, data)
    missing = data.model_copy(update={"week": 3})
    with pytest.raises(NotFoundError):
        service.mark(owner, menu.id, missing)
    barrier = Barrier(2)

    def mark(_: int) -> str:
        barrier.wait()
        try:
            service.mark(owner, menu.id, data)
            return "marked"
        except ConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(mark, range(2))) == ["conflict", "marked"]


def test_edit_archive_preserves_confirmed_outputs_and_marks(store: Store) -> None:
    menus, owner, menu = new_menu(store)
    r = setup_recipe(store, owner)
    menu = fill(menus, owner, menu, [r.version_id])
    menu = menus.confirm(owner, menu.id, menu.number)
    service = Fulfilment(store)
    before = service.get(owner, menu.id)
    store.set_recipe_flags(owner, r.recipe_id, archived=True)
    store.edit_recipe(
        owner,
        r.recipe_id,
        RecipeInput(
            "Изменён",
            "",
            1,
            Nutrition(),
            (IngredientInput(r.ingredients[0].ingredient_id, Decimal(999), Unit.GRAM),),
        ),
    )
    assert service.get(owner, menu.id) == before
