---
name: elicit-risk-lottery
description: Infer the user's risk attitude from certainty-equivalent bets - never ask "how risk averse are you". Deterministic bisection; math in decision_analysis/elicitation.py (CertaintyEquivalentSession).
case_file_reads: []
case_file_writes: [preferences.risk_exponent, elicitation_log]
implementation: decision_analysis/elicitation.py::CertaintyEquivalentSession
---

# Risk-attitude elicitation (certainty equivalent)

## Method

Reference lottery: 50% win `stake` (default 10,000 in the user's currency),
50% win nothing (EV = stake/2). Each question offers a SURE amount instead;
the user accepts or declines. Bisection on the sure amount (4 questions)
converges on the certainty equivalent CE.

Fit the power utility u(x) = x^r at indifference:
r = ln(0.5) / ln(CE / stake). r < 1 risk-averse, r = 1 neutral, r > 1
risk-seeking. CE is clamped to [0.02, 0.98] * stake before the log.

## Application

r is applied in Monte Carlo ranking as a certainty-equivalent aggregation:
CE_i = (E[V_i^r])^(1/r) over the utility distribution of each alternative -
risk-averse users thereby penalize high-variance options.

## Contract

- Record value, source = "certainty-equivalent-bisection", confidence, and
  the full offer/answer history in elicitation_log.
- The bisection and the exponent MUST come from CertaintyEquivalentSession;
  an LLM may phrase the questions (in English) but never computes.
- Optional step: skippable; absence of risk_exponent simply disables the
  risk-adjusted view.
