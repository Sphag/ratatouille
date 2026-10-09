"""Manual menu contracts and atomic draft operations; no automatic selection."""

from collections import Counter
from datetime import date, timedelta

from pydantic import Field, field_validator, model_validator
from sqlalchemy import delete, select

from ratatouille.calculations import NUTRITION_FIELDS, calculate_menu
from ratatouille.catalog import Contract, NutritionFields, StoredNutrition
from ratatouille.domain import (
    ConflictError,
    Nutrition,
    PlanRevision,
    RepeatMode,
    RevisionState,
    Slot,
    Unit,
    ValidationError,
    positive_integer,
)
from ratatouille.models import Entry, Plan, Recipe, RecipeVersion, Revision
from ratatouille.storage import Store, _set_nutrition


class Settings(Contract):
    mode: RepeatMode = Field(default=RepeatMode.AB, strict=False)
    # Safe in both SQLite INTEGER and the browser's JSON number representation.
    repeat_limit: int | None = Field(default=None, gt=0, le=2**53 - 1)
    targets: NutritionFields

    @model_validator(mode="after")
    def validate_limit(self) -> "Settings":
        if self.mode == RepeatMode.AB and self.repeat_limit is not None:
            raise ValueError("Лимит не применяется к A/B.")
        return self


class CreateMenu(Settings):
    start_date: date = Field(strict=False)
    days: int = Field(default=7)

    @field_validator("start_date", mode="before")
    @classmethod
    def calendar_date(cls, value: object) -> object:
        if type(value) is not date and not (
            isinstance(value, str) and len(value) == 10 and value[4] == "-" and value[7] == "-"
        ):
            raise ValueError("Дата должна иметь вид ГГГГ-ММ-ДД.")
        return value

    @model_validator(mode="after")
    def validate_dates(self) -> "CreateMenu":
        if self.days not in (7, 28) or (date.max - self.start_date).days < self.days - 1:
            raise ValueError("План должен содержать 7 или 28 последовательных дат.")
        return self


class Expected(Contract):
    expected_number: int = Field(gt=0, le=2**53 - 1)


class ConfigureMenu(Settings, Expected):
    pass


class Position(Contract):
    day: int = Field(ge=0, lt=28)
    slot: Slot = Field(strict=False)
    version_id: str | None


class SetPositions(Expected):
    positions: list[Position] = Field(min_length=1, max_length=112)

    @model_validator(mode="after")
    def unique_positions(self) -> "SetPositions":
        if len({(p.day, p.slot) for p in self.positions}) != len(self.positions):
            raise ValueError("Слот указан дважды.")
        return self


class MenuEntryView(Contract):
    day: int
    slot: Slot
    version_id: str
    recipe_id: str
    name: str


class DayView(Contract):
    day: int
    date: date
    total: StoredNutrition
    difference: StoredNutrition  # Signed decimal strings, unlike input NutritionFields.


class ShoppingView(Contract):
    week: int
    ingredient_id: str
    name: str
    unit: Unit
    quantity: str


class MenuView(Contract):
    id: str
    plan_id: str
    start_date: date
    days: int
    state: RevisionState
    mode: RepeatMode
    repeat_limit: int | None
    number: int
    targets: StoredNutrition
    entries: list[MenuEntryView]
    totals: list[DayView]
    problems: list[str]
    shopping: list[ShoppingView]


class PlanSummary(Contract):
    id: str
    start_date: date
    days: int
    confirmed_id: str | None
    drafts: list[str]


def stored_nutrition(value: Nutrition) -> StoredNutrition:
    return StoredNutrition(**{f: format(getattr(value, f), "f") for f in NUTRITION_FIELDS})


def problems(revision: PlanRevision) -> list[str]:
    entries = {(e.day, e.slot): e.recipe.version_id for e in revision.entries}
    missing = sum(
        (day, slot) not in entries
        for day in range(revision.days)
        for slot in (Slot.BREAKFAST, Slot.SECOND, Slot.DINNER)
    )
    result = [f"Заполните завтрак, второе и ужин: осталось {missing} слотов."] if missing else []
    for week in range(revision.days // 7):
        if revision.mode == RepeatMode.AB:
            for offsets in ((0, 2, 4, 6), (1, 3, 5)):
                for slot in Slot:
                    if len({entries.get((week * 7 + d, slot)) for d in offsets}) > 1:
                        result.append(f"Неделя {week + 1}: приведите дни A/B к одинаковым меню.")
                        break
        else:
            counts = Counter(e.recipe.recipe_id for e in revision.entries if e.day // 7 == week)
            exceeded = {
                key for key, count in counts.items() if count > (revision.repeat_limit or 3)
            }
            names = sorted(
                {e.recipe.name for e in revision.entries if e.recipe.recipe_id in exceeded}
            )
            if names:
                result.append(f"Неделя {week + 1}: превышен лимит повторов — {', '.join(names)}.")
    return result


def view(revision: PlanRevision) -> MenuView:
    calculation = calculate_menu(revision)
    return MenuView(
        id=revision.id,
        plan_id=revision.plan_id,
        start_date=revision.start_date,
        days=revision.days,
        state=revision.state,
        mode=revision.mode,
        repeat_limit=revision.repeat_limit,
        number=revision.number,
        targets=stored_nutrition(revision.targets),
        entries=[
            MenuEntryView(
                day=e.day,
                slot=e.slot,
                version_id=e.recipe.version_id,
                recipe_id=e.recipe.recipe_id,
                name=e.recipe.name,
            )
            for e in revision.entries
        ],
        totals=[
            DayView(
                day=d.day,
                date=revision.start_date + timedelta(days=d.day),
                total=stored_nutrition(d.total),
                difference=StoredNutrition(
                    **dict(
                        zip(NUTRITION_FIELDS, (format(v, "f") for v in d.difference), strict=True)
                    )
                ),
            )
            for d in calculation.days
        ],
        problems=problems(revision),
        shopping=[
            ShoppingView(
                week=s.week,
                ingredient_id=s.ingredient_id,
                name=s.name,
                unit=s.unit,
                quantity=format(s.quantity, "f"),
            )
            for s in calculation.shopping
        ],
    )


class Menus:
    def __init__(self, store: Store) -> None:
        self.store = store

    def list_plans(self, owner: str) -> list[PlanSummary]:
        with self.store._session() as session:
            self.store._user(session, owner)
            drafts: dict[str, list[str]] = {}
            for row in session.scalars(
                select(Revision)
                .where(Revision.owner_id == owner, Revision.state == RevisionState.DRAFT)
                .order_by(Revision.id)
            ):
                drafts.setdefault(row.plan_id, []).append(row.id)
            return [
                PlanSummary(
                    id=p.id,
                    start_date=p.start_date,
                    days=p.days,
                    confirmed_id=p.current_revision_id,
                    drafts=drafts.get(p.id, []),
                )
                for p in session.scalars(
                    select(Plan)
                    .where(Plan.owner_id == owner)
                    .order_by(Plan.start_date.desc(), Plan.id)
                )
                if p.current_revision_id or p.id in drafts
            ]

    def create(self, owner: str, data: CreateMenu) -> MenuView:
        # Creation and its target snapshot commit together.
        with self.store._session(write=True) as session:
            self.store._user(session, owner)
            plan = Plan(owner_id=owner, start_date=data.start_date, days=data.days)
            session.add(plan)
            session.flush()
            row = Revision(
                owner_id=owner,
                plan_id=plan.id,
                state=RevisionState.DRAFT,
                mode=data.mode,
                repeat_limit=(data.repeat_limit or 3) if data.mode == RepeatMode.LIMITED else None,
            )
            _set_nutrition(row, data.targets.domain())
            session.add(row)
            session.flush()
            return view(self.store._revision(session, owner, row.id))

    def configure(self, owner: str, identifier: str, data: ConfigureMenu) -> MenuView:
        with self.store._session(write=True) as session:
            row = self.store._owned(session, Revision, owner, identifier)
            self.store._advance(session, owner, identifier, data.expected_number)
            row.mode = data.mode
            row.repeat_limit = (data.repeat_limit or 3) if data.mode == RepeatMode.LIMITED else None
            _set_nutrition(row, data.targets.domain())
            session.flush()
            return view(self.store._revision(session, owner, identifier))

    def set_positions(self, owner: str, identifier: str, data: SetPositions) -> MenuView:
        with self.store._session(write=True) as session:
            row = self.store._owned(session, Revision, owner, identifier)
            plan = self.store._owned(session, Plan, owner, row.plan_id)
            for position in data.positions:
                if position.day >= plan.days:
                    raise ValidationError("День выходит за длительность плана.")
                if position.version_id is not None:
                    version = self.store._owned(session, RecipeVersion, owner, position.version_id)
                    recipe = self.store._owned(session, Recipe, owner, version.recipe_id)
                    if not version.sealed or recipe.archived:
                        raise ValidationError("Архивный или незавершённый рецепт нельзя добавить.")
            self.store._advance(session, owner, identifier, data.expected_number)
            for position in data.positions:
                session.execute(
                    delete(Entry).where(
                        Entry.owner_id == owner,
                        Entry.revision_id == identifier,
                        Entry.day == position.day,
                        Entry.slot == position.slot,
                    )
                )
                if position.version_id is not None:
                    session.add(
                        Entry(
                            owner_id=owner,
                            revision_id=identifier,
                            day=position.day,
                            slot=position.slot,
                            version_id=position.version_id,
                        )
                    )
            session.flush()
            return view(self.store._revision(session, owner, identifier))

    def confirm(self, owner: str, identifier: str, expected: int) -> MenuView:
        positive_integer(expected)
        with self.store._session(write=True) as session:
            row = self.store._owned(session, Revision, owner, identifier)
            if row.state == RevisionState.CONFIRMED:
                if row.number != expected + 1:
                    raise ConflictError("Номер подтверждения не соответствует сохранённому меню.")
                return view(self.store._revision(session, owner, identifier))
            plan = self.store._owned(session, Plan, owner, row.plan_id)
            if plan.current_revision_id != row.base_revision_id:
                raise ConflictError("Уже подтверждён другой черновик. Откройте сохранённое меню.")
            self.store._advance(session, owner, identifier, expected)
            current = self.store._revision(session, owner, identifier)
            invalid = problems(current)
            if invalid:
                raise ValidationError(" ".join(invalid))
            row.state = RevisionState.CONFIRMED
            session.flush()
            plan.current_revision_id = identifier
            session.flush()
            return view(self.store._revision(session, owner, identifier))
