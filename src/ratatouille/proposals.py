"""Preview without writes, then atomic application with fresh recipe admission checks."""

import json
from dataclasses import replace
from hashlib import sha256

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ratatouille.catalog import Contract
from ratatouille.domain import (
    ConflictError,
    MenuEntry,
    PlanRevision,
    RecipeSnapshot,
    RevisionState,
    ValidationError,
)
from ratatouille.generation import generate
from ratatouille.menus import (
    Expected,
    Menus,
    MenuView,
    Position,
    SetPositions,
    problems,
    view,
)
from ratatouille.models import Entry, Recipe


class Proposal(Contract):
    menu: MenuView
    positions: list[Position]
    digest: str


class ApplyProposal(SetPositions):
    digest: str


def digest(revision: PlanRevision, positions: list[Position]) -> str:
    payload = {
        "id": revision.id,
        "number": revision.number,
        "positions": [
            p.model_dump(mode="json")
            for p in sorted(positions, key=lambda p: (p.day, p.slot.value))
        ],
    }
    return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class Proposals:
    def __init__(self, menus: Menus) -> None:
        self.menus = menus
        self.store = menus.store

    @staticmethod
    def _expected(revision: PlanRevision, data: Expected) -> None:
        if revision.state != RevisionState.DRAFT or revision.number != data.expected_number:
            raise ConflictError(
                "Черновик изменён или завершён. Перечитайте его и повторите подбор."
            )

    def _allowed(self, session: Session, owner: str) -> tuple[RecipeSnapshot, ...]:
        identifiers = {
            r.current_version_id
            for r in session.scalars(
                select(Recipe).where(
                    Recipe.owner_id == owner, Recipe.eligible.is_(True), Recipe.archived.is_(False)
                )
            )
            if r.current_version_id is not None
        }
        return tuple(self.store._snapshots(session, owner, identifiers).values())

    def preview(self, owner: str, identifier: str, data: Expected) -> Proposal:
        # Release the database transaction before CPU work; apply rechecks the complete source.
        with self.store._session() as session:
            revision = self.store._revision(session, owner, identifier)
            self._expected(revision, data)
            allowed = self._allowed(session, owner)
        generated = generate(revision, allowed)
        invalid = problems(generated)
        if invalid:
            raise ValidationError(" ".join(invalid))
        positions = [
            Position(day=e.day, slot=e.slot, version_id=e.recipe.version_id)
            for e in generated.entries
        ]
        return Proposal(
            menu=view(generated), positions=positions, digest=digest(revision, positions)
        )

    def apply(self, owner: str, identifier: str, data: ApplyProposal) -> MenuView:
        with self.store._session(write=True) as session:
            revision = self.store._revision(session, owner, identifier)
            self._expected(revision, data)
            if data.digest != digest(revision, data.positions):
                raise ConflictError("Предложение изменилось. Повторите подбор перед применением.")
            allowed = {r.version_id: r for r in self._allowed(session, owner)}
            entries: list[MenuEntry] = []
            for position in data.positions:
                if position.version_id is None or position.version_id not in allowed:
                    raise ConflictError(
                        "Допуск, архив или версия выбранного рецепта изменились. "
                        "Проверьте библиотеку и повторите подбор."
                    )
                if position.day >= revision.days:
                    raise ValidationError("День выходит за длительность плана.")
                entries.append(MenuEntry(position.day, position.slot, allowed[position.version_id]))
            proposed = replace(revision, entries=tuple(entries))
            invalid = problems(proposed)
            if invalid:
                raise ValidationError(" ".join(invalid))
            self.store._advance(session, owner, identifier, data.expected_number)
            session.execute(
                delete(Entry).where(Entry.owner_id == owner, Entry.revision_id == identifier)
            )
            session.add_all(
                Entry(
                    owner_id=owner,
                    revision_id=identifier,
                    day=e.day,
                    slot=e.slot,
                    version_id=e.recipe.version_id,
                )
                for e in entries
            )
            session.flush()
            return view(self.store._revision(session, owner, identifier))
