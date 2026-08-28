---
name: premortem
description: Prospective hindsight on the recommended alternative - "a year from now this choice clearly failed; why?" Runs after analysis, before the report; risks go into the critique log and the report's risk section. Clean context (case file only).
case_file_reads: [statement, frame, alternatives, criteria, analysis]
case_file_writes: [critique_log]
---

# Premortem skill

You see a finished decision analysis and its recommendation. Assume the
recommendation was taken and, one year later, it clearly FAILED. Work
backward: what most plausibly caused the failure?

## Rules

- 2-4 risks, ranked by (likelihood x damage). Not generic caution -
  each risk must be specific to THIS case's facts, constraints, unknowns,
  and score ranges. The unknowns in the checklist are prime material:
  a failure often comes from exactly what nobody could answer.
- severity: "high" (could make this the wrong choice outright),
  "medium" (materially worse than expected), "low" (annoyance).
- Every risk carries a concrete mitigation or early-warning sign the user
  could act on ("negotiate X before signing", "revisit if Y happens").
- Do NOT re-litigate the ranking; the analysis stands. Your job is what
  could break it in the world, not in the math.
- All output in English.

## Output

Reply with ONLY a JSON object:

{
  "risks": [
    {"severity": "high" | "medium" | "low", "text": "...", "mitigation": "..."}
  ]
}
