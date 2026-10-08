"""Recipe management, approval and atomic repeatable starter imports."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ratatouille.catalog import Catalog, Flags, SaveRecipe, load_starters
from ratatouille.domain import ConflictError, NotFoundError, Slot, ValidationError
from ratatouille.local_app import initialize
from ratatouille.models import Ingredient, Recipe, StarterImport
from ratatouille.storage import Store


def card_data(*, eligible: bool = False) -> SaveRecipe:
    source = load_starters().recipes[0]
    return SaveRecipe.model_validate(
        source.model_dump(exclude={"source_id"}) | {"eligible": eligible}
    )


def test_edit_and_archive_preserve_confirmed_menu_and_manual_nutrition(store: Store) -> None:
    owner = store.create_user()
    catalog = Catalog(store)
    card = catalog.save(owner, card_data(eligible=True))
    draft = store.create_draft(owner, start_date=date(2026, 10, 12))
    draft = store.set_entry(
        owner,
        draft.id,
        day=0,
        slot=Slot.DINNER,
        version_id=card.version_id,
        expected_number=draft.number,
    )
    confirmed = store.confirm(owner, draft.id, expected_number=draft.number)
    before = store.calculate(owner, confirmed.id)
    edited = catalog.save(
        owner,
        card_data().model_copy(
            update={
                "name": "Новое название",
                "expected_version_id": card.version_id,
                "expected_eligible": card.eligible,
                "expected_archived": card.archived,
            }
        ),
        card.recipe_id,
    )
    assert not edited.eligible
    assert edited.version_id != card.version_id
    assert edited.nutrition == card.nutrition
    catalog.flags(
        owner,
        card.recipe_id,
        Flags(
            expected_version_id=edited.version_id,
            expected_eligible=False,
            expected_archived=False,
            eligible=False,
            archived=True,
        ),
    )
    assert catalog.list_cards(owner) == []
    assert catalog.list_cards(owner, archived=True)[0].archived
    assert store.calculate(owner, confirmed.id) == before
    catalog.flags(
        owner,
        card.recipe_id,
        Flags(
            expected_version_id=edited.version_id,
            expected_eligible=False,
            expected_archived=True,
            eligible=False,
            archived=False,
        ),
    )
    assert catalog.list_cards(owner)[0].name == "Новое название"


def test_stale_recipe_edit_and_flags_are_rejected_without_creating_ingredients(
    store: Store,
) -> None:
    owner = store.create_user()
    catalog = Catalog(store)
    card = catalog.save(owner, card_data())
    updated = catalog.save(
        owner,
        card_data().model_copy(
            update={
                "expected_version_id": card.version_id,
                "expected_eligible": card.eligible,
                "expected_archived": card.archived,
            }
        ),
        card.recipe_id,
    )
    with pytest.raises(ConflictError):
        catalog.save(
            owner,
            card_data().model_copy(
                update={
                    "expected_version_id": card.version_id,
                    "expected_eligible": card.eligible,
                    "expected_archived": card.archived,
                }
            ),
            card.recipe_id,
        )
    with pytest.raises(ConflictError):
        catalog.flags(
            owner,
            card.recipe_id,
            Flags(
                expected_version_id=card.version_id,
                expected_eligible=False,
                expected_archived=False,
                eligible=True,
                archived=False,
            ),
        )
    assert catalog.list_cards(owner) == [updated]


def test_owner_cannot_edit_or_reference_another_owners_card_or_ingredient(store: Store) -> None:
    owner, other = store.create_user(), store.create_user()
    catalog = Catalog(store)
    card = catalog.save(owner, card_data())
    assert catalog.list_cards(other) == []
    assert catalog.ingredients(other) == []
    with pytest.raises(NotFoundError):
        catalog.save(
            other,
            card_data().model_copy(
                update={
                    "expected_version_id": card.version_id,
                    "expected_eligible": card.eligible,
                    "expected_archived": card.archived,
                }
            ),
            card.recipe_id,
        )
    with pytest.raises(NotFoundError):
        catalog.flags(
            other,
            card.recipe_id,
            Flags(
                expected_version_id=card.version_id,
                expected_eligible=card.eligible,
                expected_archived=card.archived,
                eligible=True,
                archived=True,
            ),
        )
    item = card.ingredients[0]
    invalid = card_data().model_copy(
        update={
            "ingredients": [
                card_data().ingredients[0].model_copy(update={"ingredient_id": item.ingredient_id})
            ]
        }
    )
    with pytest.raises(NotFoundError):
        catalog.save(other, invalid)
    assert catalog.list_cards(other) == []


def test_exact_names_share_identity_but_different_names_remain_distinct(store: Store) -> None:
    owner = store.create_user()
    catalog = Catalog(store)
    first = catalog.save(owner, card_data())
    second = catalog.save(owner, card_data())
    assert first.ingredients[0].ingredient_id == second.ingredients[0].ingredient_id
    ingredients = card_data().ingredients.copy()
    ingredients[0] = ingredients[0].model_copy(update={"name": "Другой творог"})
    third = catalog.save(owner, card_data().model_copy(update={"ingredients": ingredients}))
    assert first.ingredients[0].ingredient_id != third.ingredients[0].ingredient_id


@pytest.mark.parametrize("approved,confirmed", [(False, True), (True, False), (False, False)])
def test_starters_require_project_approval_and_user_confirmation(
    store: Store, approved: bool, confirmed: bool
) -> None:
    owner = store.create_user()
    catalog = Catalog(store)
    with pytest.raises(ValidationError):
        catalog.import_starters(
            owner, load_starters().model_copy(update={"approved": approved}), confirmed=confirmed
        )
    assert catalog.list_cards(owner) == []
    assert catalog.ingredients(owner) == []


def test_import_is_personal_idempotent_and_keeps_manual_edits_and_archives(store: Store) -> None:
    owner, other = store.create_user(), store.create_user()
    catalog = Catalog(store)
    library = load_starters().model_copy(update={"approved": True})
    assert catalog.import_starters(owner, library, confirmed=True) == 18
    cards = catalog.list_cards(owner)
    assert len(cards) == 18 and not any(card.eligible for card in cards)
    assert catalog.list_cards(other) == []
    card = cards[0]
    catalog.flags(
        owner,
        card.recipe_id,
        Flags(
            expected_version_id=card.version_id,
            expected_eligible=card.eligible,
            expected_archived=card.archived,
            eligible=True,
            archived=True,
        ),
    )
    assert catalog.import_starters(owner, library, confirmed=True) == 0
    assert len(catalog.list_cards(owner)) == 17
    assert catalog.list_cards(owner, archived=True)[0].eligible
    assert catalog.import_starters(other, library, confirmed=True) == 18
    assert {c.recipe_id for c in catalog.list_cards(other)}.isdisjoint(c.recipe_id for c in cards)
    changed = library.model_copy(
        update={
            "recipes": [
                library.recipes[0].model_copy(update={"name": "Changed"}),
                *library.recipes[1:],
            ]
        }
    )
    with pytest.raises(ConflictError):
        catalog.import_starters(owner, changed, confirmed=True)


def test_invalid_last_starter_rolls_back_the_entire_import(store: Store) -> None:
    owner = store.create_user()
    catalog = Catalog(store)
    library = load_starters().model_copy(update={"approved": True})
    last = library.recipes[-1]
    invalid = last.model_copy(
        update={"ingredients": [last.ingredients[0].model_copy(update={"quantity": "0"})]}
    )
    library = library.model_copy(update={"recipes": [*library.recipes[:-1], invalid]})
    with pytest.raises(ValidationError):
        catalog.import_starters(owner, library, confirmed=True)
    with Session(store.engine) as session:
        for model in (Recipe, Ingredient, StarterImport):
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_two_parallel_imports_create_one_library(store: Store) -> None:
    owner = store.create_user()
    library = load_starters().model_copy(update={"approved": True})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: Catalog(store).import_starters(owner, library, confirmed=True), range(2)
            )
        )
    assert sorted(results) == [0, 18]
    assert len(Catalog(store).list_cards(owner)) == 18


def test_initializing_local_profile_twice_keeps_the_same_owner(store: Store) -> None:
    assert store.engine.url.database is not None
    from pathlib import Path

    path = Path(store.engine.url.database)
    assert initialize(path) == initialize(path)


def test_stale_flags_and_edits_cannot_overwrite_new_admission_or_archive(store: Store) -> None:
    owner = store.create_user()
    catalog = Catalog(store)
    card = catalog.save(owner, card_data())
    original = dict(
        expected_version_id=card.version_id, expected_eligible=False, expected_archived=False
    )
    updated = catalog.flags(
        owner,
        card.recipe_id,
        Flags.model_validate(original | {"eligible": True, "archived": False}),
    )
    with pytest.raises(ConflictError):
        catalog.save(owner, card_data().model_copy(update=original), card.recipe_id)
    with pytest.raises(ConflictError):
        catalog.flags(
            owner,
            card.recipe_id,
            Flags.model_validate(original | {"eligible": False, "archived": True}),
        )
    assert catalog.list_cards(owner) == [updated]
    archived = catalog.flags(
        owner,
        card.recipe_id,
        Flags(
            expected_version_id=card.version_id,
            expected_eligible=True,
            expected_archived=False,
            eligible=True,
            archived=True,
        ),
    )
    with pytest.raises(ConflictError):
        catalog.flags(
            owner,
            card.recipe_id,
            Flags(
                expected_version_id=card.version_id,
                expected_eligible=True,
                expected_archived=False,
                eligible=True,
                archived=False,
            ),
        )
    assert catalog.list_cards(owner, archived=True) == [archived]
