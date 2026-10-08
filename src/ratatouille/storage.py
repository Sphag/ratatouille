"""Owner-scoped transactional services; HTTP authentication belongs to T11."""

from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import TypeVar

from sqlalchemy import Engine, delete, select, update
from sqlalchemy.orm import Session

from ratatouille.calculations import MenuCalculation, calculate_menu
from ratatouille.domain import (
    ConflictError,
    IngredientSnapshot,
    MenuEntry,
    NotFoundError,
    Nutrition,
    PlanRevision,
    RecipeInput,
    RecipeSnapshot,
    RepeatMode,
    RevisionState,
    Schedule,
    Slot,
    Unit,
    ValidationError,
    nonempty,
    positive_integer,
)
from ratatouille.models import (
    CookingCheck,
    Entry,
    Goals,
    Ingredient,
    NutritionColumns,
    Owned,
    Plan,
    Recipe,
    RecipeIngredient,
    RecipeVersion,
    Reminder,
    Revision,
    ShoppingCheck,
    User,
)

T = TypeVar("T", bound=Owned)


def _nutrition(row: NutritionColumns) -> Nutrition:
    return Nutrition(row.calories, row.protein, row.fat, row.carbs)


def _set_nutrition(row: NutritionColumns, nutrition: Nutrition) -> None:
    if not isinstance(nutrition, Nutrition):
        raise ValidationError("Некорректные КБЖУ.")
    row.calories = nutrition.calories
    row.protein = nutrition.protein
    row.fat = nutrition.fat
    row.carbs = nutrition.carbs


class Store:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @contextmanager
    def _session(self, *, write: bool = False) -> Iterator[Session]:
        with self.engine.connect() as connection:
            connection = connection.execution_options(sqlite_write=write)
            with connection.begin(), Session(bind=connection) as session:
                yield session
                session.flush()

    @staticmethod
    def _user(session: Session, owner: str) -> User:
        user = session.get(User, owner)
        if user is None:
            raise NotFoundError("Пользователь не найден.")
        return user

    @staticmethod
    def _owned(session: Session, model: type[T], owner: str, identifier: str) -> T:
        row = session.scalar(select(model).where(model.id == identifier, model.owner_id == owner))
        if row is None:
            raise NotFoundError("Объект не найден.")
        return row

    def create_user(self, telegram_id: int | None = None) -> str:
        if telegram_id is not None:
            positive_integer(telegram_id)
        with self._session(write=True) as session:
            if telegram_id is not None:
                existing = session.scalar(select(User).where(User.telegram_id == telegram_id))
                if existing is not None:
                    return existing.id
            row = User(telegram_id=telegram_id)
            session.add(row)
            session.flush()
            return row.id

    def create_ingredient(self, owner: str, name: str) -> str:
        name = nonempty(name)
        with self._session(write=True) as session:
            self._user(session, owner)
            row = Ingredient(owner_id=owner, name=name)
            session.add(row)
            session.flush()
            return row.id

    def rename_ingredient(self, owner: str, identifier: str, name: str) -> None:
        name = nonempty(name)
        with self._session(write=True) as session:
            self._owned(session, Ingredient, owner, identifier).name = name

    def _new_version(
        self,
        session: Session,
        owner: str,
        recipe: Recipe,
        data: RecipeInput,
    ) -> RecipeVersion:
        if not isinstance(data, RecipeInput):
            raise ValidationError("Некорректный рецепт.")
        ingredients = [
            self._owned(session, Ingredient, owner, item.ingredient_id) for item in data.ingredients
        ]
        number = 1
        if recipe.current_version_id is not None:
            number = (
                self._owned(session, RecipeVersion, owner, recipe.current_version_id).number + 1
            )
        version = RecipeVersion(
            owner_id=owner,
            recipe_id=recipe.id,
            number=number,
            name=data.name,
            instructions=data.instructions,
            yield_portions=data.yield_portions,
        )
        _set_nutrition(version, data.nutrition)
        session.add(version)
        session.flush()
        for position, (item, ingredient) in enumerate(
            zip(data.ingredients, ingredients, strict=True)
        ):
            session.add(
                RecipeIngredient(
                    owner_id=owner,
                    version_id=version.id,
                    ingredient_id=ingredient.id,
                    position=position,
                    name=ingredient.name,
                    quantity=item.quantity,
                    unit=item.unit,
                )
            )
        session.flush()
        version.sealed = True
        session.flush()
        recipe.current_version_id = version.id
        return version

    def create_recipe(self, owner: str, data: RecipeInput) -> RecipeSnapshot:
        with self._session(write=True) as session:
            self._user(session, owner)
            recipe = Recipe(owner_id=owner)
            session.add(recipe)
            session.flush()
            version = self._new_version(session, owner, recipe, data)
            return self._snapshot(session, owner, version.id)

    def edit_recipe(self, owner: str, recipe_id: str, data: RecipeInput) -> RecipeSnapshot:
        with self._session(write=True) as session:
            recipe = self._owned(session, Recipe, owner, recipe_id)
            version = self._new_version(session, owner, recipe, data)
            return self._snapshot(session, owner, version.id)

    def set_recipe_flags(
        self,
        owner: str,
        recipe_id: str,
        *,
        eligible: bool | None = None,
        archived: bool | None = None,
    ) -> None:
        if any(value is not None and type(value) is not bool for value in (eligible, archived)):
            raise ValidationError("Допуск и архив задаются логическими значениями.")
        with self._session(write=True) as session:
            recipe = self._owned(session, Recipe, owner, recipe_id)
            if eligible is not None:
                recipe.eligible = eligible
            if archived is not None:
                recipe.archived = archived

    def list_recipes(
        self, owner: str, *, eligible_only: bool = False
    ) -> tuple[RecipeSnapshot, ...]:
        with self._session() as session:
            self._user(session, owner)
            query = select(Recipe).where(Recipe.owner_id == owner, Recipe.archived.is_(False))
            if eligible_only:
                query = query.where(Recipe.eligible.is_(True))
            identifiers = [
                row.current_version_id
                for row in session.scalars(query.order_by(Recipe.id))
                if row.current_version_id is not None
            ]
            snapshots = self._snapshots(session, owner, set(identifiers))
            return tuple(snapshots[identifier] for identifier in identifiers)

    def _snapshot(self, session: Session, owner: str, version_id: str) -> RecipeSnapshot:
        return self._snapshots(session, owner, {version_id})[version_id]

    def _snapshots(
        self,
        session: Session,
        owner: str,
        version_ids: set[str],
    ) -> dict[str, RecipeSnapshot]:
        if not version_ids:
            return {}
        versions = list(
            session.scalars(
                select(RecipeVersion).where(
                    RecipeVersion.owner_id == owner,
                    RecipeVersion.id.in_(version_ids),
                    RecipeVersion.sealed.is_(True),
                )
            )
        )
        if {row.id for row in versions} != version_ids:
            raise NotFoundError("Версия рецепта не найдена.")
        ingredients: dict[str, list[IngredientSnapshot]] = defaultdict(list)
        for item in session.scalars(
            select(RecipeIngredient)
            .where(
                RecipeIngredient.owner_id == owner,
                RecipeIngredient.version_id.in_(version_ids),
            )
            .order_by(RecipeIngredient.version_id, RecipeIngredient.position)
        ):
            ingredients[item.version_id].append(
                IngredientSnapshot(
                    item.ingredient_id,
                    item.name,
                    item.quantity,
                    item.unit,
                )
            )
        return {
            row.id: RecipeSnapshot(
                row.id,
                row.recipe_id,
                row.name,
                row.instructions,
                row.yield_portions,
                _nutrition(row),
                tuple(ingredients[row.id]),
            )
            for row in versions
        }

    def save_goals(self, owner: str, goals: Nutrition) -> None:
        with self._session(write=True) as session:
            self._user(session, owner)
            row = session.get(Goals, owner)
            if row is None:
                row = Goals(owner_id=owner)
                session.add(row)
            _set_nutrition(row, goals)

    def get_goals(self, owner: str) -> Nutrition:
        with self._session() as session:
            self._user(session, owner)
            row = session.get(Goals, owner)
            return _nutrition(row) if row is not None else Nutrition()

    def save_schedule(self, owner: str, schedules: tuple[Schedule, ...]) -> None:
        if not isinstance(schedules, tuple) or any(not isinstance(s, Schedule) for s in schedules):
            raise ValidationError("Некорректное расписание.")
        keys = [(s.kind, s.weekday, s.at) for s in schedules]
        if len(set(keys)) != len(keys):
            raise ValidationError("Расписание содержит повторяющиеся события.")
        with self._session(write=True) as session:
            self._user(session, owner)
            session.execute(delete(Reminder).where(Reminder.owner_id == owner))
            session.add_all(
                Reminder(
                    owner_id=owner,
                    kind=s.kind,
                    weekday=s.weekday,
                    at=s.at,
                    enabled=s.enabled,
                    timezone=s.timezone,
                )
                for s in schedules
            )

    def get_schedule(self, owner: str) -> tuple[Schedule, ...]:
        with self._session() as session:
            self._user(session, owner)
            return tuple(
                Schedule(row.kind, row.weekday, row.at, row.enabled, row.timezone)
                for row in session.scalars(
                    select(Reminder)
                    .where(Reminder.owner_id == owner)
                    .order_by(Reminder.kind, Reminder.weekday, Reminder.at)
                )
            )

    def create_draft(
        self,
        owner: str,
        *,
        start_date: date | None = None,
        days: int = 7,
        mode: RepeatMode = RepeatMode.AB,
        repeat_limit: int | None = None,
        plan_id: str | None = None,
    ) -> PlanRevision:
        if not isinstance(mode, RepeatMode):
            raise ValidationError("Неизвестный режим повторов.")
        if mode == RepeatMode.AB and repeat_limit is not None:
            raise ValidationError("Лимит не применяется к A/B.")
        if mode == RepeatMode.LIMITED:
            repeat_limit = positive_integer(3 if repeat_limit is None else repeat_limit)
        with self._session(write=True) as session:
            self._user(session, owner)
            if plan_id is None:
                if type(days) is not int or days not in (7, 28) or type(start_date) is not date:
                    raise ValidationError("Новый план требует даты и длительности 7 или 28 дней.")
                plan = Plan(owner_id=owner, start_date=start_date, days=days)
                session.add(plan)
                session.flush()
            else:
                plan = self._owned(session, Plan, owner, plan_id)
                if start_date is not None:
                    raise ValidationError("Копия плана сохраняет исходную дату.")
            base = None
            if plan.current_revision_id is not None:
                base = self._owned(session, Revision, owner, plan.current_revision_id)
                mode, repeat_limit = base.mode, base.repeat_limit
            row = Revision(
                owner_id=owner,
                plan_id=plan.id,
                base_revision_id=plan.current_revision_id,
                state=RevisionState.DRAFT,
                mode=mode,
                repeat_limit=repeat_limit,
            )
            goals = session.get(Goals, owner)
            _set_nutrition(
                row, _nutrition(base) if base else _nutrition(goals) if goals else Nutrition()
            )
            session.add(row)
            session.flush()
            if base is not None:
                session.add_all(
                    Entry(
                        owner_id=owner,
                        revision_id=row.id,
                        day=item.day,
                        slot=item.slot,
                        version_id=item.version_id,
                    )
                    for item in session.scalars(
                        select(Entry).where(
                            Entry.revision_id == base.id,
                            Entry.owner_id == owner,
                        )
                    )
                )
                session.flush()
            return self._revision(session, owner, row.id)

    def _revision(self, session: Session, owner: str, revision_id: str) -> PlanRevision:
        row = self._owned(session, Revision, owner, revision_id)
        plan = self._owned(session, Plan, owner, row.plan_id)
        stored_entries = list(
            session.scalars(
                select(Entry)
                .where(
                    Entry.revision_id == row.id,
                    Entry.owner_id == owner,
                )
                .order_by(Entry.day, Entry.slot)
            )
        )
        snapshots = self._snapshots(session, owner, {item.version_id for item in stored_entries})
        entries = tuple(
            MenuEntry(item.day, item.slot, snapshots[item.version_id]) for item in stored_entries
        )
        return PlanRevision(
            row.id,
            plan.id,
            plan.start_date,
            plan.days,
            row.state,
            row.mode,
            row.repeat_limit,
            row.number,
            _nutrition(row),
            entries,
        )

    def get_revision(self, owner: str, revision_id: str) -> PlanRevision:
        with self._session() as session:
            return self._revision(session, owner, revision_id)

    def get_confirmed(self, owner: str, plan_id: str) -> PlanRevision | None:
        with self._session() as session:
            plan = self._owned(session, Plan, owner, plan_id)
            return (
                self._revision(session, owner, plan.current_revision_id)
                if plan.current_revision_id
                else None
            )

    def _advance(
        self,
        session: Session,
        owner: str,
        revision_id: str,
        expected_number: int,
        state: RevisionState = RevisionState.DRAFT,
    ) -> None:
        positive_integer(expected_number)
        result = session.scalar(
            update(Revision)
            .where(
                Revision.id == revision_id,
                Revision.owner_id == owner,
                Revision.state == RevisionState.DRAFT,
                Revision.number == expected_number,
            )
            .values(number=Revision.number + 1, state=state)
            .returning(Revision.id)
        )
        if result is None:
            raise ConflictError("Черновик изменён или уже завершён; перечитайте его.")

    def set_entry(
        self,
        owner: str,
        revision_id: str,
        *,
        day: int,
        slot: Slot,
        version_id: str | None,
        expected_number: int,
    ) -> PlanRevision:
        if type(day) is not int or not isinstance(slot, Slot):
            raise ValidationError("Некорректный день или слот.")
        with self._session(write=True) as session:
            row = self._owned(session, Revision, owner, revision_id)
            plan = self._owned(session, Plan, owner, row.plan_id)
            if not 0 <= day < plan.days:
                raise ValidationError("День выходит за длительность плана.")
            if version_id is not None:
                version = self._owned(session, RecipeVersion, owner, version_id)
                recipe = self._owned(session, Recipe, owner, version.recipe_id)
                if recipe.archived:
                    raise ValidationError("Архивный рецепт нельзя добавить в меню.")
            self._advance(session, owner, row.id, expected_number)
            session.execute(
                delete(Entry).where(
                    Entry.revision_id == row.id,
                    Entry.owner_id == owner,
                    Entry.day == day,
                    Entry.slot == slot,
                )
            )
            if version_id is not None:
                session.add(
                    Entry(
                        owner_id=owner,
                        revision_id=row.id,
                        day=day,
                        slot=slot,
                        version_id=version_id,
                    )
                )
            session.flush()
            return self._revision(session, owner, row.id)

    def confirm(self, owner: str, revision_id: str, *, expected_number: int) -> PlanRevision:
        positive_integer(expected_number)
        with self._session(write=True) as session:
            row = self._owned(session, Revision, owner, revision_id)
            if row.state == RevisionState.CONFIRMED:
                if row.number != expected_number + 1:
                    raise ConflictError("Номер подтверждения не соответствует сохранённой ревизии.")
                return self._revision(session, owner, row.id)
            plan = self._owned(session, Plan, owner, row.plan_id)
            if plan.current_revision_id != row.base_revision_id:
                raise ConflictError("За время редактирования подтверждён другой черновик.")
            self._advance(session, owner, row.id, expected_number, RevisionState.CONFIRMED)
            plan.current_revision_id = row.id
            session.flush()
            return self._revision(session, owner, row.id)

    def cancel(self, owner: str, revision_id: str, *, expected_number: int) -> PlanRevision:
        positive_integer(expected_number)
        with self._session(write=True) as session:
            row = self._owned(session, Revision, owner, revision_id)
            if row.state == RevisionState.CANCELLED and row.number == expected_number + 1:
                return self._revision(session, owner, row.id)
            self._advance(session, owner, row.id, expected_number, RevisionState.CANCELLED)
            return self._revision(session, owner, row.id)

    def calculate(self, owner: str, revision_id: str) -> MenuCalculation:
        return calculate_menu(self.get_revision(owner, revision_id))

    def mark_shopping(
        self,
        owner: str,
        revision_id: str,
        *,
        week: int,
        ingredient_id: str,
        unit: Unit,
        checked: bool,
    ) -> None:
        if type(checked) is not bool or type(week) is not int or not isinstance(unit, Unit):
            raise ValidationError("Некорректная отметка покупки.")
        with self._session(write=True) as session:
            revision = self._revision(session, owner, revision_id)
            if revision.state != RevisionState.CONFIRMED:
                raise ConflictError("Отметки доступны после подтверждения меню.")
            if not any(
                item.week == week and item.ingredient_id == ingredient_id and item.unit == unit
                for item in calculate_menu(revision).shopping
            ):
                raise NotFoundError("Строка покупки не найдена.")
            row = session.scalar(
                select(ShoppingCheck).where(
                    ShoppingCheck.owner_id == owner,
                    ShoppingCheck.revision_id == revision_id,
                    ShoppingCheck.week == week,
                    ShoppingCheck.ingredient_id == ingredient_id,
                    ShoppingCheck.unit == unit,
                )
            )
            if row is None:
                row = ShoppingCheck(
                    owner_id=owner,
                    revision_id=revision_id,
                    week=week,
                    ingredient_id=ingredient_id,
                    unit=unit,
                )
                session.add(row)
            row.checked = checked

    def mark_cooking(
        self,
        owner: str,
        revision_id: str,
        *,
        week: int,
        version_id: str,
        checked: bool,
    ) -> None:
        if type(checked) is not bool or type(week) is not int:
            raise ValidationError("Некорректная отметка готовки.")
        with self._session(write=True) as session:
            revision = self._revision(session, owner, revision_id)
            if revision.state != RevisionState.CONFIRMED:
                raise ConflictError("Отметки доступны после подтверждения меню.")
            if not any(
                item.week == week and item.version_id == version_id
                for item in calculate_menu(revision).cooking
            ):
                raise NotFoundError("Партия готовки не найдена.")
            row = session.scalar(
                select(CookingCheck).where(
                    CookingCheck.owner_id == owner,
                    CookingCheck.revision_id == revision_id,
                    CookingCheck.week == week,
                    CookingCheck.version_id == version_id,
                )
            )
            if row is None:
                row = CookingCheck(
                    owner_id=owner, revision_id=revision_id, week=week, version_id=version_id
                )
                session.add(row)
            row.checked = checked

    def get_checks(
        self,
        owner: str,
        revision_id: str,
    ) -> tuple[dict[tuple[int, str, Unit], bool], dict[tuple[int, str], bool]]:
        with self._session() as session:
            self._owned(session, Revision, owner, revision_id)
            shopping = {
                (row.week, row.ingredient_id, row.unit): row.checked
                for row in session.scalars(
                    select(ShoppingCheck).where(
                        ShoppingCheck.owner_id == owner,
                        ShoppingCheck.revision_id == revision_id,
                    )
                )
            }
            cooking = {
                (row.week, row.version_id): row.checked
                for row in session.scalars(
                    select(CookingCheck).where(
                        CookingCheck.owner_id == owner,
                        CookingCheck.revision_id == revision_id,
                    )
                )
            }
            return shopping, cooking
