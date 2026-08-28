"""Value of information (VOI) for MAUT decisions.

Answers "which uncertainty is worth resolving BEFORE deciding?" with the
standard decision-analytic quantities, estimated on the same machinery as
the Monte Carlo ranking:

- EVPI (expected value of perfect information, all uncertainty at once):
  E[max_a U_a(draw)] - max_a E[U_a]. Zero means no conceivable resolution
  of the stated score ranges changes the choice - the recommendation is
  information-robust and further research is not worth its cost.
- Per-cell EVPPI (partial perfect information for one assessment cell),
  via nested Monte Carlo: outer samples of the focal cell's own PERT
  prior, inner simulation with that cell pinned; the value is
  E_theta[max_a E[U_a | theta]] - max_a E[U_a], and the more interpretable
  companion is the SWITCH PROBABILITY - how often the conditional best
  differs from the current recommendation.

Scope notes: weights are held at their stated values here - VOI prices
information about the WORLD (score ranges); preference uncertainty is
covered separately by the weight-flip thresholds. Pure stdlib, seeded.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .analysis import (
    _normalize_value,
    build_quantile_tables,
    criterion_bounds,
    sample_alternative_row,
)
from .casefile import CaseFile

_EPS = 1e-12


@dataclass
class CellVoi:
    alt_id: str
    crit_id: str
    switch_prob: float  # P(decision changes once this cell is known)
    evppi: float  # expected utility gain from resolving it
    span: tuple[float, float]  # the cell's low..high

    def worth_resolving(self) -> bool:
        return self.switch_prob >= 0.05 or self.evppi >= 0.005


@dataclass
class VoiResult:
    baseline_best_id: str
    baseline_eu: float
    evpi: float
    overall_switch_prob: float  # P(recommendation not best in a given draw)
    cells: list[CellVoi] = field(default_factory=list)
    n_outer: int = 0
    n_inner: int = 0

    def information_robust(self) -> bool:
        return self.evpi < 0.005 and not any(c.worth_resolving() for c in self.cells)


def _mean_utilities(
    case: CaseFile,
    bounds: dict,
    rng: random.Random,
    n: int,
    fixed: dict[tuple[str, str], float] | None = None,
    correlation: dict | None = None,
    tables: dict | None = None,
) -> tuple[dict[str, float], float]:
    """Inner simulation: mean utility per alternative and mean per-draw max
    (weights held at stated values; optional pinned cells; same correlated
    sampling as the main Monte Carlo)."""
    fixed = fixed or {}
    correlation = correlation or {}
    tables = tables or {}
    alts = case.active_alternatives()
    sums = {a.id: 0.0 for a in alts}
    sum_max = 0.0
    for _ in range(n):
        best = -1.0
        for a in alts:
            row = sample_alternative_row(
                case, a.id, rng, float(correlation.get(a.id, 0.0)), tables
            )
            utility = 0.0
            for crit in case.criteria:
                key = (a.id, crit.id)
                value = fixed[key] if key in fixed else row[crit.id]
                lo, hi = bounds[crit.id]
                utility += crit.weight * _normalize_value(
                    value, lo, hi, crit.direction
                )
            sums[a.id] += utility
            best = max(best, utility)
        sum_max += best
    return {aid: s / n for aid, s in sums.items()}, sum_max / n


def analyze_voi(
    case: CaseFile,
    n_outer: int = 16,
    n_inner: int = 400,
    max_cells: int = 8,
    seed: int = 11,
) -> VoiResult | None:
    """EVPI + per-cell EVPPI for the top uncertain assessment cells."""
    alts = case.active_alternatives()
    if len(alts) < 2 or not case.criteria:
        return None
    bounds = criterion_bounds(case)
    rng = random.Random(seed)
    correlation = case.frame.get("correlation", {})
    tables = build_quantile_tables(case) if any(correlation.values()) else {}

    # Baseline: everything uncertain. Track how often the on-average best
    # alternative is beaten within a draw.
    n_base = n_outer * n_inner
    means, mean_max = _mean_utilities(
        case, bounds, rng, n_base, correlation=correlation, tables=tables
    )
    best_id = max(means, key=means.get)
    baseline_eu = means[best_id]
    evpi = max(mean_max - baseline_eu, 0.0)

    switch_draws = 0
    for _ in range(1000):
        utilities = {}
        for a in alts:
            row = sample_alternative_row(
                case, a.id, rng, float(correlation.get(a.id, 0.0)), tables
            )
            u = 0.0
            for crit in case.criteria:
                lo, hi = bounds[crit.id]
                u += crit.weight * _normalize_value(
                    row[crit.id], lo, hi, crit.direction
                )
            utilities[a.id] = u
        if max(utilities, key=utilities.get) != best_id:
            switch_draws += 1
    overall_switch = switch_draws / 1000

    # Candidate cells: widest uncertainty x criterion weight, screened.
    candidates: list[tuple[float, str, str]] = []
    for a in alts:
        for crit in case.criteria:
            assessment = case.assessment(a.id, crit.id)
            lo, hi = bounds[crit.id]
            env_span = hi - lo
            cell_span = assessment.high - assessment.low
            if env_span < _EPS or cell_span < _EPS:
                continue
            impact_proxy = crit.weight * (cell_span / env_span)
            candidates.append((impact_proxy, a.id, crit.id))
    candidates.sort(reverse=True)

    cells: list[CellVoi] = []
    for _, aid, cid in candidates[:max_cells]:
        assessment = case.assessment(aid, cid)
        sum_cond_best = 0.0
        switches = 0
        for _ in range(n_outer):
            pinned = sample_alternative_row(
                case, aid, rng, float(correlation.get(aid, 0.0)), tables
            )[cid]
            cond_means, _ = _mean_utilities(
                case, bounds, rng, n_inner, fixed={(aid, cid): pinned},
                correlation=correlation, tables=tables,
            )
            cond_best = max(cond_means, key=cond_means.get)
            sum_cond_best += cond_means[cond_best]
            if cond_best != best_id:
                switches += 1
        # If the conditional best never differs from the baseline best,
        # EVPPI is exactly zero by definition (E_theta[max] collapses to
        # the baseline expectation); any residual is inner-loop noise.
        evppi = (
            max(sum_cond_best / n_outer - baseline_eu, 0.0)
            if switches > 0
            else 0.0
        )
        cells.append(
            CellVoi(
                alt_id=aid,
                crit_id=cid,
                switch_prob=switches / n_outer,
                evppi=evppi,
                span=(assessment.low, assessment.high),
            )
        )
    cells.sort(key=lambda c: (-c.switch_prob, -c.evppi))

    result = VoiResult(
        baseline_best_id=best_id,
        baseline_eu=baseline_eu,
        evpi=evpi,
        overall_switch_prob=overall_switch,
        cells=cells,
        n_outer=n_outer,
        n_inner=n_inner,
    )
    case.analysis["voi"] = {
        "evpi": evpi,
        "overall_switch_prob": overall_switch,
        "cells": [
            {"alt_id": c.alt_id, "crit_id": c.crit_id,
             "switch_prob": c.switch_prob, "evppi": c.evppi,
             "worth_resolving": c.worth_resolving()}
            for c in cells
        ],
    }
    return result
