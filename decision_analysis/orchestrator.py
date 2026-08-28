"""M1 orchestrator: a structured CLI wizard driving the deterministic engine.

In no-LLM mode (NullProvider) the wizard collects what the LLM will later
infer from natural language: the decision statement, alternatives, criteria,
swing ranking, and scores. The phase sequence mirrors the PLAN state machine:
INTAKE -> FRAME -> ELICIT -> ASSESS -> ANALYZE -> REPORT.
"""

from __future__ import annotations

import os

from pathlib import Path

from datetime import datetime

from . import __version__, channel
from .analysis import analyze, monte_carlo
from .casefile import Assessment, Alternative, CaseFile, Criterion
from .charts import (
    flip_threshold_chart,
    rank_probability_chart,
    stacked_contribution_chart,
)
from .elicitation import (
    CertaintyEquivalentSession,
    LossAversionSession,
    swing_weights,
)
from .report import render_report

# Overridable so a hosted deployment can point at a persistent volume
# (e.g. DA_CASES_DIR=/data/cases on Railway).
CASES_DIR = Path(os.environ.get("DA_CASES_DIR", "cases"))


def _case_dir(case: CaseFile) -> Path:
    """Every case gets its own folder: cases/<caseid>/ holding case.json,
    report.md, report.html, charts/, and (web sessions) the transcript."""
    path = CASES_DIR / case.slug()
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---- input helpers (all user input routes through the channel) -----------


def _ask(prompt: str, kind: str = "text", **meta) -> str:
    return channel.ask(prompt, kind, **meta)


def _ask_nonempty(prompt: str, kind: str = "text", **meta) -> str:
    while True:
        value = _ask(prompt, kind, **meta)
        if value:
            return value
        print("  Please enter a value.")


def _ask_float(prompt: str, lo: float | None = None, hi: float | None = None,
               kind: str = "number", **meta) -> float:
    while True:
        raw = _ask(prompt, kind, min=lo, max=hi, **meta)
        try:
            value = float(raw)
        except ValueError:
            print("  Please enter a number.")
            continue
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            print(f"  Please enter a number between {lo} and {hi}.")
            continue
        return value


def _parse_assessment(raw: str) -> Assessment | None:
    """Accept '5' (point) or '3/5/6' (low/most-likely/high)."""
    parts = [p.strip() for p in raw.replace(",", "/").split("/")]
    try:
        numbers = [float(p) for p in parts if p]
    except ValueError:
        return None
    if len(numbers) == 1:
        v = numbers[0]
        return Assessment(low=v, mode=v, high=v)
    if len(numbers) == 3:
        low, mode, high = sorted(numbers)
        return Assessment(low=low, mode=mode, high=high)
    return None


# ---- wizard phases -------------------------------------------------------


def _intake(case: CaseFile) -> None:
    print("\n=== 1. Decision ===")
    case.statement = _ask_nonempty("What decision are you trying to make? > ")


_TYPES = ["discrete-choice", "go-no-go", "allocation", "timing", "uncertain-bet"]


def _choose_type(case: CaseFile) -> None:
    print("\nDecision type:")
    labels = [
        "choosing among options (default)",
        "yes/no: do it or not",
        "allocating a resource across items",
        "now vs later",
        "a gamble with uncertain outcomes",
    ]
    for i, (t, label) in enumerate(zip(_TYPES, labels), start=1):
        print(f"  {i}. {t} - {label}")
    raw = _ask(
        "Pick (Enter = 1) > ",
        kind="choice",
        choices=[
            {"value": str(i), "label": f"{t} - {label}"}
            for i, (t, label) in enumerate(zip(_TYPES, labels), start=1)
        ],
        skip_label="discrete-choice (default)",
    ).strip()
    if raw.isdigit() and 1 <= int(raw) <= len(_TYPES):
        case.decision_type = _TYPES[int(raw) - 1]


def _frame_alternatives(case: CaseFile) -> None:
    print("\n=== 2. Alternatives ===")
    print("Enter each alternative on its own line. Blank line to finish (min 2).")
    while True:
        name = _ask(f"Alternative {len(case.alternatives) + 1} > ")
        if not name:
            if len(case.alternatives) >= 2:
                break
            print("  At least 2 alternatives are required.")
            continue
        case.alternatives.append(Alternative(id=f"a{len(case.alternatives) + 1}", name=name))


def _frame_criteria(case: CaseFile) -> None:
    print("\n=== 3. Criteria ===")
    print("Enter each criterion as 'name' or 'name, min' if less is better")
    print("(default is max = more is better). Blank line to finish (min 2).")
    while True:
        raw = _ask(f"Criterion {len(case.criteria) + 1} > ")
        if not raw:
            if len(case.criteria) >= 2:
                break
            print("  At least 2 criteria are required.")
            continue
        head, _, tail = raw.rpartition(",")
        if head and tail.strip().lower() in ("max", "min"):
            name, direction = head, tail.strip().lower()
        else:
            name, direction = raw, "max"
        case.criteria.append(
            Criterion(id=f"c{len(case.criteria) + 1}", name=name.strip(), direction=direction)
        )


def _collect_swing_points(
    labeled: list[tuple[str, str]], who: str = ""
) -> dict[str, float]:
    """Interactive swing weighting over (id, label) pairs; returns raw points.

    Reused for criteria weights (single user), per-stakeholder weights, and
    allocation-item importance.
    """
    suffix = f" - answering as {who}" if who else ""
    print(f"\n=== Swing weighting{suffix} ===")
    print("Imagine everything at its WORST level. You may fix ONE item at a")
    print("time from worst to best; pick them in the order that helps most.\n")
    remaining = list(labeled)
    order: list[tuple[str, str]] = []
    while remaining:
        if len(remaining) == 1:
            order.append(remaining.pop())
            break
        for i, (_, label) in enumerate(remaining, start=1):
            print(f"  {i}. {label}")
        rank_label = "most" if not order else "next most"
        idx = int(
            _ask_float(
                f"Which swing matters {rank_label}? (number) > ",
                1,
                len(remaining),
                kind="choice",
                choices=[
                    {"value": str(i), "label": label}
                    for i, (_, label) in enumerate(remaining, start=1)
                ],
            )
        )
        order.append(remaining.pop(idx - 1))

    points: dict[str, float] = {order[0][0]: 100.0}
    print(f"\n'{order[0][1]}' gets 100 points. Score the rest relative to it (0-100):")
    for iid, label in order[1:]:
        points[iid] = _ask_float(f"  {label} > ", 0.0, 100.0)
    return points


def _elicit_swing_weights(case: CaseFile) -> None:
    labeled = [(c.id, f"{c.name} ({c.direction})") for c in case.criteria]
    points = _collect_swing_points(labeled)
    weights = swing_weights(points)
    for crit in case.criteria:
        crit.weight = weights[crit.id]
        crit.weight_source = "swing"
    case.elicitation_log.append({"method": "swing-weighting", "points": points})
    case.log_event("preferences", "elicit-swing-weights", "criteria weights elicited")
    print("\nWeights: " + ", ".join(
        f"{c.name} {c.weight:.1%}"
        for c in sorted(case.criteria, key=lambda c: -c.weight)
    ))


def _elicit_stakeholder_weights(case: CaseFile) -> None:
    """Multi-stakeholder mode: each stakeholder gets their own swing
    weighting pass (answered by them, or by the user as an honest proxy).
    Group weights = influence-weighted average, written into the criteria so
    the whole downstream pipeline runs on the group view unchanged; the
    per-stakeholder views are kept for the disagreement analysis."""
    print("\n=== Stakeholders ===")
    print("Name each stakeholder whose preferences should count, with an")
    print("influence weight 1-10 (how much say they have). Blank name = done.")
    stakeholders: list[dict] = []
    while len(stakeholders) < 6:
        name = _ask(f"Stakeholder {len(stakeholders) + 1} name > ").strip()
        if not name:
            if len(stakeholders) >= 2:
                break
            print("  Model at least 2 stakeholders (else use the single-user path).")
            continue
        influence = _ask_float(f"  influence of {name} (1-10) > ", 1.0, 10.0)
        stakeholders.append({"id": f"s{len(stakeholders) + 1}", "name": name,
                             "influence": influence})

    labeled = [(c.id, f"{c.name} ({c.direction})") for c in case.criteria]
    for s in stakeholders:
        print(f"\n--- Preferences of {s['name']} ---")
        print("(Answer as they would; if they are present, hand them the keyboard.)")
        points = _collect_swing_points(labeled, who=s["name"])
        s["weights"] = swing_weights(points)
        s["points"] = points

    total_influence = sum(s["influence"] for s in stakeholders)
    for crit in case.criteria:
        crit.weight = sum(
            s["influence"] / total_influence * s["weights"][crit.id]
            for s in stakeholders
        )
        crit.weight_source = "group-swing"
    case.frame["stakeholders"] = stakeholders
    case.elicitation_log.append(
        {"method": "stakeholder-swing-weighting",
         "stakeholders": [
             {"name": s["name"], "influence": s["influence"], "points": s["points"]}
             for s in stakeholders
         ]}
    )
    print("\nGroup weights (influence-weighted): " + ", ".join(
        f"{c.name} {c.weight:.1%}"
        for c in sorted(case.criteria, key=lambda c: -c.weight)
    ))


def _elicit_weights(case: CaseFile) -> None:
    if _yes(
        "\nDo several stakeholders' preferences need to be modeled "
        "separately? (y/N) > "
    ):
        _elicit_stakeholder_weights(case)
    else:
        _elicit_swing_weights(case)


def _assess(case: CaseFile) -> None:
    from .checklists import unknown_slots

    print("\n=== 5. Scoring ===")
    print("Enter a value ('5') or a low/most-likely/high range ('3/5/6').")
    print("Any consistent scale works per criterion (money, minutes, 1-10, ...).\n")
    unknowns = unknown_slots(case)
    if unknowns:
        topics = ", ".join(s["ask_about"] for s in unknowns)
        print(f"Note: earlier you marked as unknown: {topics}.")
        print("Where a score depends on those, use a WIDE low/high range - the")
        print("uncertainty will show up honestly in the ranking probabilities.\n")
    facts = case.frame.get("researched_facts", [])
    if facts:
        print("Researched facts you can lean on:")
        for f in facts:
            value = f" [{f['value']}]" if f.get("value") else ""
            print(f"  - {f.get('finding', '')}{value}")
        print()
    for crit in case.criteria:
        better = "higher is better" if crit.direction == "max" else "lower is better"
        remaining = [
            a for a in case.active_alternatives()
            if crit.id not in case.assessments.get(a.id, {})
        ]
        if not remaining:
            continue
        print(f"-- {crit.name} ({better}) --")
        for alt in remaining:
            while True:
                assessment = _parse_assessment(
                    _ask_nonempty(
                        f"  {alt.name} > ",
                        kind="assessment",
                        alternative=alt.name,
                        criterion=crit.name,
                    )
                )
                if assessment is not None:
                    case.set_assessment(alt.id, crit.id, assessment)
                    break
                print("  Use '5' or '3/5/6'.")
    if case.is_matrix_complete():
        case.log_event("assessment", "user", "score matrix completed")


def _yes(prompt: str) -> bool:
    return _ask(prompt, kind="yesno").strip().lower() in ("y", "yes")


def _try_llm_extra(label: str, fn):
    """Run an optional LLM step (ethics, premortem, narrative); a failure
    degrades to a warning instead of killing a run whose analysis is done."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - deliberately broad here
        print(f"  [{label} step failed and was skipped: {exc}]")
        return None


def _new_case(model_id: str = "") -> CaseFile:
    case = CaseFile()
    case.created_at = datetime.now().isoformat(timespec="seconds")
    case.engine_version = __version__
    case.model_id = model_id
    return case


def _ask_decision_maker(case: CaseFile) -> None:
    name = _ask("Decision maker name (Enter = anonymous) > ").strip()
    if name:
        case.decision_maker = name


def _ensure_baseline(case: CaseFile) -> None:
    """go-no-go and timing decisions are meaningless without the do-nothing
    baseline; code enforces its presence rather than trusting generation."""
    if case.decision_type not in ("go-no-go", "timing"):
        return
    print("\nA credible analysis of this decision type needs the do-nothing baseline.")
    for i, alt in enumerate(case.active_alternatives(), start=1):
        print(f"  {i}. {alt.name}")
    raw = _ask(
        "Which one is 'keep things as they are'? (number, Enter = add it) > ",
        kind="choice",
        choices=[
            {"value": str(i), "label": alt.name}
            for i, alt in enumerate(case.active_alternatives(), start=1)
        ],
        skip_label="None of these - add a status-quo baseline",
    ).strip()
    alts = case.active_alternatives()
    if raw.isdigit() and 1 <= int(raw) <= len(alts):
        baseline = alts[int(raw) - 1]
    else:
        baseline = Alternative(
            id=f"a{len(case.alternatives) + 1}",
            name="Do nothing (status quo)",
            description="Keep things as they are - the comparison baseline",
            source="generated",
        )
        case.alternatives.append(baseline)
        print(f"  Added '{baseline.name}'.")
    case.preferences["baseline_alt_id"] = baseline.id


def _elicit_outcomes(case: CaseFile) -> None:
    """Uncertain-bet: outcomes per alternative (probability x payoff, both
    rangeable). See skills/elicit-outcomes/SKILL.md for the contract."""
    print("\n=== Outcomes ===")
    print("For each alternative, list its possible outcomes.")
    print("Probability: '20' or '10/20/35' (percent; rough is fine - they are")
    print("renormalized). Payoff: net result in one consistent unit, losses")
    print("NEGATIVE, point or range. Blank label = done with that alternative.\n")
    outcomes: dict[str, list[dict]] = {}
    for alt in case.active_alternatives():
        print(f"-- {alt.name} --")
        rows: list[dict] = []
        if _yes("  Is this a 'nothing changes' option (one outcome, payoff 0)? (y/N) > "):
            rows.append(
                {"label": "no change",
                 "p": {"low": 100.0, "mode": 100.0, "high": 100.0},
                 "payoff": {"low": 0.0, "mode": 0.0, "high": 0.0}}
            )
        while len(rows) < 8:
            label = _ask(f"  Outcome {len(rows) + 1} label > ").strip()
            if not label:
                if rows:
                    break
                print("  At least one outcome is required.")
                continue
            p = None
            while p is None:
                p = _parse_assessment(
                    _ask_nonempty("    probability (%) > ", kind="assessment")
                )
            pay = None
            while pay is None:
                pay = _parse_assessment(
                    _ask_nonempty("    payoff > ", kind="assessment")
                )
            rows.append(
                {"label": label,
                 "p": {"low": p.low, "mode": p.mode, "high": p.high},
                 "payoff": {"low": pay.low, "mode": pay.mode, "high": pay.high}}
            )
        mode_sum = sum(r["p"]["mode"] for r in rows)
        if abs(mode_sum - 100.0) > 15.0 and len(rows) > 1:
            print(f"  (Stated probabilities sum to {mode_sum:.0f}%; they will be renormalized.)")
        outcomes[alt.id] = rows
    case.frame["outcomes"] = outcomes
    case.elicitation_log.append(
        {"method": "outcome-elicitation",
         "counts": {aid: len(rows) for aid, rows in outcomes.items()}}
    )


def _elicit_uncertainty(case: CaseFile, ask_baseline: bool = True) -> None:
    """Optional round: risk attitude, loss aversion, and (for MAUT paths) a
    baseline alternative, inferred from quick binary bets."""
    print("\n=== Risk & loss attitude ===")
    default_yes = case.decision_type == "uncertain-bet"
    if default_yes:
        print("(For a bet decision these preferences drive the analysis itself.)")
        prompt = "Answer a few quick yes/no bets to model your risk attitude? (Y/n) > "
        if _ask(prompt).strip().lower() in ("n", "no"):
            return
    elif not _yes("Answer a few quick yes/no bets to model your risk attitude? (y/N) > "):
        return

    print("\nBet 1 of 2 - risk attitude. Imagine a one-time gamble:")
    print("50% chance you win 10,000 (your currency), 50% you win nothing.")
    ce = CertaintyEquivalentSession(stake=10000.0)
    while not ce.done:
        offer = ce.next_offer()
        ce.record(_yes(f"  Take a SURE {offer:,.0f} instead of the gamble? (y/n) > "))
    r = ce.risk_exponent
    case.preferences["risk_exponent"] = {
        "value": round(r, 3),
        "source": "certainty-equivalent-bisection",
        "confidence": "medium",
    }
    stance = "risk-averse" if r < 0.95 else ("risk-seeking" if r > 1.05 else "roughly risk-neutral")
    print(f"  -> You appear {stance} (r = {r:.2f}).")

    print("\nBet 2 of 2 - loss aversion. A coin-flip deal:")
    print("50% chance you WIN 1,000; 50% chance you LOSE the stated amount.")
    la = LossAversionSession(gain=1000.0)
    while not la.done:
        offer = la.next_offer()
        la.record(_yes(f"  Accept the deal if the possible loss is {offer:,.0f}? (y/n) > "))
    lam = la.loss_aversion_lambda
    case.preferences["loss_aversion"] = {
        "value": round(lam, 2),
        "source": "lottery-bisection",
        "confidence": "medium",
    }
    print(f"  -> A loss weighs about {lam:.1f}x an equal gain for you.")
    case.elicitation_log.append(
        {"method": "risk-and-loss-bets", "ce": ce.history, "loss": la.history}
    )
    case.log_event(
        "preferences", "code",
        f"risk r={r:.2f} and loss aversion lambda={lam:.2f} inferred from bets",
    )

    if not ask_baseline or case.preferences.get("baseline_alt_id"):
        return
    print("\nIs one of these the status quo (what happens if you change nothing)?")
    for i, alt in enumerate(case.active_alternatives(), start=1):
        print(f"  {i}. {alt.name}")
    raw = _ask(
        "Number, or Enter for none > ",
        kind="choice",
        choices=[
            {"value": str(i), "label": alt.name}
            for i, alt in enumerate(case.active_alternatives(), start=1)
        ],
        skip_label="None",
    )
    if raw.isdigit() and 1 <= int(raw) <= len(case.active_alternatives()):
        case.preferences["baseline_alt_id"] = case.active_alternatives()[int(raw) - 1].id


def _verify_weights_with_scenario(case: CaseFile) -> None:
    """Scenario-pair consistency check (elicit-scenario-choice skill):
    the user's felt preference between two constructed options is compared
    against what their stated weights imply. Construction and verdict are
    pure code; an inconsistency offers a re-score of the two swings."""
    from .elicitation import scenario_pair_check

    if case.frame.get("stakeholders"):
        return  # group weights have no single "felt preference" to test
    weights = {c.id: c.weight for c in case.criteria}
    check = scenario_pair_check(weights)
    if check is None:
        return
    crit_by_id = {c.id: c for c in case.criteria}
    c1, c2 = crit_by_id[check["c1"]], crit_by_id[check["c2"]]
    print("\nQuick consistency check. Two hypothetical options, identical")
    print("except on your two most important criteria:")
    print(f"  A: BEST on '{c1.name}', WORST on '{c2.name}'")
    print(f"  B: WORST on '{c1.name}', BEST on '{c2.name}'")
    chosen = ""
    while chosen not in ("a", "b"):
        chosen = _ask(
            "Which would you pick? (A/B) > ",
            kind="choice",
            choices=[
                {"value": "A", "label": f"A: best on '{c1.name}', worst on '{c2.name}'"},
                {"value": "B", "label": f"B: worst on '{c1.name}', best on '{c2.name}'"},
            ],
        ).strip().lower()
    consistent = chosen.upper() == check["implied"]
    case.elicitation_log.append(
        {
            "method": "scenario-consistency-check",
            "criteria": [c1.name, c2.name],
            "implied": check["implied"],
            "chosen": chosen.upper(),
            "consistent": consistent,
        }
    )
    if consistent:
        print("  Consistent with your stated weights.")
        return
    print(
        f"  That contradicts your swing answers (they imply {check['implied']})."
    )
    for c in (c1, c2):
        c.weight_confidence = "low" if hasattr(c, "weight_confidence") else "low"
    if _yes("Re-score the relative importance of these two? (y/N) > "):
        pts1 = 100.0
        pts2 = _ask_float(f"  '{c2.name}' vs '{c1.name}'=100 (0-200) > ", 0.0, 200.0)
        total_other = sum(c.weight for c in case.criteria if c.id not in (c1.id, c2.id))
        pair_share = max(1.0 - total_other, 0.0)
        c1.weight = pair_share * pts1 / (pts1 + pts2)
        c2.weight = pair_share * pts2 / (pts1 + pts2)
        print("  Updated weights: " + ", ".join(f"{c.name} {c.weight:.1%}" for c in case.criteria))
    else:
        print("  Keeping stated weights; the disagreement is recorded in the report.")


def _elicit_correlation(case: CaseFile) -> None:
    """One question per alternative with 2+ ranged scores: do its
    uncertainties move together? Independent sampling overstates
    diversification, so a common-factor coupling is offered (Gaussian
    copula in the Monte Carlo; marginals unchanged)."""
    candidates = [
        alt for alt in case.active_alternatives()
        if sum(
            1 for c in case.criteria
            if case.assessments.get(alt.id, {}).get(c.id) is not None
            and not case.assessment(alt.id, c.id).is_point
        ) >= 2
    ]
    if not candidates:
        return
    print("\n=== Correlated uncertainties ===")
    print("If one thing goes wrong for an option, do its other uncertain")
    print("scores tend to go wrong too? (Independent sampling would")
    print("otherwise make the ranking look more certain than it is.)")
    correlation: dict[str, float] = {}
    for alt in candidates:
        raw = _ask(
            f"  {alt.name}: do its uncertainties move together? > ",
            kind="choice",
            choices=[
                {"value": "0", "label": "Mostly independent"},
                {"value": "0.5", "label": "Somewhat linked - one setback drags others"},
                {"value": "0.8", "label": "Strongly linked - they stand or fall together"},
            ],
            skip_label="Mostly independent (default)",
        ).strip()
        rho = float(raw) if raw in ("0", "0.5", "0.8") else 0.0
        if rho > 0:
            correlation[alt.id] = rho
    if correlation:
        case.frame["correlation"] = correlation
        case.elicitation_log.append(
            {"method": "correlation-elicitation", "rho_by_alt": correlation}
        )
        case.log_event(
            "assessment", "code",
            "within-alternative correlation elicited for "
            + ", ".join(
                next(a.name for a in case.alternatives if a.id == aid)
                for aid in correlation
            ),
        )


def _readiness_gate(case: CaseFile) -> bool:
    """PLAN section 8: hard checks before any report. Returns False only
    when the user declines to proceed past an open blocker."""
    failures = []
    if len(case.active_alternatives()) < 2:
        failures.append("fewer than 2 feasible alternatives")
    if abs(sum(c.weight for c in case.criteria) - 1.0) > 1e-6 or any(
        c.weight <= 0 for c in case.criteria
    ):
        failures.append("criteria weights missing or not normalized")
    if not case.is_matrix_complete():
        failures.append("assessment matrix incomplete")
    if failures:
        print("\nReadiness check failed: " + "; ".join(failures))
        if "assessment matrix incomplete" in failures:
            print("Filling the missing scores now.")
            _assess(case)
            return _readiness_gate(case)
        return False

    case.log_event("gate", "code", "readiness checks passed")
    blockers = [
        i for i in case.critique_log
        if i.get("severity") == "blocker" and i.get("resolution") == "open"
    ]
    if blockers:
        print("\nUnresolved blocker(s) from the critic:")
        for b in blockers:
            print(f"  - {b.get('text', '')}")
        if _yes("Proceed to the report anyway? (y/N) > "):
            for b in blockers:
                b["resolution"] = "accepted-risk"
        else:
            print("Stopping before the report; the case file is saved for a rerun.")
            case.save(_case_dir(case) / "case.json")
            return False
    return True


def _run_analysis(case: CaseFile):
    from .analysis import stakeholder_views

    result = analyze(case)
    mc = monte_carlo(case)
    case.log_event(
        "analysis", "score-maut",
        f"MAUT ranking + Monte Carlo ({mc.n_samples:,} draws) computed",
    )
    views = stakeholder_views(case, result.normalized)
    if views:
        case.analysis["stakeholder_views"] = {
            name: {"ranking": v["ranking"], "agrees_with_group": v["agrees_with_group"]}
            for name, v in views.items()
        }
    case.analysis["monte_carlo"] = {
        "n_samples": mc.n_samples,
        "p_best": mc.p_best,
        "mean_rank": mc.mean_rank,
        "rank_probs": mc.rank_probs,
        "ce_utility": mc.ce_utility,
        "prospect_value": mc.prospect_value,
        "baseline_id": mc.baseline_id,
    }
    alt_by_id = {a.id: a for a in case.alternatives}
    print("\n=== Result ===")
    for i, alt_id in enumerate(result.ranking, start=1):
        print(
            f"  {i}. {alt_by_id[alt_id].name}  "
            f"(utility {result.utilities[alt_id]:.3f}, "
            f"P(best) {mc.p_best[alt_id]:.0%})"
        )
    if result.flip_points:
        f = result.flip_points[0]
        crit = next(c for c in case.criteria if c.id == f.criterion_id)
        print(
            f"  Most fragile assumption: weight of '{crit.name}' "
            f"({f.current_weight:.1%}); at {f.flip_weight:.1%} the winner becomes "
            f"'{alt_by_id[f.new_winner_id].name}'."
        )
    if views:
        dissenters = [
            (name, v) for name, v in views.items() if not v["agrees_with_group"]
        ]
        if dissenters:
            print("  Stakeholder disagreement:")
            for name, v in dissenters:
                print(
                    f"    {name} would pick '{alt_by_id[v['ranking'][0]].name}' "
                    "over the group choice."
                )
        else:
            print("  All stakeholders individually agree with the group choice.")

    from .voi import analyze_voi

    voi = analyze_voi(case)
    if voi is not None:
        case.log_event("analysis", "code", "value-of-information computed")
        actionable = [c for c in voi.cells if c.worth_resolving()]
        if voi.information_robust():
            print(
                "  Value of information: the recommendation is information-"
                "robust - no score resolution changes it."
            )
        elif actionable:
            crit_by_id = {c.id: c for c in case.criteria}
            top = actionable[0]
            print(
                f"  Worth resolving first: {crit_by_id[top.crit_id].name} for "
                f"'{alt_by_id[top.alt_id].name}' "
                f"(P(decision changes) = {top.switch_prob:.0%})."
            )
    return result, mc, voi


_FRAGILE_GAP = 0.10


def _sensitivity_review(case: CaseFile, result, mc, voi):
    """Fragile-weight follow-up: when the conclusion hinges on a small
    weight shift, offer to reconsider exactly that one weight, then rerun."""
    if not result.flip_points:
        return result, mc, voi
    f = result.flip_points[0]
    gap = abs(f.flip_weight - f.current_weight)
    if gap >= _FRAGILE_GAP:
        return result, mc, voi
    crit = next(c for c in case.criteria if c.id == f.criterion_id)
    print(
        f"\nThe conclusion is fragile: a {gap:.1%} shift in the weight of "
        f"'{crit.name}' flips it."
    )
    raw = _ask(
        f"Reconsider that weight? Enter = keep {f.current_weight:.1%}, "
        "or type a new percentage (e.g. 25) > "
    ).strip().rstrip("%")
    try:
        new_w = float(raw) / 100.0
    except ValueError:
        return result, mc, voi
    if not 0.0 < new_w < 1.0:
        return result, mc, voi
    old_w = crit.weight
    scale = (1.0 - new_w) / (1.0 - old_w) if old_w < 1.0 else 0.0
    for c in case.criteria:
        c.weight = new_w if c.id == crit.id else c.weight * scale
    case.elicitation_log.append(
        {
            "method": "sensitivity-reweight",
            "criterion": crit.name,
            "old": old_w,
            "new": new_w,
        }
    )
    print("Re-running the analysis with the updated weight.")
    return _run_analysis(case)


def _write_outputs(case: CaseFile, result, mc, narrative: str = "", voi=None) -> Path:
    case.completed_at = datetime.now().isoformat(timespec="seconds")
    case.log_event("report", "code", "analysis complete; writing report and charts")
    slug = case.slug()
    charts_dir = _case_dir(case) / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    chart_files: dict[str, str] = {}
    from .charts import utility_distribution_chart, voi_chart

    chart_list = [
        ("contributions", stacked_contribution_chart(case, result)),
        ("rank_probability", rank_probability_chart(case, mc)),
        ("utility_distribution", utility_distribution_chart(case, mc)),
        ("flip_thresholds", flip_threshold_chart(case, result)),
    ]
    if voi is not None and voi.cells:
        chart_list.append(("voi", voi_chart(case, voi)))
    for key, svg in chart_list:
        (charts_dir / f"{key}.svg").write_text(svg, encoding="utf-8")
        chart_files[key] = f"charts/{key}.svg"

    case_path = _case_dir(case) / "case.json"
    report_path = _case_dir(case) / "report.md"
    case.save(case_path)
    md_text = render_report(
        case, result, mc=mc, chart_files=chart_files, narrative=narrative, voi=voi
    )
    report_path.write_text(md_text, encoding="utf-8")
    from .htmlreport import write_html_report

    html_path = write_html_report(case, md_text, _case_dir(case))
    print(f"\nCase file: {case_path}")
    print(f"Report:    {report_path}")
    print(f"HTML:      {html_path}")
    print(f"Charts:    {charts_dir}")
    return html_path


def _run_bet_pipeline(case: CaseFile, provider=None):
    """Uncertain-bet branch: outcomes -> risk preferences -> expected
    utility (decision_tree.py) -> bet report. No criteria, no MAUT."""
    from .decision_tree import analyze_bet

    _elicit_outcomes(case)
    _elicit_uncertainty(case, ask_baseline=False)
    outcomes = case.frame.get("outcomes", {})
    with_outcomes = [a for a in case.active_alternatives() if outcomes.get(a.id)]
    if len(with_outcomes) < 2:
        print("\nReadiness check failed: need outcomes for at least 2 alternatives.")
        case.save(_case_dir(case) / "case.json")
        return

    result = analyze_bet(case)
    alt_by_id = {a.id: a for a in case.alternatives}
    print("\n=== Result (expected utility) ===")
    for i, aid in enumerate(result.ranking, start=1):
        print(
            f"  {i}. {alt_by_id[aid].name}  "
            f"(worth a sure {result.certainty_equivalent[aid]:,.0f} to you; "
            f"EV {result.expected_payoff[aid]:,.0f}; "
            f"P(best) {result.p_best[aid]:.0%})"
        )

    narrative = ""
    if provider is not None:
        from . import llm_phases

        case.save(_case_dir(case) / "case.json")
        winner = alt_by_id[result.ranking[0]].name
        _try_llm_extra(
            "premortem", lambda: llm_phases.run_premortem(provider, case, winner)
        )
        narrative = _try_llm_extra(
            "narrative", lambda: llm_phases.write_narrative(provider, case)
        ) or ""

    slug = case.slug()
    charts_dir = _case_dir(case) / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    chart_files: dict[str, str] = {}
    from .charts import risk_profile_chart

    # BetAnalysisResult duck-types the fields rank_probability_chart needs.
    for key, svg in (
        ("rank_probability", rank_probability_chart(case, result)),
        ("risk_profile", risk_profile_chart(case, result)),
    ):
        (charts_dir / f"{key}.svg").write_text(svg, encoding="utf-8")
        chart_files[key] = f"charts/{key}.svg"

    from .report import render_bet_report

    case.completed_at = datetime.now().isoformat(timespec="seconds")
    case.log_event("report", "code", "bet analysis complete; writing report")
    case.save(_case_dir(case) / "case.json")
    report_path = _case_dir(case) / "report.md"
    md_text = render_bet_report(case, result, chart_files, narrative)
    report_path.write_text(md_text, encoding="utf-8")
    from .htmlreport import write_html_report

    html_path = write_html_report(case, md_text, _case_dir(case))
    print(f"\nCase file: {CASES_DIR / (slug + '.json')}")
    print(f"Report:    {report_path}")
    print(f"HTML:      {html_path}")
    return html_path


def _run_allocation_pipeline(case: CaseFile, provider=None):
    """Allocation branch: items -> importance (swing) -> bounds -> the
    deterministic optimizer (allocation.py) across several diminishing-
    returns strengths -> markdown report."""
    from .allocation import DEFAULT_ALPHAS, AllocationItem, optimize

    items_src = case.active_alternatives()
    if len(items_src) < 2:
        print("\nAllocation needs at least 2 items to allocate across.")
        return

    print("\n=== Allocation setup ===")
    total = _ask_float("Total amount to allocate (one number) > ", 0.0)
    unit = _ask_float("Smallest allocation unit (e.g. 1000; Enter-like 1) > ", 0.0) or 1.0

    labeled = [(a.id, a.name) for a in items_src]
    points = _collect_swing_points(labeled)
    weights = swing_weights(points)

    print("\nPer-item bounds (Enter = none):")
    items: list[AllocationItem] = []
    for alt in items_src:
        raw_min = _ask(f"  minimum for {alt.name} > ").strip()
        raw_max = _ask(f"  maximum for {alt.name} > ").strip()
        items.append(
            AllocationItem(
                id=alt.id,
                name=alt.name,
                weight=weights[alt.id],
                min_amount=float(raw_min) if raw_min else 0.0,
                max_amount=float(raw_max) if raw_max else None,
            )
        )
    case.elicitation_log.append(
        {"method": "allocation-setup", "total": total, "unit": unit,
         "points": points}
    )

    try:
        result = optimize(items, total, unit)
    except ValueError as exc:
        print(f"\nInfeasible setup: {exc}")
        case.save(_case_dir(case) / "case.json")
        return

    name_by_id = {it.id: it.name for it in items}
    print("\n=== Recommended split (moderate diminishing returns) ===")
    for iid, amount in sorted(result.recommended.items(), key=lambda kv: -kv[1]):
        marker = f"  [{result.binding[iid]} bound]" if iid in result.binding else ""
        print(f"  {name_by_id[iid]}: {amount:,.0f}  ({amount / total:.0%}){marker}")

    case.frame["allocation"] = {
        "total": total,
        "unit": unit,
        "items": [vars(it) for it in items],
        "splits": {str(a): s for a, s in result.splits.items()},
        "binding": result.binding,
    }

    narrative = ""
    if provider is not None:
        from . import llm_phases

        case.save(_case_dir(case) / "case.json")
        top_item = max(result.recommended, key=result.recommended.get)
        _try_llm_extra(
            "premortem",
            lambda: llm_phases.run_premortem(
                provider, case, f"the split favoring {name_by_id[top_item]}"
            ),
        )
        narrative = _try_llm_extra(
            "narrative", lambda: llm_phases.write_narrative(provider, case)
        ) or ""

    from .charts import allocation_robustness_chart, allocation_split_chart
    from .report import render_allocation_report

    case.completed_at = datetime.now().isoformat(timespec="seconds")
    case.log_event("report", "code", "allocation optimized; writing report")
    slug = case.slug()
    charts_dir = _case_dir(case) / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    chart_files: dict[str, str] = {}
    for key, svg in (
        ("allocation_split", allocation_split_chart(case, result)),
        ("allocation_robustness",
         allocation_robustness_chart(case, result, DEFAULT_ALPHAS)),
    ):
        (charts_dir / f"{key}.svg").write_text(svg, encoding="utf-8")
        chart_files[key] = f"charts/{key}.svg"
    case.save(_case_dir(case) / "case.json")
    report_path = _case_dir(case) / "report.md"
    md_text = render_allocation_report(
        case, result, DEFAULT_ALPHAS, narrative, chart_files
    )
    report_path.write_text(md_text, encoding="utf-8")
    from .htmlreport import write_html_report

    html_path = write_html_report(case, md_text, _case_dir(case))
    print(f"\nCase file: {CASES_DIR / (slug + '.json')}")
    print(f"Report:    {report_path}")
    print(f"HTML:      {html_path}")
    return html_path


# ---- entry points --------------------------------------------------------


def run_interactive() -> None:
    print("Decision Analysis - wizard (no LLM; structured input mode)")
    case = _new_case()
    _intake(case)
    case.raw_input = case.statement
    _ask_decision_maker(case)
    _choose_type(case)
    _frame_alternatives(case)
    if case.decision_type == "uncertain-bet":
        _run_bet_pipeline(case)
        return
    if case.decision_type == "allocation":
        _run_allocation_pipeline(case)
        return
    _ensure_baseline(case)
    _frame_criteria(case)
    _elicit_weights(case)
    _verify_weights_with_scenario(case)
    _elicit_uncertainty(case)
    _assess(case)
    _elicit_correlation(case)
    if not _readiness_gate(case):
        return
    result, mc, voi = _run_analysis(case)
    result, mc, voi = _sensitivity_review(case, result, mc, voi)
    _write_outputs(case, result, mc, voi=voi)


def run_llm(provider) -> None:
    """M1.5 flow: LLM-driven INTAKE/FRAME/GENERATE + critic, then the
    deterministic elicitation/scoring/analysis phases."""
    from . import llm_phases

    print("Decision Analysis (LLM-assisted)")
    print("Describe your decision in your own words - the situation, any")
    print("options you are considering, and anything that limits you.\n")
    raw = ""
    while not raw:
        raw = _ask("> ", kind="text", multiline=True)

    case = _new_case(model_id=getattr(provider, "model", "") or "sdk-default")
    case.raw_input = raw
    channel.phase("intake")
    llm_phases.intake(provider, case, raw)
    _ask_decision_maker(case)
    channel.phase("framing")
    # Checklist-driven framing: the initial description is mapped onto the
    # decision-type checklist first (never re-asking what was already said),
    # then questions continue until every in-scope slot is closed - filled,
    # unknown (reported uncertainty), or not applicable.
    llm_phases.ask_frame_questions(provider, case, raw)
    channel.phase("options")
    llm_phases.generate_alternatives_and_criteria(provider, case)
    channel.phase("critique")
    if llm_phases.run_critic(provider, case):
        print("\nA blocker was raised. Review the lists once more:")
        pruned = llm_phases.generate_alternatives_and_criteria(provider, case)
        # Honest resolution labels: if the user pruned the offered revisions
        # at the gate, do not claim the blocker was addressed.
        resolution = (
            "revision-offered; user pruned: " + ", ".join(pruned[:4])
            if pruned
            else "addressed-by-revision"
        )
        for issue in case.critique_log:
            if issue.get("resolution") == "open":
                issue["resolution"] = resolution
    _try_llm_extra("ethics", lambda: llm_phases.run_ethics_check(provider, case))
    # External access needs explicit consent, every time.
    if _yes(
        "\nResearch public facts online (prices, typical values) to inform "
        "scoring? (y/N) > "
    ):
        channel.phase("research")
        llm_phases.run_fact_research(provider, case)
    # Route to the analysis model that fits the decision type.
    if case.decision_type == "uncertain-bet":
        return _run_bet_pipeline(case, provider)
    if case.decision_type == "allocation":
        return _run_allocation_pipeline(case, provider)
    _ensure_baseline(case)
    channel.phase("preferences")
    _elicit_weights(case)
    _verify_weights_with_scenario(case)
    _elicit_uncertainty(case)
    channel.phase("scoring")
    _assess(case)
    _elicit_correlation(case)
    if not _readiness_gate(case):
        return
    channel.phase("analysis")
    result, mc, voi = _run_analysis(case)
    result, mc, voi = _sensitivity_review(case, result, mc, voi)
    # Checkpoint: everything elicited and computed so far survives even if
    # the optional LLM steps below fail.
    case.save(_case_dir(case) / "case.json")
    channel.phase("report")
    winner = next(a.name for a in case.alternatives if a.id == result.ranking[0])
    _try_llm_extra("premortem", lambda: llm_phases.run_premortem(provider, case, winner))
    narrative = _try_llm_extra(
        "narrative", lambda: llm_phases.write_narrative(provider, case)
    ) or ""
    return _write_outputs(case, result, mc, narrative=narrative, voi=voi)


def run_demo() -> None:
    """Canned end-to-end example: choosing between job offers."""
    print("Decision Analysis - demo case: choosing a job offer\n")
    case = _new_case()
    case.statement = "Which job offer should I take?"
    case.raw_input = "Which job offer should I take? (canned demo)"
    case.alternatives = [
        Alternative(id="a1", name="Startup", description="Series B, equity-heavy"),
        Alternative(id="a2", name="BigCo", description="Established tech company"),
        Alternative(id="a3", name="Stay", description="Keep current job"),
    ]
    case.criteria = [
        Criterion(id="c1", name="Salary (k$/yr)", direction="max"),
        Criterion(id="c2", name="Commute (min)", direction="min"),
        Criterion(id="c3", name="Growth (1-10)", direction="max"),
        Criterion(id="c4", name="Stability (1-10)", direction="max"),
    ]
    points = {"c3": 100.0, "c1": 80.0, "c4": 50.0, "c2": 30.0}
    weights = swing_weights(points)
    for crit in case.criteria:
        crit.weight = weights[crit.id]
        crit.weight_source = "swing"
    case.elicitation_log.append({"method": "swing-weighting", "points": points})

    scores: dict[str, dict[str, Assessment]] = {
        "a1": {
            "c1": Assessment(120, 140, 180),
            "c2": Assessment(35, 40, 50),
            "c3": Assessment(7, 9, 10),
            "c4": Assessment(2, 4, 6),
        },
        "a2": {
            "c1": Assessment(160, 165, 175),
            "c2": Assessment(50, 55, 65),
            "c3": Assessment(5, 6, 7),
            "c4": Assessment(8, 9, 9),
        },
        "a3": {
            "c1": Assessment(120, 120, 125),
            "c2": Assessment(25, 25, 25),
            "c3": Assessment(3, 4, 5),
            "c4": Assessment(7, 8, 9),
        },
    }
    for alt_id, row in scores.items():
        for crit_id, assessment in row.items():
            case.set_assessment(alt_id, crit_id, assessment)

    # Canned M2 preferences: mildly risk-averse, typical loss aversion,
    # "Stay" as the status-quo reference point.
    case.preferences = {
        "risk_exponent": {"value": 0.76, "source": "demo", "confidence": "high"},
        "loss_aversion": {"value": 2.2, "source": "demo", "confidence": "high"},
        "baseline_alt_id": "a3",
    }

    result, mc, voi = _run_analysis(case)
    _write_outputs(case, result, mc, voi=voi)
