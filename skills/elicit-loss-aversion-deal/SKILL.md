---
name: elicit-loss-aversion-deal
description: Infer the loss-aversion coefficient lambda from accept/decline 50/50 deals - never ask for a number directly. Deterministic bisection; math in decision_analysis/elicitation.py (LossAversionSession).
case_file_reads: [alternatives]
case_file_writes: [preferences.loss_aversion, preferences.baseline_alt_id, elicitation_log]
implementation: decision_analysis/elicitation.py::LossAversionSession
---

# Loss-aversion elicitation (50/50 deal)

## Method

Deal: 50% win `gain` (default 1,000), 50% lose X. The user accepts or
declines at each proposed X; bisection (4 questions) converges on the
indifference loss X*. Under a piecewise-linear value function,
lambda = gain / X*. Search range (gain/10, gain) covers lambda in [1, 10];
typical empirical values are 1.5-2.5.

After the bets, ask which alternative (if any) is the status quo - the
reference point losses are measured against. Store it as baseline_alt_id.

## Application

In Monte Carlo, per draw: delta_i = V_i - V_baseline; prospect value
= delta if delta >= 0 else lambda * delta; expectation over draws gives the
loss-aversion-adjusted view reported alongside the neutral ranking (it never
replaces it).

## Contract

- Record value, source = "lottery-bisection", confidence, full history.
- Numbers come only from LossAversionSession; the LLM may phrase deals in
  English and parse yes/no answers.
- Skippable; without lambda or a baseline the prospect view is omitted.
