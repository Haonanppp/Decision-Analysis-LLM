---
name: monte-carlo-ranking
description: Rank alternatives under uncertainty - PERT sampling of three-point assessments, Dirichlet jitter of weights, rank probabilities, risk-adjusted and loss-aversion-adjusted views. Pure computation in decision_analysis/analysis.py.
case_file_reads: [criteria, alternatives, assessments, preferences]
case_file_writes: [analysis.monte_carlo]
implementation: decision_analysis/analysis.py::monte_carlo
---

# Monte Carlo ranking skill

## Contract

- Inputs: complete assessment matrix (three-point cells), weights summing
  to 1, optional preferences (risk_exponent, loss_aversion, baseline_alt_id).
- Per draw (default 10,000; seeded for reproducibility):
  1. Sample each cell from its PERT distribution
     (Beta with alpha = 1 + 4(m-l)/(h-l), beta = 1 + 4(h-m)/(h-l)).
  2. Sample weights from a Dirichlet centered on the stated weights
     (concentration 120 - moderate confidence in stated weights).
  3. Normalize within the draw (same min-max + direction rule as the
     deterministic path) and compute weighted utilities; record the ranking.
- Outputs: rank_probs, p_best, mean_rank, mean_utility; ce_utility when a
  risk exponent exists (CE_i = (E[V^r])^(1/r)); prospect_value when lambda
  and a baseline exist.
- The deterministic ranking stays the primary result; MC quantifies its
  confidence and the adjusted views are reported alongside, never silently
  substituted.

## Guardrail

Never let a language model produce or adjust any of these numbers.
