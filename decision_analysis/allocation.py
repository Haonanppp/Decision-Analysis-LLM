"""Deterministic optimizer for allocation decisions.

Model: maximize sum_i w_i * x_i^alpha subject to sum x_i = total,
min_i <= x_i <= max_i, x_i a multiple of the unit. w_i are importance
weights elicited by swing weighting over the items; alpha in (0, 1) is the
diminishing-returns strength (smaller = returns flatten faster = spreads
the allocation more evenly).

With a common power utility the unconstrained optimum is closed-form:
x_i proportional to w_i^(1/(1-alpha)). Bounds are handled by active-set
iteration (clip violators to their bound, redistribute the rest), and
rounding to the unit distributes the remainder greedily by marginal
utility. Everything here is deterministic - no LLM, no sampling.

The optimizer is run at several alphas so the report can show how robust
the split is to the diminishing-returns assumption.
"""

from __future__ import annotations

from dataclasses import dataclass, field

_EPS = 1e-12

DEFAULT_ALPHAS = (0.3, 0.5, 0.7)
RECOMMENDED_ALPHA = 0.5


@dataclass
class AllocationItem:
    id: str
    name: str
    weight: float  # normalized importance from swing weighting
    min_amount: float = 0.0
    max_amount: float | None = None  # None = no cap


@dataclass
class AllocationResult:
    total: float
    unit: float
    # splits[alpha][item_id] = amount
    splits: dict[float, dict[str, float]] = field(default_factory=dict)
    # bounds that ended up binding at the recommended alpha
    binding: dict[str, str] = field(default_factory=dict)  # item_id -> "min"|"max"

    @property
    def recommended(self) -> dict[str, float]:
        if RECOMMENDED_ALPHA in self.splits:
            return self.splits[RECOMMENDED_ALPHA]
        return self.splits[sorted(self.splits)[len(self.splits) // 2]]


def _marginal(weight: float, x: float, unit: float, alpha: float) -> float:
    return weight * ((x + unit) ** alpha - x ** alpha)


def _continuous_optimum(
    items: list[AllocationItem], total: float, alpha: float
) -> tuple[dict[str, float], dict[str, str]]:
    """Active-set iteration on the closed-form solution."""
    fixed: dict[str, float] = {}
    binding: dict[str, str] = {}
    free = list(items)
    for _ in range(2 * len(items) + 1):
        budget = total - sum(fixed.values())
        exponent = 1.0 / (1.0 - alpha)
        scores = {it.id: max(it.weight, 0.0) ** exponent for it in free}
        score_sum = sum(scores.values())
        raw = {
            it.id: (budget * scores[it.id] / score_sum if score_sum > _EPS
                    else budget / len(free))
            for it in free
        }
        # Clip one violation DIRECTION per iteration. Capping a max-violator
        # frees budget (everyone else rises), so items that also looked
        # below their minimum may recover naturally - clipping both
        # directions in the same pass pins them to the minimum prematurely.
        max_violators = [
            it for it in free
            if it.max_amount is not None and raw[it.id] > it.max_amount + _EPS
        ]
        min_violators = [it for it in free if raw[it.id] < it.min_amount - _EPS]
        to_fix = max_violators or min_violators
        if not to_fix:
            return {**fixed, **raw}, binding
        for it in to_fix:
            if max_violators:
                fixed[it.id] = it.max_amount
                binding[it.id] = "max"
            else:
                fixed[it.id] = it.min_amount
                binding[it.id] = "min"
        free = [it for it in free if it.id not in fixed]
        if not free:
            return dict(fixed), binding
    return {**fixed, **{it.id: raw[it.id] for it in free}}, binding


def _round_to_unit(
    items: list[AllocationItem],
    continuous: dict[str, float],
    total: float,
    unit: float,
    alpha: float,
) -> dict[str, float]:
    """Floor to the unit, then hand out the remainder greedily by marginal
    utility, respecting caps. Keeps the sum exactly at total."""
    by_id = {it.id: it for it in items}
    floored = {
        iid: max(round(x // unit) * unit, by_id[iid].min_amount)
        for iid, x in continuous.items()
    }
    remainder_units = round((total - sum(floored.values())) / unit)
    for _ in range(max(remainder_units, 0)):
        candidates = [
            iid for iid, it in by_id.items()
            if it.max_amount is None or floored[iid] + unit <= it.max_amount + _EPS
        ]
        if not candidates:
            break
        best = max(
            candidates,
            key=lambda iid: _marginal(by_id[iid].weight, floored[iid], unit, alpha),
        )
        floored[best] += unit
    return floored


def optimize(
    items: list[AllocationItem],
    total: float,
    unit: float = 1.0,
    alphas: tuple[float, ...] = DEFAULT_ALPHAS,
) -> AllocationResult:
    if len(items) < 2:
        raise ValueError("allocation needs at least 2 items")
    if sum(it.min_amount for it in items) > total + _EPS:
        raise ValueError("infeasible: minimum allocations exceed the total")
    cap_sum = sum(
        it.max_amount if it.max_amount is not None else total for it in items
    )
    if cap_sum < total - _EPS:
        raise ValueError("infeasible: caps sum to less than the total")

    result = AllocationResult(total=total, unit=unit)
    for alpha in alphas:
        continuous, binding = _continuous_optimum(items, total, alpha)
        result.splits[alpha] = _round_to_unit(items, continuous, total, unit, alpha)
        if alpha == RECOMMENDED_ALPHA:
            result.binding = binding
    return result
