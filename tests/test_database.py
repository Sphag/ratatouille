"""Migration and actual SQLite constraints, including raw cross-owner references."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from ratatouille.database import make_engine, migrate
from ratatouille.domain import IngredientInput, Nutrition, RecipeInput, Unit
from ratatouille.models import Base
from ratatouille.storage import Store


def test_engine_construction_does_not_create_database_and_migration_is_repeatable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "nested" / "test.sqlite3"
    engine = make_engine(path)
    assert not path.parent.exists()
    engine.dispose()
    migrate(path)
    migrate(path)
    engine = make_engine(path)
    try:
        assert set(inspect(engine).get_table_names()) == set(Base.metadata.tables) | {
            "alembic_version"
        }
        for table in Base.metadata.tables.values():
            actual = inspect(engine).get_foreign_keys(table.name)
            assert {
                (
                    tuple(fk["constrained_columns"]),
                    fk["referred_table"],
                    tuple(fk["referred_columns"]),
                )
                for fk in actual
            } == {
                (
                    tuple(element.parent.name for element in fk.elements),
                    fk.elements[0].column.table.name,
                    tuple(element.column.name for element in fk.elements),
                )
                for fk in table.foreign_key_constraints
            }
        with engine.connect() as first, engine.connect() as second:
            assert first.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            assert second.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            assert first.exec_driver_sql("PRAGMA recursive_triggers").scalar() == 1
            assert second.exec_driver_sql("PRAGMA recursive_triggers").scalar() == 1
            assert first.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            assert (
                first.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0004"
            )
    finally:
        engine.dispose()


def test_foreign_keys_reject_cross_owner_links_even_in_raw_sql(store: Store) -> None:
    owner, other = store.create_user(), store.create_user()
    ingredient = store.create_ingredient(owner, "Продукт")
    recipe = store.create_recipe(
        owner,
        RecipeInput(
            "Рецепт", "", 1, Nutrition(), (IngredientInput(ingredient, Decimal(1), Unit.GRAM),)
        ),
    )
    other_draft = store.create_draft(other, start_date=date(2026, 10, 12))
    with pytest.raises(IntegrityError), store.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO recipe_versions "
                "(id, owner_id, recipe_id, number, name, instructions, yield_portions, sealed, "
                "calories, protein, fat, carbs) "
                "VALUES ('row', :other, :recipe, 2, 'Продукт', '', 1, 0, '1', '1', '1', '1')"
            ),
            {"other": other, "recipe": recipe.recipe_id},
        )
    with pytest.raises(IntegrityError), store.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO menu_entries "
                "(id, owner_id, revision_id, day, slot, version_id) "
                "VALUES ('entry', :other, :revision, 0, 'dinner', :version)"
            ),
            {"other": other, "revision": other_draft.id, "version": recipe.version_id},
        )


def test_raw_day_outside_plan_and_pointer_to_draft_are_rejected(store: Store) -> None:
    owner = store.create_user()
    ingredient = store.create_ingredient(owner, "Продукт")
    recipe = store.create_recipe(
        owner,
        RecipeInput(
            "Рецепт", "", 1, Nutrition(), (IngredientInput(ingredient, Decimal(1), Unit.GRAM),)
        ),
    )
    draft = store.create_draft(owner, start_date=date(2026, 10, 12))
    with pytest.raises(IntegrityError), store.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO menu_entries "
                "(id, owner_id, revision_id, day, slot, version_id) "
                "VALUES ('entry', :owner, :revision, 7, 'dinner', :version)"
            ),
            {"owner": owner, "revision": draft.id, "version": recipe.version_id},
        )
    with pytest.raises(IntegrityError), store.engine.begin() as connection:
        connection.execute(
            text("UPDATE plans SET current_revision_id=:revision WHERE id=:plan"),
            {"revision": draft.id, "plan": draft.plan_id},
        )


def test_initial_schema_can_be_downgraded_and_recreated_on_empty_test_database(
    tmp_path: Path,
) -> None:
    path = tmp_path / "test.sqlite3"
    migrate(path)
    engine = make_engine(path)
    config = Config("alembic.ini")
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "base")
        assert inspect(engine).get_table_names() == ["alembic_version"]
        migrate(path)
        assert "recipes" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_importing_web_and_storage_does_not_create_database(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    path = tmp_path / "never-created.sqlite3"
    environment = os.environ.copy()
    environment["RATATOUILLE_DB_PATH"] = str(path)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import ratatouille.app; import ratatouille.storage; import ratatouille.local_app",
        ],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        timeout=15,
    )
    assert not path.exists()


def test_starter_migration_preserves_existing_recipe_and_confirmed_plan(tmp_path: Path) -> None:
    from ratatouille.domain import Slot

    path = tmp_path / "upgrade.sqlite3"
    engine = make_engine(path)
    config = Config("alembic.ini")
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0001")
        store = Store(engine)
        owner = store.create_user()
        ingredient = store.create_ingredient(owner, "Продукт")
        recipe = store.create_recipe(
            owner,
            RecipeInput(
                "Рецепт",
                "",
                1,
                Nutrition(calories=Decimal("0.00000001")),
                (IngredientInput(ingredient, Decimal("0.5"), Unit.PIECE),),
            ),
        )
        draft = store.create_draft(owner, start_date=date(2026, 10, 12))
        draft = store.set_entry(
            owner,
            draft.id,
            day=0,
            slot=Slot.DINNER,
            version_id=recipe.version_id,
            expected_number=draft.number,
        )
        confirmed = store.confirm(owner, draft.id, expected_number=draft.number)
        before = store.calculate(owner, confirmed.id)
        migrate(path)
        assert store.get_confirmed(owner, confirmed.plan_id) == confirmed
        assert store.calculate(owner, confirmed.id) == before
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0001")
        assert store.calculate(owner, confirmed.id) == before
        assert "starter_imports" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()
