---
name: elicit-swing-weights
description: Elicit criteria weights via swing weighting. Deterministic method - the math lives in decision_analysis/elicitation.py (swing_weights); the interaction is currently driven by the orchestrator CLI. LLM-mediated natural-language elicitation arrives in M2.
case_file_reads: [criteria]
case_file_writes: [criteria.weight, criteria.weight_source, elicitation_log]
implementation: decision_analysis/elicitation.py::swing_weights
---

# Swing weighting skill

## Method (why swing, not direct rating)

Direct importance ratings ("rate each criterion 1-10") ignore the RANGES of
the alternatives at hand and produce flat, unreliable weights. Swing
weighting anchors importance to the actual worst-to-best swing on each
criterion:

1. Ask the user to imagine a hypothetical option that is WORST on every
   criterion.
2. Ask which single criterion they would move from worst to best first.
   That swing gets 100 points.
3. For each remaining criterion (in chosen order), ask for its swing's value
   relative to the top swing (0-100).
4. Weights = points normalized to sum to 1. Record weight_source = "swing"
   and append the raw points to elicitation_log.

## Contract

- Input: the confirmed criteria list (ids, names, directions).
- Output: a weight in [0, 1] per criterion, summing to 1; provenance recorded.
- The numeric conversion MUST use `swing_weights()` in
  `decision_analysis/elicitation.py` - never let a language model do the
  normalization arithmetic.
