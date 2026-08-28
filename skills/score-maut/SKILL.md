---
name: score-maut
description: Deterministic MAUT scoring - per-criterion min-max normalization, weighted-sum utilities, ranking, and weight-flip sensitivity. Pure computation; implemented in decision_analysis/analysis.py.
case_file_reads: [criteria, alternatives, assessments]
case_file_writes: [analysis]
implementation: decision_analysis/analysis.py::analyze
---

# MAUT scoring skill

## Contract

- Input: complete assessment matrix (every active alternative scored on every
  criterion; cells are three-point estimates, a point value being
  low == mode == high), plus criteria weights summing to 1.
- Processing (all in `decision_analysis/analysis.py`, never in an LLM):
  1. Collapse each cell to its PERT mean (low + 4*mode + high) / 6.
     (M2 replaces this collapse with Monte Carlo sampling.)
  2. Min-max normalize each criterion across alternatives to [0, 1];
     "min"-direction criteria inverted; an all-tied criterion contributes 0.5.
  3. Utility per alternative = weighted sum. Rank descending.
  4. Weight-flip sensitivity: for each criterion, solve (linearly, in closed
     form) for the nearest weight - others rescaled proportionally - at which
     the winner is overtaken, in both directions.
- Output written to case.analysis: utilities, ranking, flip_points.

## Guardrail

Refuse to run on an incomplete matrix (`CaseFile.is_matrix_complete()`); the
orchestrator must return to ASSESS instead.
