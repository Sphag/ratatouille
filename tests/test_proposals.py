"""Read-only preview, rechecked admission, atomic application and saved-plan protection."""

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
    RecipeInput,
    Slot,
    ValidationError,
)
from ratatouille.menus import Expected, Position, SetPositions
from ratatouille.proposals import ApplyProposal, Proposals, digest
from ratatouille.storage import Store


def test_preview_does_not_write_and_apply_changes_only_draft_then_cancel(store: Store) -> None:
    menus, owner, menu = new_menu(store)
    original = setup_recipe(store, owner)
    menu = fill(menus, owner, menu, [original.version_id])
    menus.confirm(owner, menu.id, menu.number)
    saved = store.get_confirmed(owner, menu.plan_id)
    draft = store.create_draft(owner, plan_id=menu.plan_id)
    allowed = setup_recipe(store, owner)
    store.set_recipe_flags(owner, allowed.recipe_id, eligible=True)
    service = Proposals(menus)
    proposal = service.preview(owner, draft.id, Expected(expected_number=draft.number))
    assert store.get_revision(owner, draft.id) == draft
    assert {e.recipe_id for e in proposal.menu.entries} == {allowed.recipe_id}
    applied = service.apply(
        owner,
        draft.id,
        ApplyProposal(
            expected_number=draft.number, positions=proposal.positions, digest=proposal.digest
        ),
    )
    assert applied.state == "draft" and applied.number == draft.number + 1
    assert store.get_confirmed(owner, menu.plan_id) == saved
    store.cancel(owner, draft.id, expected_number=applied.number)
    assert store.get_confirmed(owner, menu.plan_id) == saved


@pytest.mark.parametrize("change", ["withdraw", "archive", "edit", "draft", "cancel", "tamper"])
def test_stale_proposals_are_rejected_atomically(store: Store, change: str) -> None:
    menus, owner, menu = new_menu(store)
    recipe = setup_recipe(store, owner)
    store.set_recipe_flags(owner, recipe.recipe_id, eligible=True)
    service = Proposals(menus)
    proposal = service.preview(owner, menu.id, Expected(expected_number=menu.number))
    checksum = proposal.digest
    if change == "withdraw":
        store.set_recipe_flags(owner, recipe.recipe_id, eligible=False)
    elif change == "archive":
        store.set_recipe_flags(owner, recipe.recipe_id, archived=True)
    elif change == "edit":
        store.edit_recipe(
            owner,
            recipe.recipe_id,
            RecipeInput(
                "Другой",
                "",
                1,
                recipe.nutrition,
                (
                    IngredientInput(
                        recipe.ingredients[0].ingredient_id, Decimal(1), recipe.ingredients[0].unit
                    ),
                ),
            ),
        )
    elif change == "draft":
        menus.set_positions(
            owner,
            menu.id,
            SetPositions(
                expected_number=menu.number,
                positions=[
                    Position(day=0, slot=proposal.positions[0].slot, version_id=recipe.version_id)
                ],
            ),
        )
    elif change == "cancel":
        store.cancel(owner, menu.id, expected_number=menu.number)
    else:
        checksum = "forged"
    before = store.get_revision(owner, menu.id)
    with pytest.raises(ConflictError):
        service.apply(
            owner,
            menu.id,
            ApplyProposal(
                expected_number=menu.number, positions=proposal.positions, digest=checksum
            ),
        )
    assert store.get_revision(owner, menu.id) == before
    assert store.get_confirmed(owner, menu.plan_id) is None


def test_empty_unapproved_archived_and_other_owner_recipes_are_excluded(store: Store) -> None:
    menus, owner, menu = new_menu(store)
    excluded = setup_recipe(store, owner)
    archived = setup_recipe(store, owner)
    store.set_recipe_flags(owner, archived.recipe_id, eligible=True, archived=True)
    other = store.create_user()
    foreign = setup_recipe(store, other)
    store.set_recipe_flags(other, foreign.recipe_id, eligible=True)
    with pytest.raises(ValidationError, match="явным допуском"):
        Proposals(menus).preview(owner, menu.id, Expected(expected_number=menu.number))
    assert not store.get_revision(owner, menu.id).entries
    assert not store.list_recipes(owner, eligible_only=True)
    assert excluded.recipe_id != foreign.recipe_id
    with pytest.raises(NotFoundError):
        Proposals(menus).preview(other, menu.id, Expected(expected_number=menu.number))


def test_forged_checksum_never_bypasses_admission_or_mode_rules(store: Store) -> None:
    menus, owner, menu = new_menu(store, limited=True)
    recipe = setup_recipe(store, owner)
    store.set_recipe_flags(owner, recipe.recipe_id, eligible=True)
    source = store.get_revision(owner, menu.id)
    positions = [
        Position(day=d, slot=s, version_id=recipe.version_id)
        for d in range(7)
        for s in (
            Slot.BREAKFAST,
            Slot.SECOND,
            Slot.DINNER,
        )
    ]
    with pytest.raises(ValidationError, match="лимит"):
        Proposals(menus).apply(
            owner,
            menu.id,
            ApplyProposal(
                expected_number=menu.number, positions=positions, digest=digest(source, positions)
            ),
        )
    assert store.get_revision(owner, menu.id) == source


def test_concurrent_application_only_advances_once(store: Store) -> None:
    menus, owner, menu = new_menu(store)
    recipe = setup_recipe(store, owner)
    store.set_recipe_flags(owner, recipe.recipe_id, eligible=True)
    service = Proposals(menus)
    proposal = service.preview(owner, menu.id, Expected(expected_number=menu.number))
    data = ApplyProposal(
        expected_number=menu.number, positions=proposal.positions, digest=proposal.digest
    )
    barrier = Barrier(2)

    def apply(_: int) -> str:
        barrier.wait()
        try:
            return service.apply(owner, menu.id, data).state
        except ConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(apply, range(2))) == ["conflict", "draft"]
    assert store.get_revision(owner, menu.id).number == 2
