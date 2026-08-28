"""Report rendering: full audit-first structure.

Layered layout (executive -> evidence -> analysis -> audit), composed from
shared section builders so the MAUT, uncertain-bet, and allocation reports
stay consistent:

 1. Executive Summary          7. Method & Computation
 2. Decision Context           8. Results
 3. Decision Frame             9. Stakeholder Analysis
 4. Information Gathering     10. Ethics & Externalities
 5. Alternatives              11. Critique & Risks
 6. Preference Profile        12. Recommendation & Next Steps
 A1. Workflow Log             A2. Reproducibility

Every number comes from the analysis modules; the renderer never computes.
"""

from __future__ import annotations

from .analysis import AnalysisResult, MonteCarloResult
from .casefile import CaseFile, frame_item
from .decision_tree import BetAnalysisResult


def _fmt(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _md_escape(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


# ---- shared section builders ---------------------------------------------


def _conditional_note(case: CaseFile) -> list[str]:
    """When the premortem attaches high-severity risks to the winner, the
    recommendation is conditional, and the executive summary must say so."""
    high = [
        e for e in case.critique_log
        if e.get("skill") == "premortem" and e.get("severity") == "high"
    ]
    if not high:
        return []
    return [
        f"**This is a conditional recommendation**: the premortem attaches "
        f"{len(high)} high-severity risk(s) to it. It stands only with the "
        "mitigations in Recommendation & Next Steps in place - without "
        "them, the 'safe' choice can fail the very objective it protects.",
        "",
    ]


def _sec_narrative(narrative: str) -> list[str]:
    if not narrative:
        return []
    lines = []
    for paragraph in narrative.split("\n"):
        if paragraph.strip():
            lines += [paragraph.strip(), ""]
    lines += ["---", ""]
    return lines


def _sec_context(case: CaseFile, model_label: str) -> list[str]:
    lines = ["## Decision Context", ""]
    lines += ["| | |", "|---|---|"]
    rows = [
        ("Decision", case.statement),
        ("Decision maker", case.decision_maker),
        ("Type", f"{case.decision_type} (stakes: {case.stakes}, {case.reversibility})"),
        ("Analysis model", model_label),
        ("Started", case.created_at or "-"),
        ("Completed", case.completed_at or "-"),
        ("Engine", f"decision-analysis v{case.engine_version or '?'}"
                   + (f", LLM {case.model_id}" if case.model_id else ", no LLM (wizard)")),
    ]
    lines += [f"| {k} | {_md_escape(v)} |" for k, v in rows]
    lines += [""]
    if case.raw_input:
        lines += [
            "Original request, verbatim:",
            "",
            "> " + case.raw_input.replace("\n", "\n> "),
            "",
        ]
    return lines


_KIND_LABEL = {
    "initial": "initial description",
    "qa": "follow-up Q&A",
    "research": "web research",
    "inferred": "inferred",
    "user": "user",
}


def _sec_frame(case: CaseFile) -> list[str]:
    lines = ["## Decision Frame", ""]
    for key, title in (
        ("objectives", "Objectives"),
        ("hard_constraints", "Hard constraints (veto conditions)"),
        ("context_facts", "Context facts"),
    ):
        items = [frame_item(x) for x in case.frame.get(key, [])]
        if not items:
            continue
        lines += [f"### {title}", "", "| # | Item | Source |", "|---:|---|---|"]
        for i, item in enumerate(items, start=1):
            src = _KIND_LABEL.get(item["source_kind"], item["source_kind"])
            if item["source_ref"]:
                src += f' - "{_md_escape(item["source_ref"])}"'
            lines.append(f"| {i} | {_md_escape(item['text'])} | {src} |")
        lines.append("")

    checklist = case.frame.get("checklist", [])
    if checklist:
        lines += [
            "### Information checklist",
            "",
            "The sufficiency standard for this decision type; questioning "
            "continued until every in-scope slot closed.",
            "",
            "| Slot | Level | Status | Asked | Value / note |",
            "|---|---|---|---:|---|",
        ]
        for s in checklist:
            value = s.get("value") or s.get("note") or ""
            lines.append(
                f"| {s['ask_about']} | {s['level']} | **{s['status']}** "
                f"| {s.get('attempts', 0)} | {_md_escape(value)} |"
            )
        lines.append("")
        unknowns = [s for s in checklist if s["status"] == "unknown"]
        if unknowns:
            lines += [
                "Open unknowns (answered \"unknown\" or left blank - modeled "
                "as uncertainty, not resolved):",
                "",
            ]
            for s in unknowns:
                impact = s.get("impact_if_unknown") or s.get("note") or ""
                lines.append(f"- **{s['ask_about']}** - {impact}")
            lines.append("")
    return lines


def _sec_gathering(case: CaseFile) -> list[str]:
    qa_entries = [
        e for e in case.elicitation_log if e.get("method") == "frame-questions"
    ]
    facts = case.frame.get("researched_facts", [])
    if not qa_entries and not facts:
        return []
    lines = ["## Information Gathering Record", ""]
    if qa_entries:
        lines += [
            "| Round | Question | Answer | Fills slot |",
            "|---:|---|---|---|",
        ]
        for round_no, entry in enumerate(qa_entries, start=1):
            for qa in entry.get("qa", []):
                lines.append(
                    f"| {round_no} | {_md_escape(qa.get('question', ''))} "
                    f"| {_md_escape(qa.get('answer', ''))} "
                    f"| {qa.get('slot', '')} |"
                )
        lines.append("")
    if facts:
        lines += [
            "Web research (run with the user's consent):",
            "",
            "| Topic | Finding | Value | Source | Confidence |",
            "|---|---|---|---|---|",
        ]
        for f in facts:
            lines.append(
                f"| {_md_escape(f.get('topic', ''))} | {_md_escape(f.get('finding', ''))} "
                f"| {_md_escape(f.get('value', ''))} | {f.get('source', '')} "
                f"| {f.get('confidence', '')} |"
            )
        lines.append("")
    return lines


_SOURCE_LABEL = {
    "user": "provided by user",
    "extracted": "extracted from user's description",
    "generated": "proposed by the system, confirmed by user",
}


def _sec_alternatives(case: CaseFile) -> list[str]:
    lines = ["## Alternatives", ""]
    ethics_by_target = {}
    for c in case.ethics:
        ethics_by_target.setdefault(c.get("target", "general"), []).append(c)
    for alt in case.alternatives:
        status = "" if alt.status == "active" else f" ({alt.status})"
        lines += [f"### {alt.name}{status}", ""]
        lines.append(f"- Source: {_SOURCE_LABEL.get(alt.source, alt.source)}")
        if alt.description:
            lines.append(f"- {alt.description}")
        scores = case.assessments.get(alt.id, {})
        if scores:
            attrs = []
            for crit in case.criteria:
                a = scores.get(crit.id)
                if a is None:
                    continue
                value = (
                    _fmt(a.mode)
                    if a.is_point
                    else f"{_fmt(a.low)}..{_fmt(a.high)} (~{_fmt(a.mode)})"
                )
                attrs.append(f"{crit.name} = {value}")
            if attrs:
                lines.append("- Attributes: " + "; ".join(attrs))
        outcomes = case.frame.get("outcomes", {}).get(alt.id, [])
        if outcomes:
            lines.append(
                "- Outcomes: "
                + "; ".join(
                    f"{o['label']} (~{o['p']['mode']:.0f}%, payoff ~{o['payoff']['mode']:,.0f})"
                    for o in outcomes
                )
            )
        for c in ethics_by_target.get(alt.name, []):
            lines.append(f"- Ethics {c.get('severity', 'flag')}: {c.get('text', '')}")
        lines.append("")
    return lines


def _sec_preferences(case: CaseFile) -> list[str]:
    lines = ["## Preference Profile", ""]
    alloc = case.frame.get("allocation", {})
    if alloc.get("items") and not case.criteria:
        setup = next(
            (e for e in case.elicitation_log if e.get("method") == "allocation-setup"),
            {},
        )
        points = setup.get("points", {})
        lines += [
            "Item importance from swing weighting over the allocation "
            "targets (top swing = 100 points, others relative to it):",
            "",
            "| Item | Swing points | Weight |",
            "|---|---:|---:|",
        ]
        for it in sorted(alloc["items"], key=lambda it: -it["weight"]):
            pts = points.get(it["id"], "")
            pts_txt = f"{pts:.0f}" if isinstance(pts, (int, float)) else "-"
            lines.append(f"| {it['name']} | {pts_txt} | {_pct(it['weight'])} |")
        lines.append("")
        bounds = [
            f"{it['name']}: min {it['min_amount']:,.0f}"
            + (f", max {it['max_amount']:,.0f}" if it.get("max_amount") else "")
            for it in alloc["items"]
            if it.get("min_amount") or it.get("max_amount")
        ]
        if bounds:
            lines += ["Stated bounds: " + "; ".join(bounds) + ".", ""]
    if case.criteria:
        lines += [
            "Criteria weights"
            + (" (influence-weighted group)" if case.frame.get("stakeholders") else "")
            + ":",
            "",
            "| Criterion | Direction | Weight | Method |",
            "|---|---|---:|---|",
        ]
        for c in sorted(case.criteria, key=lambda c: -c.weight):
            lines.append(
                f"| {c.name} | {c.direction} | {_pct(c.weight)} | {c.weight_source} |"
            )
        lines.append("")
    checks = [
        e for e in case.elicitation_log
        if e.get("method") == "scenario-consistency-check"
    ]
    for e in checks:
        verdict = "consistent" if e.get("consistent") else "**inconsistent** (kept as stated)"
        lines += [
            f"Scenario consistency check ({e['criteria'][0]} vs "
            f"{e['criteria'][1]}): implied {e['implied']}, user chose "
            f"{e['chosen']} - {verdict}.",
            "",
        ]

    risk = case.preferences.get("risk_exponent")
    lam = case.preferences.get("loss_aversion")
    if risk or lam:
        lines += [
            "Inferred from binary bets (never asked as numbers):",
            "",
            "| Parameter | Value | Method | Confidence | Interpretation |",
            "|---|---:|---|---|---|",
        ]
        if risk:
            r = risk["value"]
            stance = (
                "risk-averse" if r < 0.95
                else ("risk-seeking" if r > 1.05 else "risk-neutral")
            )
            lines.append(
                f"| Risk exponent r | {r:.2f} | {risk.get('source', '?')} "
                f"| {risk.get('confidence', '?')} | {stance}, u(v)=v^r |"
            )
        if lam:
            lines.append(
                f"| Loss aversion λ | {lam['value']:.2f} | {lam.get('source', '?')} "
                f"| {lam.get('confidence', '?')} "
                f"| a loss weighs {lam['value']:.1f}x an equal gain |"
            )
        lines.append("")
    baseline_id = case.preferences.get("baseline_alt_id")
    if baseline_id:
        name = next(
            (a.name for a in case.alternatives if a.id == baseline_id), baseline_id
        )
        lines += [f"Reference point (status quo): **{name}**.", ""]

    for s in case.frame.get("stakeholders", []):
        weights = ", ".join(
            f"{c.name} {_pct(s['weights'][c.id])}" for c in case.criteria
        )
        lines.append(
            f"- **{s['name']}** (influence {s['influence']:.0f}/10): {weights}"
        )
    if case.frame.get("stakeholders"):
        lines.append("")
    return lines


def _sec_uncertainty_voi(case: CaseFile, result, voi) -> list[str]:
    """Consolidated uncertainty ledger + value-of-information table: every
    modeled source of uncertainty, how it enters the numbers, and whether
    resolving it before deciding is actually worth anything."""
    alt_by_id = {a.id: a for a in case.alternatives}
    crit_by_id = {c.id: c for c in case.criteria}
    lines = ["## Uncertainty & Value of Information", ""]

    # -- ledger ------------------------------------------------------------
    lines += ["### Uncertainty ledger", "",
              "| Source | Type | How it is modeled | Decision impact |",
              "|---|---|---|---|"]
    for s in case.frame.get("checklist", []):
        if s.get("status") == "unknown":
            lines.append(
                f"| {s['ask_about']} | open unknown | wide score ranges "
                f"advised where relevant | {s.get('impact_if_unknown', '')} |"
            )
    voi_by_cell = {(c.alt_id, c.crit_id): c for c in (voi.cells if voi else [])}
    for (aid, cid), cell in voi_by_cell.items():
        crit = crit_by_id.get(cid)
        alt = alt_by_id.get(aid)
        if crit is None or alt is None:
            continue
        impact = (
            f"P(decision changes if known) = {cell.switch_prob:.0%}"
            if cell.switch_prob > 0
            else "cannot change the decision"
        )
        lines.append(
            f"| Score range {cell.span[0]:g}..{cell.span[1]:g} for "
            f"'{alt.name}' on {crit.name} | score uncertainty "
            f"| PERT-sampled in Monte Carlo | {impact} |"
        )
    alt_names = {a.id: a.name for a in case.alternatives}
    for aid, rho in case.frame.get("correlation", {}).items():
        if rho > 0:
            lines.append(
                f"| Linked uncertainties within '{alt_names.get(aid, aid)}' "
                f"(ρ={rho:g}) | correlation | common-factor Gaussian copula "
                f"in Monte Carlo | widens that alternative's utility spread |"
            )
    for key, label in (("risk_exponent", "Risk exponent r"),
                       ("loss_aversion", "Loss aversion λ")):
        pref = case.preferences.get(key)
        if pref:
            lines.append(
                f"| {label} = {pref['value']:.2f} | preference inference "
                f"({pref.get('confidence', '?')} confidence) | inferred from "
                f"binary bets | alters the risk/prospect views, not the "
                f"primary ranking |"
            )
    if result is not None and result.flip_points:
        f0 = result.flip_points[0]
        crit = crit_by_id[f0.criterion_id]
        lines.append(
            f"| Weight of {crit.name} ({_pct(f0.current_weight)}) | preference "
            f"uncertainty | Dirichlet jitter in Monte Carlo | flips the winner "
            f"at {_pct(f0.flip_weight)} |"
        )
    lines.append("")

    # -- VOI ---------------------------------------------------------------
    if voi is not None:
        lines += ["### Value of resolving each uncertainty", ""]
        if voi.information_robust():
            lines += [
                "**The recommendation is information-robust**: even perfect "
                "knowledge of every scored range would essentially never "
                f"change the choice (EVPI ≈ {voi.evpi:.3f} utility, decision "
                f"unstable in {voi.overall_switch_prob:.1%} of simulations). "
                "Further research on these numbers is not worth its cost - "
                "attention belongs on the premortem risks instead.",
                "",
            ]
        else:
            lines += [
                f"Perfect information about everything at once would be worth "
                f"**{voi.evpi:.3f}** utility (the recommendation loses in "
                f"{voi.overall_switch_prob:.1%} of simulated worlds). Broken "
                "down by what you could actually go and find out:",
                "",
                "| Uncertain quantity | P(decision changes if resolved) "
                "| Expected utility gain | Verdict |",
                "|---|---:|---:|---|",
            ]
            for c in voi.cells:
                name = (
                    f"{crit_by_id[c.crit_id].name} for "
                    f"'{alt_by_id[c.alt_id].name}' ({c.span[0]:g}..{c.span[1]:g})"
                )
                verdict = (
                    "**worth resolving before deciding**"
                    if c.worth_resolving()
                    else "not worth the effort"
                )
                lines.append(
                    f"| {name} | {c.switch_prob:.0%} | {c.evppi:.3f} | {verdict} |"
                )
            lines.append("")
        lines += [
            f"_Nested Monte Carlo: {voi.n_outer} outer x {voi.n_inner} inner "
            "draws per cell; weights held at stated values (weight "
            "sensitivity is covered by the flip thresholds above)._",
            "",
        ]
    return lines


def _sec_ethics(case: CaseFile) -> list[str]:
    lines = ["## Ethics & Externalities", ""]
    if not case.ethics_checked:
        lines += ["Not assessed in this run (no LLM available for the check).", ""]
        return lines
    if not case.ethics:
        lines += [
            "Reviewed for third-party impacts, fairness, legality red flags, "
            "and irreversibility for others: **no material ethical concerns "
            "identified**.",
            "",
        ]
        return lines
    lines += ["| Target | Severity | Concern |", "|---|---|---|"]
    for c in case.ethics:
        lines.append(
            f"| {c.get('target', 'general')} | {c.get('severity', 'flag')} "
            f"| {_md_escape(c.get('text', ''))} |"
        )
    lines.append("")
    if any(c.get("severity") == "serious" for c in case.ethics):
        lines += [
            "Serious concerns were escalated into the critique log and "
            "required an explicit decision before this report could be "
            "produced.",
            "",
        ]
    return lines


def _sec_critique(case: CaseFile) -> list[str]:
    if not case.critique_log:
        return []
    lines = ["## Critique & Risks", ""]
    premortem = [e for e in case.critique_log if e.get("skill") == "premortem"]
    critic = [e for e in case.critique_log if e.get("skill") != "premortem"]
    if critic:
        lines += ["Critic and ethics findings, with how each was handled:", ""]
        for issue in critic:
            res = issue.get("resolution", "noted")
            lines.append(
                f"- [{issue.get('severity', 'note')} / {res}] {issue.get('text', '')}"
            )
        lines.append("")
    if premortem:
        lines += [
            "Premortem - assuming the recommended choice failed a year from "
            "now, the most plausible causes:",
            "",
            "| Risk | Severity | Mitigation / early warning |",
            "|---|---|---|",
        ]
        for r in premortem:
            lines.append(
                f"| {_md_escape(r.get('text', ''))} | {r.get('severity', '')} "
                f"| {_md_escape(r.get('mitigation', ''))} |"
            )
        lines.append("")
    return lines


def _sec_next_steps(case: CaseFile, fragility_note: str, voi=None) -> list[str]:
    lines = ["## Recommendation & Next Steps", ""]
    steps = []
    crit_by_id = {c.id: c for c in case.criteria}
    alt_by_id = {a.id: a for a in case.alternatives}
    for c in (voi.cells if voi else []):
        if c.worth_resolving():
            steps.append(
                f"**Resolve before deciding**: {crit_by_id[c.crit_id].name} "
                f"for '{alt_by_id[c.alt_id].name}' - there is a "
                f"{c.switch_prob:.0%} chance the decision changes once known."
            )
    unknowns = [
        s for s in case.frame.get("checklist", []) if s.get("status") == "unknown"
    ]
    for s in unknowns:
        steps.append(
            f"Resolve before committing if practical: **{s['ask_about']}** "
            f"({s.get('impact_if_unknown', '')})."
        )
    for r in (e for e in case.critique_log if e.get("skill") == "premortem"):
        if r.get("mitigation"):
            steps.append(f"Mitigation: {r['mitigation']}")
    for issue in case.critique_log:
        if issue.get("resolution") == "accepted-risk":
            steps.append(
                f"Accepted risk to monitor: {issue.get('text', '')}"
            )
    if fragility_note:
        steps.append(fragility_note)
    if not steps:
        steps.append(
            "No open unknowns or fragile assumptions remain; proceed when ready."
        )
    lines += [f"- {s}" for s in steps]
    lines += [
        "",
        "Revisit this analysis if a hard constraint changes, a key unknown "
        "resolves differently than assumed, or a premortem early-warning "
        "sign appears.",
        "",
    ]
    return lines


def _sec_trace(case: CaseFile) -> list[str]:
    if not case.trace:
        return []
    lines = [
        "## Appendix: Workflow Log",
        "",
        "| Time | Phase | Actor | Action |",
        "|---|---|---|---|",
    ]
    for e in case.trace:
        lines.append(
            f"| {e.get('at', '')} | {e.get('phase', '')} | {e.get('actor', '')} "
            f"| {_md_escape(e.get('action', ''))} |"
        )
    lines.append("")
    if case.skill_log:
        lines += [
            "Runtime skill requests (allow/deny policy):",
            "",
            "| Requested | Decision | Note |",
            "|---|---|---|",
        ]
        for entry in case.skill_log:
            lines.append(
                f"| {entry.get('requested', '')} | {entry.get('decision', '')} "
                f"| {_md_escape(entry.get('note', ''))} |"
            )
        lines.append("")
    return lines


def _sec_repro(case: CaseFile, details: list[str]) -> list[str]:
    lines = [
        "## Appendix: Reproducibility",
        "",
        f"- Case file: `cases/{case.slug()}/case.json` (complete state; "
        "rerunning the analysis on it reproduces every number).",
        f"- Engine decision-analysis v{case.engine_version or '?'}"
        + (f"; LLM {case.model_id}." if case.model_id else "; wizard mode (no LLM)."),
    ]
    lines += [f"- {d}" for d in details]
    lines.append("")
    return lines


# ---- MAUT report ----------------------------------------------------------


def render_report(
    case: CaseFile,
    result: AnalysisResult,
    mc: MonteCarloResult | None = None,
    chart_files: dict[str, str] | None = None,
    narrative: str = "",
    voi=None,
) -> str:
    charts = chart_files or {}
    alt_by_id = {a.id: a for a in case.alternatives}
    crit_by_id = {c.id: c for c in case.criteria}
    winner = alt_by_id[result.ranking[0]]
    runner_up = alt_by_id[result.ranking[1]] if len(result.ranking) > 1 else None

    lines: list[str] = [f"# Decision Report: {case.statement}", ""]
    add = lines.append
    ext = lines.extend

    # 1. Executive summary
    ext(_sec_narrative(narrative))
    add("## Executive Summary")
    add("")
    margin = ""
    if runner_up is not None:
        gap = result.utilities[winner.id] - result.utilities[runner_up.id]
        margin = f" (margin over **{runner_up.name}**: {_fmt(gap)})"
    confidence = ""
    if mc is not None:
        confidence = (
            f" Across {mc.n_samples:,} simulations of the stated uncertainty "
            f"it finishes first in {_pct(mc.p_best[winner.id])} of runs."
        )
    add(
        f"**Recommendation: {winner.name}** with weighted utility "
        f"{_fmt(result.utilities[winner.id])}{margin}.{confidence}"
    )
    fragility_note = ""
    if result.flip_points:
        f0 = result.flip_points[0]
        crit0 = crit_by_id[f0.criterion_id]
        add(
            f"Most sensitive assumption: the weight of **{crit0.name}** - if it "
            f"{f0.direction}s from {_pct(f0.current_weight)} past "
            f"{_pct(f0.flip_weight)}, the recommendation flips to "
            f"**{alt_by_id[f0.new_winner_id].name}**."
        )
        if abs(f0.flip_weight - f0.current_weight) < 0.10:
            fragility_note = (
                f"The conclusion is fragile to the weight of **{crit0.name}** "
                "- if any open unknown bears on it, resolving that first is "
                "worth more than further analysis."
            )
    else:
        add("No single-criterion weight change flips this recommendation.")
    add("")
    ext(_conditional_note(case))

    # 2-6: context, frame, gathering, alternatives, preferences
    ext(_sec_context(case, "MAUT (multi-attribute utility) + Monte Carlo"))
    ext(_sec_frame(case))
    ext(_sec_gathering(case))
    ext(_sec_alternatives(case))
    ext(_sec_preferences(case))

    # 7. Method & computation
    add("## Method & Computation")
    add("")
    add(
        "- Ranged assessments collapse to their PERT mean "
        "(low + 4x mode + high) / 6 for the deterministic ranking."
    )
    add(
        "- Each criterion normalized against its range envelope (lowest "
        "'low' to highest 'high' across alternatives), preserving the "
        "magnitude of differences; \"min\" criteria inverted; ties "
        "contribute 0.5. Utility = weighted sum."
    )
    if mc is not None:
        add(
            f"- Monte Carlo: {mc.n_samples:,} draws; assessments sampled from "
            "PERT distributions, weights jittered with a Dirichlet centered "
            "on the stated weights (concentration 120), seed 7."
        )
    add("- Weight-flip thresholds solved in closed form (utilities are linear "
        "in any single weight with the others rescaled).")
    correlation = case.frame.get("correlation", {})
    if any(correlation.values()):
        alt_by_id_m = {a.id: a for a in case.alternatives}
        pairs = ", ".join(
            f"{alt_by_id_m[aid].name} (ρ={rho:g})"
            for aid, rho in correlation.items()
        )
        add(
            "- Correlated uncertainties: within-alternative score ranges "
            f"sampled with a common-factor Gaussian copula for {pairs} - "
            "one setback drags that alternative's linked scores together; "
            "marginal distributions unchanged."
        )
    add("")
    add("Normalized scores (0 = worst plausible in the stated ranges, "
        "1 = best plausible):")
    add("")
    header = "| Alternative | " + " | ".join(c.name for c in case.criteria) + " |"
    add(header)
    add("|---" * (len(case.criteria) + 1) + "|")
    for alt_id in result.ranking:
        row = " | ".join(_fmt(result.normalized[alt_id][c.id]) for c in case.criteria)
        add(f"| {alt_by_id[alt_id].name} | {row} |")
    add("")
    if "contributions" in charts:
        add(f"![Weighted utility by criterion]({charts['contributions']})")
        add("")

    # 8. Results
    add("## Results")
    add("")
    add("| Rank | Alternative | Weighted Utility |")
    add("|---:|---|---:|")
    for i, alt_id in enumerate(result.ranking, start=1):
        add(f"| {i} | {alt_by_id[alt_id].name} | {_fmt(result.utilities[alt_id])} |")
    add("")
    if mc is not None:
        add("Under uncertainty:")
        add("")
        add("| Alternative | P(best) | Mean rank | Mean utility |")
        add("|---|---:|---:|---:|")
        for alt_id in sorted(mc.p_best, key=mc.p_best.get, reverse=True):
            add(
                f"| {alt_by_id[alt_id].name} | {_pct(mc.p_best[alt_id])} "
                f"| {mc.mean_rank[alt_id]:.2f} | {_fmt(mc.mean_utility[alt_id])} |"
            )
        add("")
        if "rank_probability" in charts:
            add(f"![Probability of each final rank]({charts['rank_probability']})")
            add("")
        if "utility_distribution" in charts:
            add(f"![Utility distributions]({charts['utility_distribution']})")
            add("")
    # What would change the answer - grouped per challenger.
    if result.flip_points:
        add("### What would change the answer")
        add("")
        by_challenger: dict[str, list] = {}
        for f in result.flip_points:
            by_challenger.setdefault(f.new_winner_id, []).append(f)
        for challenger_id, flips in by_challenger.items():
            add(f"**{alt_by_id[challenger_id].name}** overtakes {winner.name} if:")
            add("")
            for f in flips:
                crit = crit_by_id[f.criterion_id]
                add(
                    f"- the weight of {crit.name} {f.direction}s from "
                    f"{_pct(f.current_weight)} past {_pct(f.flip_weight)}"
                )
            add("")
        if "flip_thresholds" in charts:
            add(f"![Weight change needed to flip the winner]({charts['flip_thresholds']})")
            add("")
    if mc is not None and mc.ce_utility is not None:
        ce_order = sorted(mc.ce_utility, key=mc.ce_utility.get, reverse=True)
        r = case.preferences["risk_exponent"]["value"]
        add(
            f"Risk-adjusted view (r = {r:.2f}; r < 1 penalizes uncertain "
            "options): " + " > ".join(alt_by_id[a].name for a in ce_order) + "."
        )
        if ce_order[0] != result.ranking[0]:
            add("Risk adjustment changes the top choice - your aversion to "
                "uncertainty matters here.")
        add("")
    if mc is not None and mc.prospect_value is not None:
        lam = case.preferences["loss_aversion"]["value"]
        base = alt_by_id[mc.baseline_id].name
        add(f"Loss-aversion view (losses vs '{base}' weighted {lam:.1f}x):")
        add("")
        add("| Alternative | Expected prospect value vs baseline |")
        add("|---|---:|")
        for alt_id in sorted(mc.prospect_value, key=mc.prospect_value.get, reverse=True):
            add(f"| {alt_by_id[alt_id].name} | {mc.prospect_value[alt_id]:+.3f} |")
        add("")

    # 8.5 Uncertainty ledger + VOI
    ext(_sec_uncertainty_voi(case, result, voi))
    if voi is not None and "voi" in charts:
        add(f"![Value of information]({charts['voi']})")
        add("")

    # 9. Stakeholders
    views = case.analysis.get("stakeholder_views", {})
    if views:
        add("## Stakeholder Analysis")
        add("")
        add("| Stakeholder | Their top choice | Agrees with group? |")
        add("|---|---|---|")
        for name, v in views.items():
            top = alt_by_id[v["ranking"][0]].name
            add(f"| {name} | {top} | {'yes' if v['agrees_with_group'] else '**no**'} |")
        add("")
        if any(not v["agrees_with_group"] for v in views.values()):
            add(
                "At least one stakeholder individually prefers a different "
                "option - worth an explicit conversation before committing."
            )
            add("")

    # 10-12 + appendices
    ext(_sec_ethics(case))
    ext(_sec_critique(case))
    ext(_sec_next_steps(case, fragility_note, voi))
    ext(_sec_trace(case))
    ext(_sec_repro(
        case,
        [f"Monte Carlo: n = {mc.n_samples:,}, seed 7." if mc else "Deterministic only.",
         "VOI: nested Monte Carlo, seed 11." if voi else "VOI: not computed."],
    ))
    return "\n".join(lines)


# ---- uncertain-bet report -------------------------------------------------


def render_bet_report(
    case: CaseFile,
    result: BetAnalysisResult,
    chart_files: dict[str, str] | None = None,
    narrative: str = "",
) -> str:
    charts = chart_files or {}
    alt_by_id = {a.id: a for a in case.alternatives}
    winner = alt_by_id[result.ranking[0]]

    lines: list[str] = [f"# Decision Report: {case.statement}", ""]
    add = lines.append
    ext = lines.extend

    ext(_sec_narrative(narrative))
    add("## Executive Summary")
    add("")
    ce = result.certainty_equivalent[winner.id]
    add(
        f"**Recommendation: {winner.name}** - the highest expected value to "
        f"you given your risk attitude, best in {_pct(result.p_best[winner.id])} "
        f"of {result.n_samples:,} simulations. To you it is worth about a "
        f"sure **{ce:,.0f}** (certainty equivalent)."
    )
    add("")
    ext(_conditional_note(case))

    ext(_sec_context(case, "Expected utility (prospect-style value function)"))
    ext(_sec_frame(case))
    ext(_sec_gathering(case))
    ext(_sec_alternatives(case))
    ext(_sec_preferences(case))

    add("## Method & Computation")
    add("")
    add(
        "- Value function v(x) = x^r for gains, -λ(-x)^r for losses; "
        "reference point = status quo (0)."
    )
    add(
        f"- Monte Carlo: {result.n_samples:,} draws; probabilities and payoffs "
        "sampled from PERT distributions, probabilities renormalized within "
        "each draw; seed 7."
    )
    add("- Certainty equivalents invert the value function on the mean "
        "prospect value, in payoff units.")
    add("")

    add("## Results")
    add("")
    add("| Rank | Alternative | Certainty equivalent | Risk-neutral EV | P(best) |")
    add("|---:|---|---:|---:|---:|")
    for i, aid in enumerate(result.ranking, start=1):
        add(
            f"| {i} | {alt_by_id[aid].name} "
            f"| {result.certainty_equivalent[aid]:,.0f} "
            f"| {result.expected_payoff[aid]:,.0f} "
            f"| {_pct(result.p_best[aid])} |"
        )
    add("")
    if "rank_probability" in charts:
        add(f"![Probability of each final rank]({charts['rank_probability']})")
        add("")
    if "risk_profile" in charts:
        add(f"![Risk profiles]({charts['risk_profile']})")
        add(
            "_Each curve: the chance of ending up at or below a given payoff. "
            "A curve further right and steeper near the top is better; the "
            "left tail is your downside._"
        )
        add("")
    ev_order = sorted(
        result.expected_payoff, key=result.expected_payoff.get, reverse=True
    )
    if ev_order[0] != result.ranking[0]:
        add(
            f"Note: on raw expected payoff alone, **{alt_by_id[ev_order[0]].name}** "
            "would rank first. The difference is your own risk attitude - the "
            "recommendation already accounts for it."
        )
        add("")

    ext(_sec_ethics(case))
    ext(_sec_critique(case))
    ext(_sec_next_steps(case, ""))
    ext(_sec_trace(case))
    ext(_sec_repro(case, [f"Monte Carlo: n = {result.n_samples:,}, seed 7."]))
    return "\n".join(lines)


# ---- allocation report ----------------------------------------------------


def render_allocation_report(
    case: CaseFile,
    result,  # AllocationResult
    alphas: tuple[float, ...],
    narrative: str = "",
    chart_files: dict[str, str] | None = None,
) -> str:
    charts = chart_files or {}
    alloc = case.frame.get("allocation", {})
    name_by_id = {it["id"]: it["name"] for it in alloc.get("items", [])}
    weight_by_id = {it["id"]: it["weight"] for it in alloc.get("items", [])}
    total = result.total

    lines: list[str] = [f"# Decision Report: {case.statement}", ""]
    add = lines.append
    ext = lines.extend

    ext(_sec_narrative(narrative))
    add("## Executive Summary")
    add("")
    ordered = sorted(result.recommended.items(), key=lambda kv: -kv[1])
    add(
        "**Recommended split**: "
        + ", ".join(
            f"{name_by_id.get(iid, iid)} {amount / total:.0%}"
            for iid, amount in ordered
        )
        + f" of {total:,.0f}."
    )
    add("")
    ext(_conditional_note(case))

    ext(_sec_context(case, "Deterministic allocation optimizer (power utility)"))
    ext(_sec_frame(case))
    ext(_sec_gathering(case))
    ext(_sec_alternatives(case))
    ext(_sec_preferences(case))

    add("## Method & Computation")
    add("")
    add(
        "- Maximize sum of w_i * x_i^alpha subject to the total, per-item "
        "bounds, and unit granularity; closed-form power-utility optimum, "
        "active-set for bounds, marginal-utility rounding."
    )
    add(
        "- Importance weights w_i from swing weighting over the items; "
        "alpha is the diminishing-returns strength."
    )
    add("")

    add("## Results")
    add("")
    add(f"Total allocated: **{total:,.0f}** (unit {result.unit:,.0f}).")
    add("")
    add("| Item | Importance | Amount | Share | Bound |")
    add("|---|---:|---:|---:|---|")
    for iid, amount in ordered:
        bound = result.binding.get(iid, "")
        add(
            f"| {name_by_id.get(iid, iid)} | {_pct(weight_by_id.get(iid, 0.0))} "
            f"| {amount:,.0f} | {amount / total:.0%} | {bound} |"
        )
    add("")
    if "allocation_split" in charts:
        add(f"![Recommended split]({charts['allocation_split']})")
        add("")
    add("Robustness across the diminishing-returns assumption (smaller alpha "
        "= spread more evenly):")
    add("")
    header = "| Item | " + " | ".join(f"alpha {a}" for a in alphas) + " |"
    add(header)
    add("|---" * (len(alphas) + 1) + "|")
    for iid, _ in ordered:
        cells = " | ".join(f"{result.splits[a].get(iid, 0.0):,.0f}" for a in alphas)
        add(f"| {name_by_id.get(iid, iid)} | {cells} |")
    add("")
    if "allocation_robustness" in charts:
        add(f"![Split under different diminishing-returns strengths]"
            f"({charts['allocation_robustness']})")
        add("")
    if result.binding:
        add(
            "Binding constraints: "
            + "; ".join(
                f"{name_by_id.get(iid, iid)} at its {kind}"
                for iid, kind in result.binding.items()
            )
            + ". Relaxing these would change the split."
        )
        add("")

    ext(_sec_ethics(case))
    ext(_sec_critique(case))
    ext(_sec_next_steps(case, ""))
    ext(_sec_trace(case))
    ext(_sec_repro(case, ["Optimizer is fully deterministic (no sampling)."]))
    return "\n".join(lines)
