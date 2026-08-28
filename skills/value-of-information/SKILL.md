---
name: value-of-information
description: Prices each modeled uncertainty - EVPI overall and per-assessment-cell EVPPI with switch probabilities - answering "which uncertainty is worth resolving BEFORE deciding, and which research would be wasted?" Pure nested Monte Carlo in decision_analysis/voi.py.
case_file_reads: [criteria, alternatives, assessments, preferences]
case_file_writes: [analysis.voi]
implementation: decision_analysis/voi.py::analyze_voi
---

# Value-of-information skill

## Quantities

- **EVPI** (all uncertainty at once): E[max_a U_a(draw)] - max_a E[U_a].
  Zero means the recommendation is information-robust - no resolution of
  the stated score ranges can change it, so further research on the
  numbers is not worth its cost.
- **Per-cell EVPPI** via nested Monte Carlo: outer draws sample the focal
  cell from its own PERT prior; inner draws pin it and re-simulate the
  rest; value = E_theta[max_a E[U_a|theta]] - max_a E[U_a].
- **Switch probability** (the headline for users): how often the
  conditional best differs from the current recommendation - "if you go
  find this out, there is an X% chance you end up choosing differently."

## Contract

- Candidate cells screened by weight x relative range width; top 8 priced.
- Weights held at stated values (weight sensitivity is the flip-threshold
  analysis's job); seeds fixed for reproducibility.
- "Worth resolving" verdict: switch probability >= 5% or EVPPI >= 0.005.
  Worth-resolving cells feed the report's Next Steps as concrete
  find-this-out-first actions.
- All numbers from voi.py; an LLM never computes or adjusts them.
