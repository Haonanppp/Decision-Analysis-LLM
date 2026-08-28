---
name: elicit-outcomes
description: For uncertain-bet decisions, elicit each alternative's possible outcomes as probability x payoff pairs (both may be ranges). Interaction is CLI-driven; validation and storage are deterministic code.
case_file_reads: [alternatives, frame]
case_file_writes: [frame.outcomes, elicitation_log]
implementation: decision_analysis/orchestrator.py::_elicit_outcomes
---

# Outcome elicitation skill (uncertain-bet)

## Method

For every alternative, collect its possible outcomes:

- label - what happens ("deal closes", "total loss", "break even");
- probability - a percentage, or a low/most-likely/high range ("20" or
  "10/20/35"). Probabilities need not sum exactly to 100: they are
  renormalized within every Monte Carlo draw, so rough numbers are fine.
- payoff - the net consequence in one consistent unit (money is typical);
  LOSSES ARE NEGATIVE. Point value or a range.

A certain alternative ("don't bet") is one outcome with probability 100
and payoff 0 (offered as the default).

## Guardrails

- At least 2 alternatives with outcomes; each alternative >= 1 outcome.
- Warn (not block) when stated mode-probabilities sum far from 100%.
- All numbers stored as three-point Assessments; unknown checklist slots
  about outcomes justify WIDE probability ranges, not made-up precision.
