"""Deterministic bounded menu search with exact scores and strict weekly capacities."""

from collections import Counter
from dataclasses import replace
from fractions import Fraction

from ratatouille.calculations import NUTRITION_FIELDS
from ratatouille.domain import (
    MenuEntry,
    Nutrition,
    PlanRevision,
    RecipeSnapshot,
    RepeatMode,
    Slot,
    ValidationError,
)

type Vector = tuple[Fraction, ...]
type Week = list[list[int | None]]
type Score = tuple[Fraction, int]

SLOTS = (Slot.BREAKFAST, Slot.FIRST, Slot.SECOND, Slot.DINNER)
REQUIRED = (0, 2, 3)
MAX_CANDIDATES = 64
STARTS = 3
IMPROVEMENT_ROUNDS = 6


def vector(nutrition: Nutrition) -> Vector:
    return tuple(Fraction(getattr(nutrition, key)) for key in NUTRITION_FIELDS)


def distance(actual: Vector, target: Vector, scale: Vector) -> Fraction:
    return sum((abs(a - t) / s for a, t, s in zip(actual, target, scale, strict=True)), Fraction())


def counts(week: Week, weights: list[int]) -> Counter[int]:
    result: Counter[int] = Counter()
    for row, weight in zip(week, weights, strict=True):
        for recipe in row:
            if recipe is not None:
                result[recipe] += weight
    return result


def total(row: list[int | None], values: list[Vector]) -> Vector:
    return tuple(sum((values[r][i] for r in row if r is not None), Fraction()) for i in range(4))


def score(
    week: Week,
    weights: list[int],
    values: list[Vector],
    target: Vector,
    scale: Vector,
    previous: Counter[int],
) -> Score:
    nutritional = sum(
        (
            w * distance(total(row, values), target, scale)
            for row, w in zip(week, weights, strict=True)
        ),
        Fraction(),
    )
    used = counts(week, weights)
    diversity = sum(n * n + 2 * n * previous[r] for r, n in used.items())
    return nutritional, diversity


def greedy(
    weights: list[int],
    values: list[Vector],
    target: Vector,
    scale: Vector,
    limit: int | None,
    previous: Counter[int],
    start: int,
) -> Week:
    week: Week = []
    used: Counter[int] = Counter()
    for weight in weights:
        row: list[int | None] = [None] * 4
        for step, slot in enumerate(REQUIRED):
            partial_target = tuple(t * (step + 1) / 3 for t in target)
            options = [r for r in range(len(values)) if limit is None or used[r] < limit]

            def rank(
                r: int,
                row: list[int | None] = row,
                slot: int = slot,
                partial_target: Vector = partial_target,
            ) -> tuple[Fraction, int, int]:
                candidate = row.copy()
                candidate[slot] = r
                return (
                    distance(total(candidate, values), partial_target, scale),
                    used[r] + previous[r],
                    r,
                )

            options.sort(key=rank)
            # Multiple deterministic starts escape simple single-slot local minima.
            selected = options[min(start, len(options) - 1)] if step == 0 else options[0]
            row[slot] = selected
            used[selected] += weight
        week.append(row)
    return week


def improve(
    week: Week,
    weights: list[int],
    values: list[Vector],
    target: Vector,
    scale: Vector,
    limit: int | None,
    previous: Counter[int],
) -> Week:
    """Coordinate replacements, optional first courses and swaps; accept strict improvement."""
    current = score(week, weights, values, target, scale, previous)
    for _ in range(IMPROVEMENT_ROUNDS):
        changed = False
        used = counts(week, weights)
        # Replacement deltas only affect one day and two recipe counts.
        for day, row in enumerate(week):
            weight = weights[day]
            for slot in range(4):
                old = row[slot]
                best = old
                best_score = current
                before = distance(total(row, values), target, scale)
                options: list[int | None] = list(range(len(values)))
                if slot == 1:
                    options.append(None)
                for candidate in options:
                    if candidate == old or (
                        candidate is not None
                        and limit is not None
                        and used[candidate] + weight > limit
                    ):
                        continue
                    row[slot] = candidate
                    nutrition = current[0] + weight * (
                        distance(total(row, values), target, scale) - before
                    )
                    diversity = current[1]
                    for r, delta in ((old, -weight), (candidate, weight)):
                        if r is not None:
                            diversity += 2 * delta * (used[r] + previous[r]) + delta * delta
                    proposed = (nutrition, diversity)
                    if proposed < best_score:
                        best, best_score = candidate, proposed
                row[slot] = best
                if best != old:
                    if old is not None:
                        used[old] -= weight
                    if best is not None:
                        used[best] += weight
                    current = best_score
                    changed = True
        # In the limited mode, swaps can improve two days even at saturated capacity.
        if limit is not None:
            for a in range(len(week)):
                for b in range(a + 1, len(week)):
                    for sa in range(4):
                        for sb in range(4):
                            x, y = week[a][sa], week[b][sb]
                            if x == y or (x is None and sb != 1) or (y is None and sa != 1):
                                continue
                            before = distance(total(week[a], values), target, scale) + distance(
                                total(week[b], values), target, scale
                            )
                            week[a][sa], week[b][sb] = y, x
                            after = distance(total(week[a], values), target, scale) + distance(
                                total(week[b], values), target, scale
                            )
                            if after < before:
                                current = (current[0] + after - before, current[1])
                                changed = True
                            else:
                                week[a][sa], week[b][sb] = x, y
        if not changed:
            break
    return week


def generate(revision: PlanRevision, recipes: tuple[RecipeSnapshot, ...]) -> PlanRevision:
    if not recipes:
        raise ValidationError(
            "Нет активных рецептов с явным допуском. "
            "Подтвердите допуск в библиотеке или добавьте рецепты."
        )
    limit = revision.repeat_limit if revision.mode == RepeatMode.LIMITED else None
    if limit is not None and len(recipes) * limit < 21:
        raise ValidationError(
            f"Для 21 обязательной порции за неделю доступны {len(recipes)} рецептов "
            f"с лимитом {limit}: не более {len(recipes) * limit} появлений. "
            "Увеличьте лимит, выберите A/B или подтвердите больше рецептов."
        )
    target = vector(revision.targets)
    scale = tuple(max(t, Fraction(1)) for t in target)
    ordered = sorted(recipes, key=lambda r: r.recipe_id)
    all_values = [vector(r.nutrition) for r in ordered]
    previous_global: Counter[str] = Counter()
    entries: list[MenuEntry] = []
    for week_number in range(revision.days // 7):
        # Bound search cost for large libraries; every recipe considered for ranking.
        indices = sorted(
            range(len(ordered)),
            key=lambda r: (
                min(
                    distance(all_values[r], tuple(t / portions for t in target), scale)
                    for portions in (3, 4)
                ),
                previous_global[ordered[r].recipe_id],
                ordered[r].recipe_id,
            ),
        )[:MAX_CANDIDATES]
        pool = [ordered[r] for r in indices]
        values = [all_values[r] for r in indices]
        previous = Counter({i: previous_global[r.recipe_id] for i, r in enumerate(pool)})
        weights = [4, 3] if revision.mode == RepeatMode.AB else [1] * 7
        attempts = [
            improve(
                greedy(weights, values, target, scale, limit, previous, start),
                weights,
                values,
                target,
                scale,
                limit,
                previous,
            )
            for start in range(min(STARTS, len(pool)))
        ]
        chosen = min(attempts, key=lambda w: score(w, weights, values, target, scale, previous))
        previous_global = Counter()
        for day in range(7):
            row = chosen[day % 2] if revision.mode == RepeatMode.AB else chosen[day]
            for slot, recipe in zip(SLOTS, row, strict=True):
                if recipe is not None:
                    snapshot = pool[recipe]
                    entries.append(MenuEntry(week_number * 7 + day, slot, snapshot))
                    previous_global[snapshot.recipe_id] += 1
    return replace(revision, entries=tuple(entries))
