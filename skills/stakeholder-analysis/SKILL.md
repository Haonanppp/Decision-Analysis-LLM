---
name: stakeholder-analysis
description: Multi-stakeholder preference modeling - each stakeholder gets their own swing-weighting pass; the group ranking uses influence-weighted average weights, and per-stakeholder rankings surface disagreement explicitly. Deterministic aggregation in code.
case_file_reads: [criteria, alternatives, frame]
case_file_writes: [frame.stakeholders, criteria.weight (group), elicitation_log, analysis.stakeholder_views]
implementation: decision_analysis/orchestrator.py::_elicit_stakeholder_weights, decision_analysis/analysis.py::stakeholder_views
---

# Stakeholder analysis skill

## Principle

Scores are shared facts (an option's price is the same for everyone);
PREFERENCES differ per person. So the assessment matrix is elicited once,
while each stakeholder gets their own swing-weighting pass - answered by
them directly, or by the user as an honest proxy.

## Method

1. Name the stakeholders (2-6) and give each an influence weight 1-10
   (how much say they have in this decision).
2. Swing weighting per stakeholder over the same criteria.
3. Group weights = influence-weighted average of the stakeholders' weight
   vectors, normalized. The entire downstream pipeline (MAUT, Monte Carlo,
   flip thresholds) runs on the group weights unchanged.
4. Disagreement analysis: each stakeholder's individual ranking from their
   own weights. Any stakeholder whose personal #1 differs from the group
   #1 is flagged in the console and the report - a flagged disagreement is
   a conversation to have, not a number to average away.

## Notes

- The scenario-pair consistency check is skipped in group mode (there is
  no single "felt preference" to test).
- Risk/loss-attitude bets remain the deciding user's own.
- Aggregation is deterministic code; an LLM never adjusts weights.
