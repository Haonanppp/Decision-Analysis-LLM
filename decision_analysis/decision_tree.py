"""Expected-utility analysis for uncertain-bet decisions.

The right model for a gamble is not multi-criteria scoring but outcomes x
probabilities x payoffs, valued through the user's elicited risk attitude
and loss aversion (prospect-style value function, reference point = keeping
what you have). Monte Carlo covers uncertainty in both the probabilities
and the payoffs.

Outcome data lives in case.frame["outcomes"]:
  {alt_id: [ {"label": str,
              "p":      {"low": .., "mode": .., "high": ..},   # 0-1
              "payoff": {"low": .., "mode": .., "high": ..}} ]}
Probabilities are renormalized to sum to 1 within every draw, so entering
rough percentages is fine.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .casefile import Assessment, CaseFile

_EPS = 1e-12


@dataclass
class BetAnalysisResult:
    n_samples: int
    ranking: list[str]  # by mean prospect value, best first
    mean_value: dict[str, float]  # mean prospect value per alternative
    expected_payoff: dict[str, float]  # risk-neutral EV, for comparison
    certainty_equivalent: dict[str, float]  # in payoff units
    p_best: dict[str, float]
    rank_probs: dict[str, list[float]]
    risk_exponent: float
    loss_aversion: float
    # Realized-outcome payoff samples per alternative (for risk-profile CDFs):
    # one outcome drawn per simulated world, sorted ascending.
    payoff_samples: dict[str, list[float]] | None = None


def _value(x: float, r: float, lam: float) -> float:
    """Prospect-style value function, reference point 0 (status quo)."""
    if x >= 0:
        return x ** r
    return -lam * ((-x) ** r)


def _inverse_value(v: float, r: float, lam: float) -> float:
    if v >= 0:
        return v ** (1.0 / r)
    return -((-v / lam) ** (1.0 / r))


def _sample(a: Assessment, rng: random.Random) -> float:
    if a.high - a.low < _EPS:
        return a.mode
    span = a.high - a.low
    alpha = 1.0 + 4.0 * (a.mode - a.low) / span
    beta = 1.0 + 4.0 * (a.high - a.mode) / span
    return a.low + span * rng.betavariate(alpha, beta)


def _as_assessment(d: dict) -> Assessment:
    return Assessment(low=float(d["low"]), mode=float(d["mode"]), high=float(d["high"]))


def analyze_bet(
    case: CaseFile, n_samples: int = 10000, seed: int = 7
) -> BetAnalysisResult:
    outcomes = case.frame.get("outcomes", {})
    alts = [a for a in case.active_alternatives() if a.id in outcomes]
    if len(alts) < 2:
        raise ValueError("uncertain-bet analysis needs outcomes for >= 2 alternatives")
    alt_ids = [a.id for a in alts]

    r = case.preferences.get("risk_exponent", {}).get("value") or 1.0
    lam = case.preferences.get("loss_aversion", {}).get("value") or 1.0

    rng = random.Random(seed)
    rank_counts = {aid: [0] * len(alt_ids) for aid in alt_ids}
    sum_value = {aid: 0.0 for aid in alt_ids}
    sum_payoff = {aid: 0.0 for aid in alt_ids}

    parsed = {
        aid: [
            (_as_assessment(o["p"]), _as_assessment(o["payoff"]))
            for o in outcomes[aid]
        ]
        for aid in alt_ids
    }

    payoff_samples: dict[str, list[float]] = {aid: [] for aid in alt_ids}

    for _ in range(n_samples):
        values = {}
        for aid in alt_ids:
            probs = [max(_sample(p, rng), 0.0) for p, _ in parsed[aid]]
            total = sum(probs) or 1.0
            probs = [p / total for p in probs]
            payoffs = [_sample(pay, rng) for _, pay in parsed[aid]]
            values[aid] = sum(
                p * _value(x, r, lam) for p, x in zip(probs, payoffs)
            )
            sum_payoff[aid] += sum(p * x for p, x in zip(probs, payoffs))
            # Realize ONE outcome in this world for the risk-profile CDF.
            u = rng.random()
            cumulative = 0.0
            realized = payoffs[-1]
            for p, x in zip(probs, payoffs):
                cumulative += p
                if u <= cumulative:
                    realized = x
                    break
            payoff_samples[aid].append(realized)
        ordered = sorted(alt_ids, key=lambda aid: values[aid], reverse=True)
        for rank, aid in enumerate(ordered):
            rank_counts[aid][rank] += 1
        for aid in alt_ids:
            sum_value[aid] += values[aid]

    mean_value = {aid: sum_value[aid] / n_samples for aid in alt_ids}
    rank_probs = {
        aid: [c / n_samples for c in counts] for aid, counts in rank_counts.items()
    }
    result = BetAnalysisResult(
        n_samples=n_samples,
        ranking=sorted(alt_ids, key=lambda aid: mean_value[aid], reverse=True),
        mean_value=mean_value,
        expected_payoff={aid: sum_payoff[aid] / n_samples for aid in alt_ids},
        certainty_equivalent={
            aid: _inverse_value(mean_value[aid], r, lam) for aid in alt_ids
        },
        p_best={aid: rank_probs[aid][0] for aid in alt_ids},
        rank_probs=rank_probs,
        risk_exponent=r,
        loss_aversion=lam,
        payoff_samples={aid: sorted(s) for aid, s in payoff_samples.items()},
    )
    case.analysis["bet"] = {
        "ranking": result.ranking,
        "mean_value": result.mean_value,
        "expected_payoff": result.expected_payoff,
        "certainty_equivalent": result.certainty_equivalent,
        "p_best": result.p_best,
        "rank_probs": result.rank_probs,
        "risk_exponent": r,
        "loss_aversion": lam,
    }
    return result
