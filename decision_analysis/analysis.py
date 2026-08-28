"""MAUT scoring and weight-flip sensitivity. Pure functions only - no I/O.

M1 is deterministic: assessments collapse to their PERT mean before scoring.
M2 replaces the collapse with Monte Carlo sampling over the three-point
distributions and adds rank probabilities; the normalization and flip-point
logic below is reused unchanged.
"""

from __future__ import annotations

import math
import random
import zlib
from dataclasses import dataclass, field

from .casefile import Assessment, CaseFile

_EPS = 1e-12
_QUANTILE_K = 513


def _phi(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def build_quantile_tables(case: CaseFile) -> dict:
    """Deterministic empirical quantile table per ranged cell, used to map
    copula uniforms onto each cell's own PERT marginal."""
    tables: dict[tuple[str, str], list[float]] = {}
    for a in case.active_alternatives():
        for crit in case.criteria:
            assessment = case.assessment(a.id, crit.id)
            if assessment.high - assessment.low < _EPS:
                continue
            seed = zlib.crc32(f"{a.id}|{crit.id}".encode())
            rng = random.Random(seed)
            tables[(a.id, crit.id)] = sorted(
                _sample_pert(assessment, rng) for _ in range(_QUANTILE_K)
            )
    return tables


def sample_alternative_row(
    case: CaseFile,
    alt_id: str,
    rng: random.Random,
    rho: float,
    tables: dict,
) -> dict[str, float]:
    """One draw of all of an alternative's cells.

    rho = 0: independent PERT sampling (identical to the pre-correlation
    behavior). rho > 0: a Gaussian-copula common factor couples the
    alternative's ranged cells - one bad turn of events drags them down
    together - while each cell keeps its own PERT marginal via the
    quantile table.
    """
    row: dict[str, float] = {}
    z_common = rng.gauss(0.0, 1.0) if rho > 0 else 0.0
    sqrt_rest = math.sqrt(1.0 - rho * rho) if rho > 0 else 1.0
    for crit in case.criteria:
        assessment = case.assessment(alt_id, crit.id)
        if assessment.high - assessment.low < _EPS:
            row[crit.id] = assessment.mode
        elif rho > 0:
            z = rho * z_common + sqrt_rest * rng.gauss(0.0, 1.0)
            u = min(max(_phi(z), 1e-9), 1.0 - 1e-9)
            table = tables[(alt_id, crit.id)]
            row[crit.id] = table[min(int(u * (_QUANTILE_K - 1)), _QUANTILE_K - 1)]
        else:
            row[crit.id] = _sample_pert(assessment, rng)
    return row


@dataclass
class FlipPoint:
    """Weight threshold at which the recommended alternative changes."""

    criterion_id: str
    current_weight: float
    flip_weight: float
    new_winner_id: str

    @property
    def direction(self) -> str:
        return "increase" if self.flip_weight > self.current_weight else "decrease"


@dataclass
class AnalysisResult:
    # normalized[alt_id][crit_id] -> utility in [0, 1]
    normalized: dict[str, dict[str, float]]
    utilities: dict[str, float]
    ranking: list[str]  # alternative ids, best first
    flip_points: list[FlipPoint]


def criterion_bounds(case: CaseFile) -> dict[str, tuple[float, float]]:
    """Normalization envelope per criterion: the span from the lowest 'low'
    to the highest 'high' across all active alternatives' three-point
    estimates. Using the range envelope instead of a min-max over point
    means preserves the MAGNITUDE of differences - critically, with only
    two alternatives a mean-based min-max degenerates to 0/1 on every
    criterion, erasing whether the gap was 2x or 400x."""
    alts = case.active_alternatives()
    bounds: dict[str, tuple[float, float]] = {}
    for crit in case.criteria:
        lows = [case.assessment(a.id, crit.id).low for a in alts]
        highs = [case.assessment(a.id, crit.id).high for a in alts]
        bounds[crit.id] = (min(lows), max(highs))
    return bounds


def _normalize_value(
    value: float, lo: float, hi: float, direction: str
) -> float:
    if hi - lo < _EPS:
        return 0.5
    score = (value - lo) / (hi - lo)
    return 1.0 - score if direction == "min" else score


def normalize_scores(case: CaseFile) -> dict[str, dict[str, float]]:
    """Normalize each criterion to [0, 1] against its range envelope.

    Direction "min" is inverted so 1.0 is always best. A criterion on which
    all alternatives tie contributes a constant 0.5 (it cannot discriminate).
    """
    bounds = criterion_bounds(case)
    alts = case.active_alternatives()
    normalized: dict[str, dict[str, float]] = {a.id: {} for a in alts}
    for crit in case.criteria:
        lo, hi = bounds[crit.id]
        for a in alts:
            normalized[a.id][crit.id] = _normalize_value(
                case.assessment(a.id, crit.id).pert_mean, lo, hi, crit.direction
            )
    return normalized


def weighted_utilities(
    case: CaseFile, normalized: dict[str, dict[str, float]]
) -> dict[str, float]:
    return {
        alt.id: sum(c.weight * normalized[alt.id][c.id] for c in case.criteria)
        for alt in case.active_alternatives()
    }


def flip_thresholds(
    case: CaseFile,
    normalized: dict[str, dict[str, float]],
    ranking: list[str],
) -> list[FlipPoint]:
    """For each criterion, find the nearest weight (other weights rescaled
    proportionally) at which the current winner is overtaken.

    Total utility is linear in the criterion's weight t:
        U_i(t) = t * n_ic + (1 - t) * r_i
    where r_i is alternative i's utility over the remaining criteria with
    their weights renormalized. Each pairwise crossing is the root of a
    linear equation; the crossing nearest to the current weight (on either
    side) is the first flip.
    """
    if len(ranking) < 2:
        return []
    top = ranking[0]
    weights = {c.id: c.weight for c in case.criteria}
    flips: list[FlipPoint] = []

    for crit in case.criteria:
        wc = weights[crit.id]
        if wc >= 1.0 - _EPS:
            continue
        rest = {
            alt.id: sum(
                weights[c.id] * normalized[alt.id][c.id]
                for c in case.criteria
                if c.id != crit.id
            )
            / (1.0 - wc)
            for alt in case.active_alternatives()
        }
        crossings: list[tuple[float, str]] = []
        n_top, r_top = normalized[top][crit.id], rest[top]
        for alt in case.active_alternatives():
            if alt.id == top:
                continue
            n_alt, r_alt = normalized[alt.id][crit.id], rest[alt.id]
            denom = (n_top - n_alt) - (r_top - r_alt)
            if abs(denom) < _EPS:
                continue
            t = (r_alt - r_top) / denom
            if 0.0 <= t <= 1.0 and abs(t - wc) > 1e-9:
                crossings.append((t, alt.id))

        above = min((c for c in crossings if c[0] > wc), default=None)
        below = max((c for c in crossings if c[0] < wc), default=None)
        for crossing in (above, below):
            if crossing is not None:
                flips.append(
                    FlipPoint(
                        criterion_id=crit.id,
                        current_weight=wc,
                        flip_weight=crossing[0],
                        new_winner_id=crossing[1],
                    )
                )
    flips.sort(key=lambda f: abs(f.flip_weight - f.current_weight))
    return flips


@dataclass
class MonteCarloResult:
    n_samples: int
    # rank_probs[alt_id][k] = P(alternative finishes at rank k+1)
    rank_probs: dict[str, list[float]]
    p_best: dict[str, float]
    mean_rank: dict[str, float]
    mean_utility: dict[str, float]
    # Certainty-equivalent utility under power utility u(v) = v^r; None
    # when no risk attitude was elicited. Ranks by CE penalize variance
    # for risk-averse users (r < 1).
    ce_utility: dict[str, float] | None
    # Expected prospect value vs the baseline alternative (losses scaled by
    # lambda); None when no baseline or no lambda.
    prospect_value: dict[str, float] | None
    baseline_id: str | None
    # utility_quantiles[alt_id] = {"p5":.., "p25":.., "p50":.., "p75":.., "p95":..}
    utility_quantiles: dict[str, dict[str, float]] = field(default_factory=dict)


def _sample_pert(a: Assessment, rng: random.Random) -> float:
    if a.high - a.low < _EPS:
        return a.mode
    span = a.high - a.low
    alpha = 1.0 + 4.0 * (a.mode - a.low) / span
    beta = 1.0 + 4.0 * (a.high - a.mode) / span
    return a.low + span * rng.betavariate(alpha, beta)


def _sample_weights(
    weights: list[float], concentration: float, rng: random.Random
) -> list[float]:
    """Dirichlet jitter centered on the stated weights: gamma draws with
    shape proportional to each weight, normalized. Higher concentration =
    tighter around the stated weights."""
    draws = [rng.gammavariate(max(w, 1e-4) * concentration, 1.0) for w in weights]
    total = sum(draws)
    return [d / total for d in draws]


def monte_carlo(
    case: CaseFile,
    n_samples: int = 10000,
    weight_concentration: float = 120.0,
    seed: int = 7,
) -> MonteCarloResult:
    """Sample assessments (PERT over the three-point estimates) and weights
    (Dirichlet around the stated weights), score each draw with the same
    normalization + weighted sum as the deterministic path, and aggregate
    rank statistics."""
    rng = random.Random(seed)
    alts = case.active_alternatives()
    crits = case.criteria
    alt_ids = [a.id for a in alts]
    stated_weights = [c.weight for c in crits]

    risk = case.preferences.get("risk_exponent", {}).get("value")
    lam = case.preferences.get("loss_aversion", {}).get("value")
    baseline_id = case.preferences.get("baseline_alt_id")
    if baseline_id not in alt_ids:
        baseline_id = None

    rank_counts = {aid: [0] * len(alts) for aid in alt_ids}
    sum_utility = {aid: 0.0 for aid in alt_ids}
    sum_powered = {aid: 0.0 for aid in alt_ids} if risk else None
    sum_prospect = (
        {aid: 0.0 for aid in alt_ids} if (lam and baseline_id) else None
    )
    utility_samples: dict[str, list[float]] = {aid: [] for aid in alt_ids}

    bounds = criterion_bounds(case)
    # Elicited within-alternative correlation strengths (0 = independent).
    correlation = case.frame.get("correlation", {})
    tables = build_quantile_tables(case) if any(correlation.values()) else {}

    for _ in range(n_samples):
        w = _sample_weights(stated_weights, weight_concentration, rng)
        # Sampled values normalized against the same fixed range envelope as
        # the deterministic path - keeps magnitudes and avoids the 0/1
        # degeneration a within-draw min-max causes with few alternatives.
        utilities = {aid: 0.0 for aid in alt_ids}
        for aid in alt_ids:
            row = sample_alternative_row(
                case, aid, rng, float(correlation.get(aid, 0.0)), tables
            )
            for ci, crit in enumerate(crits):
                lo, hi = bounds[crit.id]
                utilities[aid] += w[ci] * _normalize_value(
                    row[crit.id], lo, hi, crit.direction
                )

        ordered = sorted(alt_ids, key=lambda aid: utilities[aid], reverse=True)
        for rank, aid in enumerate(ordered):
            rank_counts[aid][rank] += 1
        for aid in alt_ids:
            sum_utility[aid] += utilities[aid]
            utility_samples[aid].append(utilities[aid])
            if sum_powered is not None:
                sum_powered[aid] += max(utilities[aid], 0.0) ** risk
            if sum_prospect is not None:
                delta = utilities[aid] - utilities[baseline_id]
                sum_prospect[aid] += delta if delta >= 0 else lam * delta

    rank_probs = {
        aid: [c / n_samples for c in counts] for aid, counts in rank_counts.items()
    }
    quantiles: dict[str, dict[str, float]] = {}
    for aid, samples in utility_samples.items():
        samples.sort()
        quantiles[aid] = {
            f"p{p}": samples[min(int(len(samples) * p / 100), len(samples) - 1)]
            for p in (5, 25, 50, 75, 95)
        }
    return MonteCarloResult(
        n_samples=n_samples,
        rank_probs=rank_probs,
        p_best={aid: rank_probs[aid][0] for aid in alt_ids},
        mean_rank={
            aid: sum((k + 1) * p for k, p in enumerate(rank_probs[aid]))
            for aid in alt_ids
        },
        mean_utility={aid: sum_utility[aid] / n_samples for aid in alt_ids},
        ce_utility=(
            {aid: (sum_powered[aid] / n_samples) ** (1.0 / risk) for aid in alt_ids}
            if sum_powered is not None
            else None
        ),
        prospect_value=(
            {aid: sum_prospect[aid] / n_samples for aid in alt_ids}
            if sum_prospect is not None
            else None
        ),
        baseline_id=baseline_id,
        utility_quantiles=quantiles,
    )


def stakeholder_views(
    case: CaseFile, normalized: dict[str, dict[str, float]]
) -> dict:
    """Per-stakeholder rankings from their own weights over the shared
    (factual) normalized scores, plus disagreement flags vs the group view.
    Returns {} when the case has no stakeholder modeling."""
    stakeholders = case.frame.get("stakeholders", [])
    if not stakeholders:
        return {}
    group_utilities = weighted_utilities(case, normalized)
    group_top = max(group_utilities, key=group_utilities.get)
    views = {}
    for s in stakeholders:
        utilities = {
            alt.id: sum(
                s["weights"][c.id] * normalized[alt.id][c.id]
                for c in case.criteria
            )
            for alt in case.active_alternatives()
        }
        ranking = sorted(utilities, key=utilities.get, reverse=True)
        views[s["name"]] = {
            "utilities": utilities,
            "ranking": ranking,
            "influence": s["influence"],
            "agrees_with_group": ranking[0] == group_top,
        }
    return views


def analyze(case: CaseFile) -> AnalysisResult:
    normalized = normalize_scores(case)
    utilities = weighted_utilities(case, normalized)
    ranking = sorted(utilities, key=utilities.get, reverse=True)
    flips = flip_thresholds(case, normalized, ranking)
    case.analysis = {
        "utilities": utilities,
        "ranking": ranking,
        "flip_points": [vars(f) for f in flips],
    }
    return AnalysisResult(normalized, utilities, ranking, flips)
