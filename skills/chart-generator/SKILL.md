---
name: chart-generator
description: Render the report's SVG charts - weighted contribution stacks, rank-probability distribution, weight-flip thresholds. Pure stdlib SVG in decision_analysis/charts.py, following the reference data-viz palette and mark rules.
case_file_reads: [criteria, alternatives, analysis]
case_file_writes: []
implementation: decision_analysis/charts.py
---

# Chart generator skill

## Charts (one function each in charts.py)

1. `stacked_contribution_chart` - horizontal stacked bars: each criterion's
   weighted contribution to each alternative's utility. Categorical hues in
   fixed slot order (never cycled), legend always present, utility direct-
   labeled at the bar end.
2. `rank_probability_chart` - per-alternative distribution over final ranks
   from Monte Carlo. Ordinal blue ramp, dark = rank 1; P(best) labeled.
3. `flip_threshold_chart` - per criterion, the bar from current weight to
   the nearest flip point on a 0-100% axis; current weight marked with an
   ink tick; new winner labeled.

## Rules

- Numbers come from case.analysis only; charts never recompute.
- Light mode with the surface painted explicitly (standalone SVG files).
- Text in ink tokens, never series colors; 2px surface gaps between stacked
  fills; thin marks; muted hairline grid.
- Long names clipped with an ellipsis; all text XML-escaped.
- Files land in cases/<slug>-charts/ and are embedded in the Markdown report.
