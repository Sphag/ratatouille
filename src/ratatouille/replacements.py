"""Owner-bound replacement previews with fixed selected positions and exact shopping."""

from collections import Counter
from dataclasses import replace
from enum import StrEnum
from fractions import Fraction
from hashlib import sha256

from pydantic import Field
from sqlalchemy import delete
from sqlalchemy.orm import Session

from ratatouille.calculations import calculate_menu
from ratatouille.domain import (
    ConflictError,
    MenuEntry,
    PlanRevision,
    RecipeSnapshot,
    RepeatMode,
    Slot,
    ValidationError,
)
from ratatouille.generation import MAX_CANDIDATES, SLOTS, distance, improve, vector
from ratatouille.menus import Expected, Menus, MenuView, Position, problems, view
from ratatouille.models import Entry, Recipe
from ratatouille.proposals import Proposals, digest


class Scenario(StrEnum):
    KEEP = "keep"
    REBALANCE = "rebalance"


class ReplacementLocation(Expected):
    day: int = Field(ge=0, lt=28)
    slot: Slot = Field(strict=False)


class ReplacementRequest(ReplacementLocation):
    version_id: str
    scenario: Scenario = Field(default=Scenario.KEEP, strict=False)
    automatic: bool = False


class ApplyReplacement(ReplacementRequest):
    digest: str


class ReplacementPreview(Expected):
    menu: MenuView
    changes: list[Position]
    digest: str


def affected_days(revision: PlanRevision, day: int) -> tuple[int, ...]:
    if day >= revision.days:
        raise ValidationError("День выходит за длительность плана.")
    week = day // 7
    return (
        tuple(week * 7 + d for d in range(7) if d % 2 == (day % 7) % 2)
        if revision.mode == RepeatMode.AB
        else (day,)
    )


def replace_selected(
    revision: PlanRevision, data: ReplacementRequest, selected: RecipeSnapshot
) -> PlanRevision:
    days = affected_days(revision, data.day)
    entries = [e for e in revision.entries if not (e.day in days and e.slot == data.slot)]
    entries.extend(MenuEntry(day, data.slot, selected) for day in days)
    return replace(revision, entries=tuple(sorted(entries, key=lambda e: (e.day, e.slot.value))))


def rebalance(
    revision: PlanRevision, data: ReplacementRequest, allowed: tuple[RecipeSnapshot, ...]
) -> PlanRevision:
    week_number = data.day // 7
    current = [e for e in revision.entries if e.day // 7 == week_number]
    allowed_ids = {r.version_id for r in allowed}
    if any(e.recipe.version_id not in allowed_ids for e in current):
        raise ValidationError(
            "Для подбора остальных блюд нужны текущие активные версии с явным допуском. "
            "Проверьте библиотеку или оставьте остальные блюда."
        )
    target = vector(revision.targets)
    scale = tuple(max(t, Fraction(1)) for t in target)
    required = {e.recipe.version_id for e in current}
    ranked = sorted(
        allowed,
        key=lambda r: (
            distance(vector(r.nutrition), tuple(t / 3 for t in target), scale),
            r.recipe_id,
        ),
    )
    pool = sorted(
        [r for r in allowed if r.version_id in required]
        + [r for r in ranked if r.version_id not in required][: MAX_CANDIDATES - len(required)],
        key=lambda r: r.recipe_id,
    )
    index = {r.version_id: i for i, r in enumerate(pool)}
    values = [vector(r.nutrition) for r in pool]
    weights = [4, 3] if revision.mode == RepeatMode.AB else [1] * 7
    rows: list[list[int | None]] = [[None] * 4 for _ in weights]
    for e in current:
        day = (e.day % 7) % 2 if revision.mode == RepeatMode.AB else e.day % 7
        rows[day][SLOTS.index(e.slot)] = index[e.recipe.version_id]
    neighbors = Counter(
        e.recipe.recipe_id for e in revision.entries if abs(e.day // 7 - week_number) == 1
    )
    previous = Counter({i: neighbors[r.recipe_id] for i, r in enumerate(pool)})
    locked_day = (data.day % 7) % 2 if revision.mode == RepeatMode.AB else data.day % 7
    locks = {(locked_day, SLOTS.index(data.slot))}
    if revision.mode == RepeatMode.LIMITED:
        used = Counter(r for row in rows for r in row if r is not None)
        limit = revision.repeat_limit or 3
        for day, row in enumerate(rows):
            for slot, recipe in enumerate(row):
                if recipe is None or used[recipe] <= limit or (day, slot) in locks:
                    continue
                options = [r for r in range(len(pool)) if used[r] < limit]
                if not options and slot != 1:
                    raise ValidationError(
                        "Недостаточно допущенных рецептов для замены с этим лимитом."
                    )
                new = (
                    min(
                        options,
                        key=lambda r: (distance(values[r], tuple(t / 3 for t in target), scale), r),
                    )
                    if options
                    else None
                )
                used[recipe] -= 1
                if new is not None:
                    used[new] += 1
                row[slot] = new
    chosen = improve(
        rows,
        weights,
        values,
        target,
        scale,
        revision.repeat_limit if revision.mode == RepeatMode.LIMITED else None,
        previous,
        locked=locks,
    )
    entries = [e for e in revision.entries if e.day // 7 != week_number]
    for day in range(7):
        row = chosen[day % 2] if revision.mode == RepeatMode.AB else chosen[day]
        entries.extend(
            MenuEntry(week_number * 7 + day, slot, pool[r])
            for slot, r in zip(SLOTS, row, strict=True)
            if r is not None
        )
    return replace(revision, entries=tuple(sorted(entries, key=lambda e: (e.day, e.slot.value))))


class Replacements:
    def __init__(self, menus: Menus) -> None:
        self.menus = menus
        self.store = menus.store
        self.proposals = Proposals(menus)

    def _selected(self, session: Session, owner: str, version_id: str) -> RecipeSnapshot:
        snapshots = self.store._snapshots(session, owner, {version_id})
        selected = snapshots[version_id]
        recipe = self.store._owned(session, Recipe, owner, selected.recipe_id)
        if recipe.archived or recipe.current_version_id != version_id:
            raise ConflictError("Рецепт изменён или архивирован. Перечитайте библиотеку.")
        return selected

    def _build(
        self,
        revision: PlanRevision,
        data: ReplacementRequest,
        selected: RecipeSnapshot,
        allowed: tuple[RecipeSnapshot, ...],
    ) -> ReplacementPreview:
        if (data.automatic or data.scenario == Scenario.REBALANCE) and selected.version_id not in {
            r.version_id for r in allowed
        }:
            raise ConflictError(
                "Для автоматической замены нужен актуальный явный допуск выбранного рецепта."
            )
        proposed = replace_selected(revision, data, selected)
        if data.scenario == Scenario.REBALANCE:
            proposed = rebalance(proposed, data, allowed)
        invalid = problems(proposed)
        if invalid:
            raise ValidationError("Замена нарушает заполнение или режим: " + " ".join(invalid))
        before = {(e.day, e.slot): e.recipe.version_id for e in revision.entries}
        after = {(e.day, e.slot): e.recipe.version_id for e in proposed.entries}
        changes = [
            Position(day=d, slot=s, version_id=after.get((d, s)))
            for d, s in sorted(before.keys() | after.keys(), key=lambda p: (p[0], p[1].value))
            if before.get((d, s)) != after.get((d, s))
        ]
        positions = [
            Position(day=e.day, slot=e.slot, version_id=e.recipe.version_id)
            for e in proposed.entries
        ]
        checksum = sha256(
            (digest(revision, positions) + data.model_dump_json()).encode()
        ).hexdigest()
        return ReplacementPreview(
            expected_number=revision.number, menu=view(proposed), changes=changes, digest=checksum
        )

    def preview(self, owner: str, identifier: str, data: ReplacementRequest) -> ReplacementPreview:
        with self.store._session() as session:
            revision = self.store._revision(session, owner, identifier)
            self.proposals._expected(revision, data)
            selected = self._selected(session, owner, data.version_id)
            allowed = self.proposals._allowed(session, owner)
        return self._build(revision, data, selected, allowed)

    def suggestions(self, owner: str, identifier: str, data: ReplacementLocation) -> list[MenuView]:
        with self.store._session() as session:
            revision = self.store._revision(session, owner, identifier)
            self.proposals._expected(revision, data)
            allowed = self.proposals._allowed(session, owner)
        affected_days(revision, data.day)
        current = next(
            (
                e.recipe.version_id
                for e in revision.entries
                if e.day == data.day and e.slot == data.slot
            ),
            None,
        )
        options: list[tuple[tuple[Fraction, str], MenuView]] = []
        for recipe in allowed:
            if recipe.version_id == current:
                continue
            request = ReplacementRequest(**data.model_dump(), version_id=recipe.version_id)
            proposed = replace_selected(revision, request, recipe)
            if not problems(proposed):
                target = vector(proposed.targets)
                scale = tuple(max(t, Fraction(1)) for t in target)
                objective = sum(
                    (
                        distance(vector(d.total), target, scale)
                        for d in calculate_menu(proposed).days
                    ),
                    Fraction(),
                )
                options.append(((objective, recipe.recipe_id), view(proposed)))
        options.sort(key=lambda item: item[0])
        return [menu for _, menu in options[:5]]

    def apply(self, owner: str, identifier: str, data: ApplyReplacement) -> MenuView:
        request = ReplacementRequest(**data.model_dump(exclude={"digest"}))
        with self.store._session(write=True) as session:
            revision = self.store._revision(session, owner, identifier)
            self.proposals._expected(revision, request)
            selected = self._selected(session, owner, request.version_id)
            allowed = self.proposals._allowed(session, owner)
            proposal = self._build(revision, request, selected, allowed)
            if data.digest != proposal.digest:
                raise ConflictError("Предложение изменилось. Повторите просмотр замены.")
            self.store._advance(session, owner, identifier, request.expected_number)
            session.execute(
                delete(Entry).where(Entry.owner_id == owner, Entry.revision_id == identifier)
            )
            session.add_all(
                Entry(
                    owner_id=owner,
                    revision_id=identifier,
                    day=e.day,
                    slot=e.slot,
                    version_id=e.version_id,
                )
                for e in proposal.menu.entries
            )
            session.flush()
            return view(self.store._revision(session, owner, identifier))
