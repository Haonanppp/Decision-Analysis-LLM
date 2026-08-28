---
name: elicit-scenario-choice
description: Consistency check on elicited weights via a constructed scenario pair - the user's choice between two hypothetical options is compared against what their stated weights imply. Deterministic construction and verdict in code; the LLM only phrases the scenario (in English).
case_file_reads: [criteria]
case_file_writes: [criteria.weight_confidence, elicitation_log]
implementation: decision_analysis/elicitation.py::scenario_pair_check
---

# Scenario-choice consistency check

## Method

After swing weighting, take the two highest-weight criteria c1, c2 and
construct two hypothetical options, identical elsewhere:

- Scenario A: BEST on c1, WORST on c2.
- Scenario B: WORST on c1, BEST on c2.

The stated weights imply preferring A iff w1 > w2. Ask the user which
scenario they would pick. Agreement -> weights confirmed (confidence up).
Contradiction -> the swing answers and the felt preference disagree: flag
both weights low-confidence, tell the user, and offer to re-score the two
criteria's swing points; log the whole exchange.

The check is most informative when w1 and w2 are close; when the top weight
dwarfs the rest the implied answer is obvious and the check may be skipped
(code decides by the weight gap).

## Contract

- Construction, the implied answer, and the consistency verdict are code
  (elicitation.py) - never model judgment.
- Result recorded in elicitation_log as
  {method: "scenario-consistency-check", implied, chosen, consistent}.
- An inconsistent user who declines to re-score proceeds anyway - the
  disagreement is recorded and surfaces in the report appendix.
