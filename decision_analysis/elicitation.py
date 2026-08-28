"""Preference elicitation math. Pure functions and sessions only - no I/O.

Swing weighting (M1) plus the indirect-inference family (M2): certainty-
equivalent bisection for risk attitude and lottery bisection for loss
aversion. Interaction (question wording, y/n parsing) happens in the
orchestrator; every number is computed here.
"""

from __future__ import annotations

import math


def swing_weights(points_by_criterion: dict[str, float]) -> dict[str, float]:
    """Convert swing points into normalized weights.

    The top-ranked swing gets 100 points; every other criterion gets points
    relative to it. Weights are the points normalized to sum to 1.
    """
    total = sum(points_by_criterion.values())
    if total <= 0:
        raise ValueError("swing points must contain at least one positive value")
    return {cid: pts / total for cid, pts in points_by_criterion.items()}


class _BisectionSession:
    """Shared bisection scaffold: propose an amount, record accept/reject,
    converge on an indifference point in a fixed number of questions."""

    def __init__(self, lo: float, hi: float, steps: int = 4) -> None:
        self.lo = lo
        self.hi = hi
        self.steps_left = steps
        self.history: list[dict] = []

    @property
    def done(self) -> bool:
        return self.steps_left <= 0

    def next_offer(self) -> float:
        return round((self.lo + self.hi) / 2.0, 2)

    def _record(self, offer: float, answer: bool, moves_up: bool) -> None:
        self.history.append({"offer": offer, "accepted": answer})
        if moves_up:
            self.lo = offer
        else:
            self.hi = offer
        self.steps_left -= 1

    @property
    def indifference_point(self) -> float:
        return (self.lo + self.hi) / 2.0


class CertaintyEquivalentSession(_BisectionSession):
    """Risk attitude via certainty equivalent.

    Lottery: 50% win `stake`, 50% win nothing (EV = stake/2). Each question
    offers a sure amount instead; bisection converges on the certainty
    equivalent CE. Fitting the power utility u(x) = x^r to indifference gives
    r = ln(0.5) / ln(CE / stake): r < 1 risk-averse, r = 1 neutral, r > 1
    risk-seeking.
    """

    def __init__(self, stake: float = 10000.0, steps: int = 4) -> None:
        super().__init__(lo=0.0, hi=stake, steps=steps)
        self.stake = stake

    def record(self, accepted_sure_amount: bool) -> None:
        # Accepting the sure amount means CE <= offer: search lower.
        self._record(self.next_offer(), accepted_sure_amount,
                     moves_up=not accepted_sure_amount)

    @property
    def risk_exponent(self) -> float:
        # Clamp CE away from 0 and stake so the log ratio stays finite.
        ce = min(max(self.indifference_point, 0.02 * self.stake), 0.98 * self.stake)
        return math.log(0.5) / math.log(ce / self.stake)


class LossAversionSession(_BisectionSession):
    """Loss aversion via a 50/50 accept-or-decline deal.

    Deal: 50% win `gain`, 50% lose X. Bisection on X converges on the loss
    X* at which the user is indifferent; under a piecewise-linear value
    function, lambda = gain / X*. Search range (gain/10, gain) covers
    lambda in [1, 10]; typical empirical values are around 1.5-2.5.
    """

    def __init__(self, gain: float = 1000.0, steps: int = 4) -> None:
        super().__init__(lo=gain / 10.0, hi=gain, steps=steps)
        self.gain = gain

    def record(self, accepted_deal: bool) -> None:
        # Accepting the deal at loss X means the indifference loss is larger:
        # search upward. Declining means it is smaller: search downward.
        self._record(self.next_offer(), accepted_deal, moves_up=accepted_deal)

    @property
    def loss_aversion_lambda(self) -> float:
        return self.gain / self.indifference_point


def scenario_pair_check(weights: dict[str, float]) -> dict | None:
    """Build the scenario-pair consistency check for the two heaviest criteria.

    Returns None when the check is uninformative: fewer than two criteria,
    or the top weight dwarfs the runner-up (the implied answer is obvious).
    Otherwise returns {c1, c2, implied}: with A = best-on-c1/worst-on-c2 and
    B = the reverse (identical elsewhere), stated weights imply choosing
    "A" iff w1 > w2.
    """
    if len(weights) < 2:
        return None
    (c1, w1), (c2, w2) = sorted(weights.items(), key=lambda kv: -kv[1])[:2]
    if w2 <= 0 or w1 / w2 > 2.5:
        return None
    return {"c1": c1, "c2": c2, "implied": "A" if w1 > w2 else "B"}
