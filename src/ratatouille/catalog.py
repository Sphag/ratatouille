"""Validated recipe cards and atomic owner-scoped library operations."""

from hashlib import sha256
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ratatouille.domain import (
    ConflictError,
    IngredientInput,
    Nutrition,
    RecipeInput,
    RecipeSnapshot,
    Unit,
    ValidationError,
    decimal_value,
)
from ratatouille.models import Ingredient, Recipe, StarterImport
from ratatouille.storage import Store

DecimalText = Annotated[str, Field(pattern=r"^\d{1,30}(\.\d{1,20})?$", max_length=51)]
Name = Annotated[str, Field(min_length=1, max_length=200)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True, frozen=True)


class NutritionFields(Contract):
    calories: DecimalText
    protein: DecimalText
    fat: DecimalText
    carbs: DecimalText

    def domain(self) -> Nutrition:
        return Nutrition(
            *(decimal_value(value) for value in (self.calories, self.protein, self.fat, self.carbs))
        )


class IngredientFields(Contract):
    name: Name
    quantity: DecimalText
    unit: Unit = Field(strict=False)
    ingredient_id: str | None = None


class RecipeFields(Contract):
    name: Name
    instructions: str = Field(max_length=10000)
    yield_portions: int = Field(gt=0, le=10000)
    nutrition: NutritionFields
    ingredients: list[IngredientFields] = Field(min_length=1, max_length=200)


class SaveRecipe(RecipeFields):
    eligible: bool = False
    expected_version_id: str | None = None
    expected_eligible: bool | None = None
    expected_archived: bool | None = None


class Flags(Contract):
    expected_version_id: str
    expected_eligible: bool
    expected_archived: bool
    eligible: bool
    archived: bool


class StoredNutrition(Contract):
    calories: str
    protein: str
    fat: str
    carbs: str


class StoredIngredient(Contract):
    ingredient_id: str
    name: str
    quantity: str
    unit: Unit = Field(strict=False)


class RecipeCard(Contract):
    # Existing T05 values are exact and can exceed the new form's input bounds.
    name: str
    instructions: str
    yield_portions: int
    nutrition: StoredNutrition
    ingredients: list[StoredIngredient]
    recipe_id: str
    version_id: str
    eligible: bool
    archived: bool


class StarterRecipe(RecipeFields):
    source_id: Name


class StarterLibrary(Contract):
    id: Name
    approved: bool
    recipes: list[StarterRecipe] = Field(min_length=18, max_length=18)

    @property
    def digest(self) -> str:
        return sha256(self.model_dump_json(exclude={"approved"}).encode()).hexdigest()


def load_starters(path: Path | None = None) -> StarterLibrary:
    source = path or Path(__file__).resolve().parents[2] / "data/starter_recipes.json"
    library = StarterLibrary.model_validate_json(source.read_text(encoding="utf-8"))
    if len({recipe.source_id for recipe in library.recipes}) != 18:
        raise ValidationError("Идентификаторы стартовых рецептов должны быть уникальны.")
    return library


def _card(snapshot: RecipeSnapshot, row: Recipe) -> RecipeCard:
    n = snapshot.nutrition
    return RecipeCard(
        recipe_id=row.id,
        version_id=snapshot.version_id,
        name=snapshot.name,
        instructions=snapshot.instructions,
        yield_portions=snapshot.yield_portions,
        nutrition=StoredNutrition(
            calories=format(n.calories, "f"),
            protein=format(n.protein, "f"),
            fat=format(n.fat, "f"),
            carbs=format(n.carbs, "f"),
        ),
        ingredients=[
            StoredIngredient(
                ingredient_id=item.ingredient_id,
                name=item.name,
                quantity=format(item.quantity, "f"),
                unit=item.unit,
            )
            for item in snapshot.ingredients
        ],
        eligible=row.eligible,
        archived=row.archived,
    )


class Catalog:
    def __init__(self, store: Store) -> None:
        self.store = store

    def list_cards(self, owner: str, *, archived: bool = False) -> list[RecipeCard]:
        with self.store._session() as session:
            self.store._user(session, owner)
            rows = list(
                session.scalars(
                    select(Recipe).where(Recipe.owner_id == owner, Recipe.archived.is_(archived))
                )
            )
            snapshots = self.store._snapshots(
                session,
                owner,
                {row.current_version_id for row in rows if row.current_version_id is not None},
            )
            return sorted(
                [
                    _card(snapshots[row.current_version_id], row)
                    for row in rows
                    if row.current_version_id is not None
                ],
                key=lambda card: (card.name.casefold(), card.recipe_id),
            )

    def ingredients(self, owner: str) -> list[dict[str, str]]:
        with self.store._session() as session:
            self.store._user(session, owner)
            return [
                {"ingredient_id": row.id, "name": row.name}
                for row in session.scalars(
                    select(Ingredient).where(Ingredient.owner_id == owner).order_by(Ingredient.name)
                )
            ]

    def _input(self, session: Session, owner: str, data: RecipeFields) -> RecipeInput:
        items = []
        for item in data.ingredients:
            row: Ingredient | None
            # An explicit identity must belong to this owner, even if its name was changed.
            if item.ingredient_id is not None:
                row = self.store._owned(session, Ingredient, owner, item.ingredient_id)
                if row.name != item.name:
                    raise ConflictError("Название ингредиента изменилось. Обновите карточку.")
            else:
                row = session.scalar(
                    select(Ingredient)
                    .where(Ingredient.owner_id == owner, Ingredient.name == item.name)
                    .order_by(Ingredient.id)
                )
                if row is None:
                    row = Ingredient(owner_id=owner, name=item.name)
                    session.add(row)
                    session.flush()
            items.append(
                IngredientInput(row.id, decimal_value(item.quantity, positive=True), item.unit)
            )
        return RecipeInput(
            data.name, data.instructions, data.yield_portions, data.nutrition.domain(), tuple(items)
        )

    def save(self, owner: str, data: SaveRecipe, recipe_id: str | None = None) -> RecipeCard:
        with self.store._session(write=True) as session:
            self.store._user(session, owner)
            if recipe_id is None:
                if any(
                    value is not None
                    for value in (
                        data.expected_version_id,
                        data.expected_eligible,
                        data.expected_archived,
                    )
                ):
                    raise ValidationError("Новый рецепт не имеет предыдущей версии.")
                row = Recipe(owner_id=owner)
                session.add(row)
                session.flush()
            else:
                row = self.store._owned(session, Recipe, owner, recipe_id)
                if row.archived:
                    raise ConflictError("Сначала восстановите рецепт из архива.")
                if (
                    row.current_version_id != data.expected_version_id
                    or row.eligible != data.expected_eligible
                    or row.archived != data.expected_archived
                ):
                    raise ConflictError("Рецепт изменился. Обновите карточку перед сохранением.")
            version = self.store._new_version(
                session, owner, row, self._input(session, owner, data)
            )
            row.eligible = data.eligible
            return _card(self.store._snapshot(session, owner, version.id), row)

    def flags(self, owner: str, recipe_id: str, data: Flags) -> RecipeCard:
        with self.store._session(write=True) as session:
            row = self.store._owned(session, Recipe, owner, recipe_id)
            if (
                row.current_version_id != data.expected_version_id
                or row.eligible != data.expected_eligible
                or row.archived != data.expected_archived
            ):
                raise ConflictError("Рецепт изменился. Обновите карточку.")
            row.eligible, row.archived = data.eligible, data.archived
            assert row.current_version_id is not None
            return _card(self.store._snapshot(session, owner, row.current_version_id), row)

    def imported(self, owner: str, library: StarterLibrary) -> bool:
        with self.store._session() as session:
            self.store._user(session, owner)
            return (
                session.scalar(
                    select(StarterImport.id).where(
                        StarterImport.owner_id == owner, StarterImport.library_id == library.id
                    )
                )
                is not None
            )

    def import_starters(self, owner: str, library: StarterLibrary, *, confirmed: bool) -> int:
        if not library.approved or not confirmed:
            raise ValidationError("Сначала проверьте и подтвердите стартовые карточки и КБЖУ.")
        if len({recipe.source_id for recipe in library.recipes}) != 18:
            raise ValidationError("Идентификаторы стартовых рецептов должны быть уникальны.")
        with self.store._session(write=True) as session:
            self.store._user(session, owner)
            existing = session.scalar(
                select(StarterImport).where(
                    StarterImport.owner_id == owner, StarterImport.library_id == library.id
                )
            )
            if existing is not None:
                if existing.digest != library.digest:
                    raise ConflictError("Эта библиотека уже импортирована с другим составом.")
                return 0
            for data in library.recipes:
                row = Recipe(owner_id=owner)
                session.add(row)
                session.flush()
                self.store._new_version(session, owner, row, self._input(session, owner, data))
            session.add(StarterImport(owner_id=owner, library_id=library.id, digest=library.digest))
            return len(library.recipes)
