---
name: expected-utility
description: Analysis model for uncertain-bet decisions - outcomes x probabilities x payoffs valued through the user's elicited risk attitude (power utility) and loss aversion (prospect-style value function, status quo = reference point), with Monte Carlo over probability and payoff uncertainty. Pure computation.
case_file_reads: [alternatives, frame.outcomes, preferences]
case_file_writes: [analysis.bet]
implementation: decision_analysis/decision_tree.py::analyze_bet
---

# Expected-utility skill (uncertain-bet)

## Why not MAUT

A gamble is not a multi-criteria comparison; its structure is outcomes,
probabilities, and payoffs. The correct model is expected utility with the
user's own risk preferences - elicited earlier from bets, never asked as
numbers.

## Model

- Value function v(x) = x^r for gains, -lambda * (-x)^r for losses;
  reference point 0 = status quo. r from certainty-equivalent bisection,
  lambda from the loss-aversion deals (defaults 1.0 = risk-neutral when
  not elicited).
- Per draw: sample every probability and payoff from its PERT distribution,
  renormalize probabilities within the alternative, compute
  sum(p * v(payoff)); rank alternatives per draw.
- Outputs per alternative: mean prospect value (ranking key), risk-neutral
  expected payoff (for comparison), certainty equivalent in payoff units
  ("this bet is worth a sure X to you"), P(best), rank distribution.

## Report

The bet report shows the outcome tables, then the ranking with CE and EV
side by side - when CE ranks differently from EV, that is the user's risk
attitude speaking, and the report says so explicitly.

## Guardrail

All numbers from decision_tree.py; an LLM never computes or adjusts them.
