"""Snapshots, owner isolation, confirmation/cancellation and concurrent writes."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, time
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ratatouille.database import make_engine
from ratatouille.domain import (
    ConflictError,
    IngredientInput,
    NotFoundError,
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
)
from ratatouille.models import RecipeVersion, Revision
from ratatouille.storage import Store


def setup_recipe(
    store: Store, owner: str, *, quantity: str = "0.125", yield_portions: int = 1
) -> RecipeSnapshot:
    ingredient = store.create_ingredient(owner, "Продукт")
    return store.create_recipe(
        owner,
        RecipeInput(
            "Рецепт",
            "Приготовить",
            yield_portions,
            Nutrition(
                Decimal("123.45678901234567890123456789"), Decimal("10.1"), Decimal(5), Decimal(15)
            ),
            (IngredientInput(ingredient, Decimal(quantity), Unit.KILOGRAM),),
        ),
    )


def filled_draft(store: Store, owner: str, recipe: RecipeSnapshot) -> PlanRevision:
    draft = store.create_draft(owner, start_date=date(2026, 10, 12))
    return store.set_entry(
        owner,
        draft.id,
        day=0,
        slot=Slot.DINNER,
        version_id=recipe.version_id,
        expected_number=draft.number,
    )


def test_recipe_edit_rename_archive_and_goal_changes_preserve_confirmed_plan(store: Store) -> None:
    owner = store.create_user()
    recipe = setup_recipe(store, owner)
    store.save_goals(owner, Nutrition(calories=Decimal(2000)))
    draft = filled_draft(store, owner, recipe)
    confirmed = store.confirm(owner, draft.id, expected_number=draft.number)
    calculation = store.calculate(owner, confirmed.id)
    ingredient = recipe.ingredients[0].ingredient_id
    store.rename_ingredient(owner, ingredient, "Переименован")
    changed = store.edit_recipe(
        owner,
        recipe.recipe_id,
        RecipeInput(
            "Новый рецепт",
            "Другая инструкция",
            2,
            Nutrition(calories=Decimal(900)),
            (IngredientInput(ingredient, Decimal(2), Unit.GRAM),),
        ),
    )
    store.set_recipe_flags(owner, recipe.recipe_id, archived=True)
    store.save_goals(owner, Nutrition(calories=Decimal(3000)))
    assert store.get_confirmed(owner, draft.plan_id) == confirmed
    assert store.calculate(owner, confirmed.id) == calculation
    assert changed.version_id != recipe.version_id
    assert changed.ingredients[0].name == "Переименован"
    assert store.list_recipes(owner) == ()
    store.set_recipe_flags(owner, recipe.recipe_id, archived=False, eligible=True)
    assert store.list_recipes(owner, eligible_only=True) == (changed,)


def test_replacement_recalculates_draft_and_cancel_keeps_previous_plan(store: Store) -> None:
    owner = store.create_user()
    old = setup_recipe(store, owner)
    new = setup_recipe(store, owner, quantity="2")
    initial = filled_draft(store, owner, old)
    confirmed = store.confirm(owner, initial.id, expected_number=initial.number)
    draft = store.create_draft(owner, plan_id=initial.plan_id)
    assert draft.entries == confirmed.entries
    draft = store.set_entry(
        owner,
        draft.id,
        day=0,
        slot=Slot.DINNER,
        version_id=new.version_id,
        expected_number=draft.number,
    )
    assert store.calculate(owner, draft.id).shopping[0].quantity == 2000
    assert store.calculate(owner, confirmed.id).shopping[0].quantity == 125
    cancelled = store.cancel(owner, draft.id, expected_number=draft.number)
    assert cancelled.state == RevisionState.CANCELLED
    assert store.get_confirmed(owner, initial.plan_id) == confirmed
    assert store.cancel(owner, draft.id, expected_number=draft.number) == cancelled
    with pytest.raises(ConflictError):
        store.confirm(owner, draft.id, expected_number=cancelled.number)


def test_repeated_confirmation_is_idempotent_and_does_not_restore_an_old_revision(
    store: Store,
) -> None:
    owner = store.create_user()
    draft = filled_draft(store, owner, setup_recipe(store, owner))
    first = store.confirm(owner, draft.id, expected_number=draft.number)
    assert store.confirm(owner, draft.id, expected_number=draft.number) == first
    next_draft = store.create_draft(owner, plan_id=draft.plan_id)
    second = store.confirm(owner, next_draft.id, expected_number=next_draft.number)
    assert store.confirm(owner, draft.id, expected_number=draft.number) == first
    assert store.get_confirmed(owner, draft.plan_id) == second
    with Session(store.engine) as session:
        assert len(list(session.scalars(select(Revision)))) == 2


def test_stale_edits_do_not_change_menu_or_number_and_errors_roll_back(store: Store) -> None:
    owner = store.create_user()
    recipe = setup_recipe(store, owner)
    draft = filled_draft(store, owner, recipe)
    with pytest.raises(ConflictError):
        store.set_entry(
            owner,
            draft.id,
            day=0,
            slot=Slot.DINNER,
            version_id=None,
            expected_number=draft.number - 1,
        )
    with pytest.raises(ValidationError):
        store.set_entry(
            owner, draft.id, day=7, slot=Slot.DINNER, version_id=None, expected_number=draft.number
        )
    assert store.get_revision(owner, draft.id) == draft
    confirmed = store.confirm(owner, draft.id, expected_number=draft.number)
    with pytest.raises(ConflictError):
        store.set_entry(
            owner,
            draft.id,
            day=0,
            slot=Slot.DINNER,
            version_id=None,
            expected_number=confirmed.number,
        )


def test_two_parallel_edits_with_same_number_have_one_winner(store: Store) -> None:
    owner = store.create_user()
    recipe = setup_recipe(store, owner)
    draft = store.create_draft(owner, start_date=date(2026, 10, 12))
    barrier = Barrier(2)

    def edit(day: int) -> str:
        barrier.wait(timeout=10)
        try:
            store.set_entry(
                owner,
                draft.id,
                day=day,
                slot=Slot.DINNER,
                version_id=recipe.version_id,
                expected_number=draft.number,
            )
            return "saved"
        except ConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(edit, [0, 1])) == ["conflict", "saved"]
    saved = store.get_revision(owner, draft.id)
    assert saved.number == draft.number + 1
    assert len(saved.entries) == 1


def test_parallel_confirmation_of_two_drafts_cannot_overwrite_the_winner(store: Store) -> None:
    owner = store.create_user()
    first = filled_draft(store, owner, setup_recipe(store, owner))
    store.confirm(owner, first.id, expected_number=first.number)
    drafts = [store.create_draft(owner, plan_id=first.plan_id) for _ in range(2)]
    barrier = Barrier(2)

    def confirm(draft: PlanRevision) -> str:
        barrier.wait(timeout=10)
        try:
            store.confirm(owner, draft.id, expected_number=draft.number)
            return "saved"
        except ConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(confirm, drafts)) == ["conflict", "saved"]
    confirmed = store.get_confirmed(owner, first.plan_id)
    assert confirmed is not None and confirmed.id in {d.id for d in drafts}


def test_users_cannot_read_change_reference_or_check_each_others_objects(store: Store) -> None:
    owner, stranger = store.create_user(), store.create_user()
    recipe = setup_recipe(store, owner)
    draft = filled_draft(store, owner, recipe)
    other = store.create_draft(stranger, start_date=date(2026, 10, 12))
    with pytest.raises(NotFoundError):
        store.get_revision(stranger, draft.id)
    with pytest.raises(NotFoundError):
        store.get_confirmed(stranger, draft.plan_id)
    with pytest.raises(NotFoundError):
        store.set_recipe_flags(stranger, recipe.recipe_id, archived=True)
    with pytest.raises(NotFoundError):
        store.edit_recipe(
            stranger,
            recipe.recipe_id,
            RecipeInput(
                "Чужой",
                "",
                1,
                Nutrition(),
                (IngredientInput(recipe.ingredients[0].ingredient_id, Decimal(1), Unit.GRAM),),
            ),
        )
    with pytest.raises(NotFoundError):
        store.set_entry(
            stranger,
            other.id,
            day=0,
            slot=Slot.DINNER,
            version_id=recipe.version_id,
            expected_number=other.number,
        )
    with pytest.raises(NotFoundError):
        store.create_recipe(
            stranger,
            RecipeInput(
                "Чужой ингредиент",
                "",
                1,
                Nutrition(),
                (IngredientInput(recipe.ingredients[0].ingredient_id, Decimal(1), Unit.GRAM),),
            ),
        )
    with pytest.raises(NotFoundError):
        store.get_checks(stranger, draft.id)
    assert store.list_recipes(stranger) == ()
    assert store.get_revision(stranger, other.id) == other
    assert store.get_revision(owner, draft.id) == draft


def test_default_eligibility_archiving_and_reminders_are_explicit(store: Store) -> None:
    owner = store.create_user(telegram_id=123)
    assert store.create_user(telegram_id=123) == owner
    recipe = setup_recipe(store, owner)
    assert store.list_recipes(owner, eligible_only=True) == ()
    assert store.get_schedule(owner) == ()
    schedule = Schedule(ReminderKind.COOKING, 5, time(11, 30))
    store.save_schedule(owner, (schedule,))
    assert store.get_schedule(owner) == (schedule,)
    assert not store.get_schedule(owner)[0].enabled
    store.set_recipe_flags(owner, recipe.recipe_id, eligible=True, archived=True)
    assert store.list_recipes(owner, eligible_only=True) == ()
    draft = store.create_draft(owner, start_date=date(2026, 10, 12))
    with pytest.raises(ValidationError):
        store.set_entry(
            owner,
            draft.id,
            day=0,
            slot=Slot.DINNER,
            version_id=recipe.version_id,
            expected_number=draft.number,
        )


def test_personal_goals_and_schedule_remain_isolated_and_failed_save_preserves_data(
    store: Store,
) -> None:
    owner, stranger = store.create_user(), store.create_user()
    goals = Nutrition(calories=Decimal("2000.1"))
    store.save_goals(owner, goals)
    schedule = Schedule(ReminderKind.THAWING, 6, time(9, 30), enabled=True)
    store.save_schedule(owner, (schedule,))
    assert store.get_goals(stranger) == Nutrition()
    assert store.get_schedule(stranger) == ()
    with pytest.raises(ValidationError):
        store.save_schedule(owner, (schedule, schedule))
    assert store.get_schedule(owner) == (schedule,)
    assert store.get_goals(owner) == goals


def test_checklists_are_revision_scoped_and_not_copied_to_new_menu(store: Store) -> None:
    owner = store.create_user()
    recipe = setup_recipe(store, owner)
    draft = filled_draft(store, owner, recipe)
    ingredient = recipe.ingredients[0].ingredient_id
    with pytest.raises(ConflictError):
        store.mark_shopping(
            owner, draft.id, week=0, ingredient_id=ingredient, unit=Unit.GRAM, checked=True
        )
    first = store.confirm(owner, draft.id, expected_number=draft.number)
    store.mark_shopping(
        owner, first.id, week=0, ingredient_id=ingredient, unit=Unit.GRAM, checked=True
    )
    store.mark_cooking(owner, first.id, week=0, version_id=recipe.version_id, checked=True)
    assert store.get_checks(owner, first.id) == (
        {(0, ingredient, Unit.GRAM): True},
        {(0, recipe.version_id): True},
    )
    with pytest.raises(NotFoundError):
        store.mark_shopping(
            owner, first.id, week=1, ingredient_id=ingredient, unit=Unit.GRAM, checked=True
        )
    with pytest.raises(NotFoundError):
        store.mark_cooking(owner, first.id, week=0, version_id="unknown", checked=True)
    copied = store.create_draft(owner, plan_id=first.plan_id)
    second = store.confirm(owner, copied.id, expected_number=copied.number)
    assert store.get_checks(owner, second.id) == ({}, {})
    store.mark_shopping(
        owner, first.id, week=0, ingredient_id=ingredient, unit=Unit.GRAM, checked=False
    )
    assert not store.get_checks(owner, first.id)[0][0, ingredient, Unit.GRAM]


def test_recipe_decimal_values_survive_storage_reopening_exactly(store: Store) -> None:
    owner = store.create_user()
    recipe = setup_recipe(store, owner, quantity="0.12345678901234567890123456789")
    path = store.engine.url.database
    assert path is not None
    engine = make_engine(Path(path))
    try:
        reopened = Store(engine)
        assert reopened.list_recipes(owner) == (recipe,)
        assert reopened.get_goals(owner) == Nutrition()
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "mode,limit", [(RepeatMode.AB, None), (RepeatMode.LIMITED, 3), (RepeatMode.LIMITED, 5)]
)
def test_both_modes_and_28_day_duration_round_trip(
    store: Store, mode: RepeatMode, limit: int | None
) -> None:
    owner = store.create_user()
    draft = store.create_draft(
        owner, start_date=date(2026, 10, 12), days=28, mode=mode, repeat_limit=limit
    )
    assert draft.mode == mode
    assert draft.repeat_limit == limit
    assert draft.days == 28
    assert store.get_revision(owner, draft.id) == draft


@pytest.mark.parametrize(
    "days,mode,limit",
    [
        (8, RepeatMode.AB, None),
        (True, RepeatMode.AB, None),
        (7, RepeatMode.AB, 3),
        (7, RepeatMode.LIMITED, 0),
        (7, RepeatMode.LIMITED, True),
    ],
)
def test_invalid_plan_configuration_is_rejected(
    store: Store, days: Any, mode: RepeatMode, limit: Any
) -> None:
    owner = store.create_user()
    with pytest.raises(ValidationError):
        store.create_draft(
            owner, start_date=date(2026, 10, 12), days=days, mode=mode, repeat_limit=limit
        )


def test_database_prevents_mutation_of_sealed_versions_and_confirmed_entries(store: Store) -> None:
    owner = store.create_user()
    recipe = setup_recipe(store, owner)
    draft = filled_draft(store, owner, recipe)
    store.confirm(owner, draft.id, expected_number=draft.number)
    for statement, parameters in [
        ("UPDATE recipe_versions SET name='bad' WHERE id=:id", {"id": recipe.version_id}),
        ("DELETE FROM recipe_ingredients WHERE version_id=:id", {"id": recipe.version_id}),
        ("DELETE FROM menu_entries WHERE revision_id=:id", {"id": draft.id}),
        ("UPDATE plan_revisions SET calories='999' WHERE id=:id", {"id": draft.id}),
        ("UPDATE plans SET days=28 WHERE id=:id", {"id": draft.plan_id}),
    ]:
        with pytest.raises(IntegrityError), store.engine.begin() as connection:
            connection.execute(text(statement), parameters)
    with Session(store.engine) as session:
        version = session.get(RecipeVersion, recipe.version_id)
        assert version is not None and version.sealed


@pytest.mark.parametrize(
    "table,column,value",
    [
        ("recipe_versions", "name", "Changed by REPLACE"),
        ("recipe_ingredients", "quantity", "999"),
        ("plan_revisions", "calories", "999"),
        ("plans", "start_date", "2027-01-01"),
        ("menu_entries", "day", 1),
    ],
)
def test_replace_cannot_change_confirmed_snapshots(
    store: Store, table: str, column: str, value: str | int
) -> None:
    owner = store.create_user()
    recipe = setup_recipe(store, owner)
    draft = filled_draft(store, owner, recipe)
    confirmed = store.confirm(owner, draft.id, expected_number=draft.number)
    calculation = store.calculate(owner, confirmed.id)
    filters = {
        "recipe_versions": ("id", recipe.version_id),
        "recipe_ingredients": ("version_id", recipe.version_id),
        "plan_revisions": ("id", confirmed.id),
        "plans": ("id", confirmed.plan_id),
        "menu_entries": ("revision_id", confirmed.id),
    }
    key, identifier = filters[table]
    with store.engine.connect() as connection:
        row = dict(
            connection.execute(
                text(f"SELECT * FROM {table} WHERE {key}=:id LIMIT 1"), {"id": identifier}
            )
            .mappings()
            .one()
        )
    row[column] = value
    columns = ", ".join(row)
    parameters = ", ".join(f":{name}" for name in row)
    with pytest.raises(IntegrityError), store.engine.begin() as connection:
        connection.execute(
            text(f"INSERT OR REPLACE INTO {table} ({columns}) VALUES ({parameters})"), row
        )
    assert store.get_confirmed(owner, confirmed.plan_id) == confirmed
    assert store.calculate(owner, confirmed.id) == calculation
