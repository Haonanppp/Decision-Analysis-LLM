---
name: report-writer
description: Render the final Markdown report from a completed case file. Template-driven in M1.5 (decision_analysis/report.py); an LLM-polished narrative layer and charts arrive in M2. Runs on the case file only, not the conversation.
case_file_reads: [everything]
case_file_writes: []
implementation: decision_analysis/report.py::render_report
---

# Report writer skill

## Report outline (fixed)

1. Executive summary: recommendation, utility margin, and the single most
   fragile assumption (nearest weight-flip threshold) - or an explicit
   robustness statement if nothing flips.
2. Criteria and weights, with provenance (weight_source) per criterion.
3. Assessments: raw values, ranges shown as low / most-likely / high.
4. Ranking: weighted utilities plus the normalized score matrix.
5. Sensitivity: every weight-flip threshold, phrased as "if the weight of X
   rises/falls past P%, Y becomes the top choice".
6. Method appendix.

## Narrative layer (LLM part, M3)

When invoked as a skill, produce a short executive narrative in English,
inserted at the top of the report above the template sections. 2-3
paragraphs: the recommendation and how confident to be in it
(P(best), margin), what would change the answer (nearest flip threshold),
and what remains unknown or risky (open unknowns, top premortem risk).

- Use ONLY numbers already present in the case file's analysis - never
  compute or invent a number.
- Plain prose, no headings, no bullet lists, no flattery.

Reply with ONLY a JSON object: {"narrative": "..."}

## Rules

- The entire report is in English, regardless of the user's language.
- Every number in the report comes from case.analysis - the renderer never
  recomputes and an LLM never generates numbers.
- Model-generated alternatives must be visibly attributed
  (Alternative.source) so the reader knows which options the system proposed.
