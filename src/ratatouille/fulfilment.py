"""Exact weekly shopping and cooking with revision-bound optimistic checklists."""

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ratatouille.calculations import calculate_menu
from ratatouille.catalog import Contract
from ratatouille.domain import ConflictError, NotFoundError, RevisionState, Unit
from ratatouille.menus import ShoppingView
from ratatouille.models import CookingCheck, ShoppingCheck
from ratatouille.storage import Store


class ShoppingLine(ShoppingView):
    checked: bool


class Batch(Contract):
    week: int
    version_id: str
    name: str
    portions: int
    ingredients: list[ShoppingView]
    checked: bool


class FulfilmentView(Contract):
    revision_id: str
    state: RevisionState
    shopping: list[ShoppingLine]
    cooking: list[Batch]


class CheckRequest(Contract):
    week: int = Field(ge=0, lt=4)
    checked: bool
    expected_checked: bool


class ShoppingRequest(CheckRequest):
    ingredient_id: str
    unit: Unit = Field(strict=False)


class CookingRequest(CheckRequest):
    version_id: str


class Fulfilment:
    def __init__(self, store: Store) -> None:
        self.store = store

    def _view(self, session: Session, owner: str, identifier: str) -> FulfilmentView:
        revision = self.store._revision(session, owner, identifier)
        calculation = calculate_menu(revision)
        shopping = {
            (r.week, r.ingredient_id, r.unit): r.checked
            for r in session.scalars(
                select(ShoppingCheck).where(
                    ShoppingCheck.owner_id == owner, ShoppingCheck.revision_id == identifier
                )
            )
        }
        cooking = {
            (r.week, r.version_id): r.checked
            for r in session.scalars(
                select(CookingCheck).where(
                    CookingCheck.owner_id == owner, CookingCheck.revision_id == identifier
                )
            )
        }
        return FulfilmentView(
            revision_id=identifier,
            state=revision.state,
            shopping=[
                ShoppingLine(
                    week=s.week,
                    ingredient_id=s.ingredient_id,
                    name=s.name,
                    unit=s.unit,
                    quantity=format(s.quantity, "f"),
                    checked=shopping.get((s.week, s.ingredient_id, s.unit), False),
                )
                for s in calculation.shopping
            ],
            cooking=[
                Batch(
                    week=b.week,
                    version_id=b.version_id,
                    name=b.name,
                    portions=b.portions,
                    ingredients=[
                        ShoppingView(
                            week=s.week,
                            ingredient_id=s.ingredient_id,
                            name=s.name,
                            unit=s.unit,
                            quantity=format(s.quantity, "f"),
                        )
                        for s in b.ingredients
                    ],
                    checked=cooking.get((b.week, b.version_id), False),
                )
                for b in calculation.cooking
            ],
        )

    def get(self, owner: str, identifier: str) -> FulfilmentView:
        with self.store._session() as session:
            return self._view(session, owner, identifier)

    def mark(
        self, owner: str, identifier: str, data: ShoppingRequest | CookingRequest
    ) -> FulfilmentView:
        with self.store._session(write=True) as session:
            before = self._view(session, owner, identifier)
            if before.state != RevisionState.CONFIRMED:
                raise ConflictError("Отметки доступны после подтверждения меню.")
            if isinstance(data, ShoppingRequest):
                item = next(
                    (
                        s
                        for s in before.shopping
                        if (s.week, s.ingredient_id, s.unit)
                        == (data.week, data.ingredient_id, data.unit)
                    ),
                    None,
                )
                if item is None:
                    raise NotFoundError("Строка покупки не найдена.")
                if item.checked != data.expected_checked:
                    raise ConflictError("Отметка изменена в другой вкладке. Перечитайте список.")
                row = session.scalar(
                    select(ShoppingCheck).where(
                        ShoppingCheck.owner_id == owner,
                        ShoppingCheck.revision_id == identifier,
                        ShoppingCheck.week == data.week,
                        ShoppingCheck.ingredient_id == data.ingredient_id,
                        ShoppingCheck.unit == data.unit,
                    )
                )
                if row is None:
                    row = ShoppingCheck(
                        owner_id=owner,
                        revision_id=identifier,
                        week=data.week,
                        ingredient_id=data.ingredient_id,
                        unit=data.unit,
                    )
                    session.add(row)
                row.checked = data.checked
            else:
                batch = next(
                    (
                        b
                        for b in before.cooking
                        if (b.week, b.version_id) == (data.week, data.version_id)
                    ),
                    None,
                )
                if batch is None:
                    raise NotFoundError("Партия готовки не найдена.")
                if batch.checked != data.expected_checked:
                    raise ConflictError("Отметка изменена в другой вкладке. Перечитайте список.")
                cooking_row = session.scalar(
                    select(CookingCheck).where(
                        CookingCheck.owner_id == owner,
                        CookingCheck.revision_id == identifier,
                        CookingCheck.week == data.week,
                        CookingCheck.version_id == data.version_id,
                    )
                )
                if cooking_row is None:
                    cooking_row = CookingCheck(
                        owner_id=owner,
                        revision_id=identifier,
                        week=data.week,
                        version_id=data.version_id,
                    )
                    session.add(cooking_row)
                cooking_row.checked = data.checked
            session.flush()
            return self._view(session, owner, identifier)
