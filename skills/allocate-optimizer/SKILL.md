---
name: allocate-optimizer
description: Deterministic optimizer for allocation decisions - computes the split of a total resource across items from swing-elicited importance weights, per-item bounds, and unit granularity, under a diminishing-returns power utility. Pure computation in decision_analysis/allocation.py.
case_file_reads: [alternatives (as items), frame.allocation]
case_file_writes: [frame.allocation, analysis]
implementation: decision_analysis/allocation.py::optimize
---

# Allocation optimizer skill

## Model

Maximize sum_i w_i * x_i^alpha subject to sum x_i = total,
min_i <= x_i <= max_i, and x_i a multiple of the unit.

- w_i: importance weights over the ITEMS, elicited by the same swing
  weighting used for criteria (never asked as raw percentages).
- alpha in (0,1): diminishing-returns strength. Run at alpha = 0.3 / 0.5 /
  0.7; recommend the 0.5 split and show all three as a robustness table -
  a split that barely moves across alphas is a safe recommendation.
- Solution: closed-form power-utility optimum (x_i proportional to
  w_i^(1/(1-alpha))), active-set iteration for bounds, greedy
  marginal-utility rounding to the unit (sum preserved exactly).

## Contract

- Infeasible setups (minimums exceed the total; caps below the total) are
  rejected with a clear message, never silently adjusted.
- Binding bounds are reported ("X is at its cap - relaxing it would change
  the split").
- All numbers from allocation.py; an LLM never computes or adjusts them.
