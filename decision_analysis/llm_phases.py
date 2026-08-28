"""LLM-driven INTAKE / FRAME / GENERATE phases (M1.5).

Implements the three-path logic from PLAN section 6.1:
- user mentioned alternatives -> extract, confirm, then supplement;
- pure dilemma -> ask constraint/objective questions FIRST, then generate;
- no LLM -> the orchestrator falls back to the manual wizard.

Each phase loads its SKILL.md body as the system prompt, so skill files are
the single source of truth for LLM behavior. All numeric work stays in the
deterministic modules.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import checklists
from .casefile import Alternative, CaseFile, Criterion, frame_item
from .provider import LLMProvider

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


# ---- skill loading and JSON plumbing -------------------------------------


def load_skill(name: str) -> str:
    """Return the SKILL.md body (frontmatter stripped) to use as a system prompt."""
    text = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"^---\n.*?\n---\n", text, flags=re.DOTALL)
    return text[match.end():] if match else text


def _parse_json(raw: str) -> dict:
    """Extract the first JSON object from a model reply, tolerating fences
    and raw control characters (e.g. literal newlines) inside strings."""
    cleaned = re.sub(r"```(?:json)?", "", raw).strip("` \n")
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"no JSON object in model reply: {raw[:200]!r}")
    return json.loads(cleaned[start : end + 1], strict=False)


def _call_skill(
    provider: LLMProvider,
    skill: str,
    prompt: str,
    case: CaseFile | None = None,
    phase: str = "",
) -> dict:
    from . import registry

    if not registry.is_registered(skill):
        raise RuntimeError(f"skill '{skill}' is not in the registry (allow/deny gate)")
    print("  [thinking...]")
    if case is not None:
        case.log_event(phase or skill, skill, "LLM skill call")
    system = load_skill(skill)
    try:
        data = _parse_json(provider.complete(prompt, system=system))
    except ValueError:
        # One corrective retry before giving up - models occasionally wrap
        # or malform the JSON despite instructions.
        print("  [reply was not valid JSON - asking once more]")
        if case is not None:
            case.log_event(phase or skill, skill, "invalid JSON; retried once")
        data = _parse_json(
            provider.complete(
                prompt
                + "\n\nYour previous reply could not be parsed as JSON. "
                "Reply with ONLY the JSON object - no commentary, no code "
                "fences, and use \\n escapes for newlines inside strings.",
                system=system,
            )
        )
    # Runtime capability requests from any skill go through the policy gate.
    if case is not None and data.get("request_skill"):
        req = data["request_skill"]
        decision = registry.evaluate_skill_request(
            case, str(req.get("name", req)), str(req.get("reason", ""))
        )
        print(f"  [skill request '{decision['requested']}': {decision['decision']} - {decision['note']}]")
    return data


def _frame_as_json(case: CaseFile) -> str:
    frame_no_checklist = {k: v for k, v in case.frame.items() if k != "checklist"}
    return json.dumps(
        {
            "statement": case.statement,
            "decision_type": case.decision_type,
            "stakes": case.stakes,
            "reversibility": case.reversibility,
            "frame": frame_no_checklist,
            "alternatives": [
                {"name": a.name, "description": a.description, "source": a.source}
                for a in case.alternatives
            ],
            "criteria": [
                {"name": c.name, "direction": c.direction} for c in case.criteria
            ],
            "critique_log": case.critique_log,
        },
        ensure_ascii=False,
        indent=2,
    )


# ---- confirmation gates --------------------------------------------------


def _edit_list(items: list[str], noun: str) -> list[str]:
    """Numbered-list confirmation gate: blank accepts, 'd N' deletes, 'a text' adds."""
    from . import channel

    while True:
        # On the web the editor card shows the items itself; printing them
        # too would duplicate the list in the transcript. Console only.
        if channel.current() is None:
            print(f"\nCurrent {noun}:")
            for i, item in enumerate(items, start=1):
                print(f"  {i}. {item}")
        raw = channel.ask(
            "Enter = accept | 'd 2' = delete #2 | 'a <text>' = add > ",
            kind="list_edit",
            items=list(items),
            noun=noun,
        ).strip()
        if not raw:
            return items
        if raw.lower().startswith("d ") and raw[2:].strip().isdigit():
            idx = int(raw[2:].strip())
            if 1 <= idx <= len(items):
                items.pop(idx - 1)
            continue
        if raw.lower().startswith("a ") and raw[2:].strip():
            items.append(raw[2:].strip())
            continue
        print("  Unrecognized command.")


# ---- phases --------------------------------------------------------------


def intake(provider: LLMProvider, case: CaseFile, raw_input_text: str) -> None:
    """INTAKE: parse the user's free-text description via the framing skill."""
    data = _call_skill(provider, "framing", raw_input_text, case, phase="intake")
    case.statement = data.get("statement", raw_input_text)
    if data.get("detected_language"):
        case.language = data["detected_language"]
    case.decision_type = data.get("decision_type", "discrete-choice")
    if data.get("stakes") in ("low", "medium", "high"):
        case.stakes = data["stakes"]
    if data.get("reversibility") in ("reversible", "partial", "irreversible"):
        case.reversibility = data["reversibility"]
    case.frame["hard_constraints"] = [
        frame_item(x, "initial") for x in data.get("hard_constraints", [])
    ]
    case.frame["objectives"] = [
        frame_item(x, "initial") for x in data.get("objectives", [])
    ]
    case.frame["context_facts"] = [
        frame_item(x, "initial")
        for x in data.get("context_facts", []) + data.get("preference_hints", [])
    ]
    case.log_event("intake", "code", "frame extracted from initial description")
    for item in data.get("alternatives", []):
        case.alternatives.append(
            Alternative(
                id=f"a{len(case.alternatives) + 1}",
                name=item["name"],
                description=item.get("description", ""),
                source="extracted",
            )
        )
    print(
        f"\nDecision: {case.statement}  "
        f"[{case.decision_type}, stakes: {case.stakes}, {case.reversibility}]"
    )


_STOP_WORDS = ("enough", "start the analysis", "够了", "开始分析", "开始吧")


def _is_stop(answer: str) -> bool:
    lowered = answer.strip().lower()
    return any(word in lowered for word in _STOP_WORDS)


def ask_frame_questions(
    provider: LLMProvider, case: CaseFile, initial_text: str
) -> None:
    """Checklist-driven framing loop (gap-and-ask skill).

    The standard is the decision-type checklist in checklists.py. Round 1
    maps the user's ORIGINAL description onto the slots (so nothing already
    said is ever asked again); every later round maps the newest answers.
    Questions target only open/partial slots; 'unknown' is a valid answer
    that closes a slot as reported uncertainty. Sufficiency, attempt caps,
    and termination are decided in code, never by the model.
    """
    checklists.init_checklist(case)
    new_text = f"Initial user description:\n{initial_text}"
    first_call = True

    while True:
        prompt = (
            "Current case file:\n" + _frame_as_json(case)
            + "\n\nChecklist:\n"
            + json.dumps(case.frame["checklist"], ensure_ascii=False, indent=1)
            + "\n\n" + new_text
            + "\n\nMap this text onto the checklist, then produce questions "
            "for slots still open."
        )
        data = _call_skill(provider, "gap-and-ask", prompt, case, phase="framing-qa")

        checklists.apply_updates(case, data.get("slot_updates", []))
        if first_call:
            checklists.add_llm_slots(case, data.get("new_slots", []))
        for key in ("hard_constraints", "objectives", "context_facts"):
            if key in data.get("frame", {}):
                # Merge preserving provenance: unchanged texts keep their
                # original source; new/changed items are stamped with the
                # phase that produced them.
                existing = {
                    item["text"]: item
                    for item in case.frame.get(key, [])
                    if isinstance(item, dict)
                }
                kind = "initial" if first_call else "qa"
                case.frame[key] = [
                    existing.get(
                        frame_item(x)["text"], frame_item(x, kind)
                    )
                    for x in data["frame"][key]
                ]
        first_call = False
        checklists.enforce_attempt_caps(case)

        if checklists.is_sufficient(case):
            return
        open_ids = {s["slot"] for s in checklists.open_slots(case)}
        questions = [q for q in data.get("questions", []) if q.get("slot") in open_ids]
        if not questions:
            # Insufficient but the model has nothing valid to ask: close the
            # remainder as unknown rather than stalling.
            checklists.close_all_open(case, "no viable question produced")
            return

        remaining = len(open_ids)
        print(f"\nA few questions to complete the picture ({remaining} item(s) open;")
        print("Enter or 'unknown' if you can't say, 'enough' to start the analysis):")
        from . import channel as _channel

        answers = []
        stopped = False
        for q in questions[:3]:
            checklists.record_attempt(case, q["slot"])
            answer = _channel.ask(f"  {q['text']} > ", kind="text", multiline=True)
            if _is_stop(answer):
                stopped = True
                break
            if not answer:
                # A blank answer means "I don't know": close the slot as
                # unknown right here - no LLM round needed for that.
                slot = checklists.find_slot(case, q["slot"])
                if slot is not None and not slot.get("must_fill"):
                    slot["status"] = "unknown"
                    slot["note"] = "left blank by user"
                answers.append(
                    {"slot": q["slot"], "question": q["text"], "answer": "unknown"}
                )
                continue
            answers.append({"slot": q["slot"], "question": q["text"], "answer": answer})
        if answers:
            case.elicitation_log.append({"method": "frame-questions", "qa": answers})
        if stopped:
            checklists.close_all_open(case, "user chose to start the analysis")
            return
        new_text = "User answers to your previous questions:\n" + json.dumps(
            answers, ensure_ascii=False
        )


def generate_alternatives_and_criteria(
    provider: LLMProvider, case: CaseFile
) -> list[str]:
    """GENERATE: propose alternatives (constraint-filtered) and criteria,
    each behind a user confirmation gate. Returns the names the user pruned
    at the gates this round (used for honest blocker-resolution labels)."""
    prompt = "Current case file:\n" + _frame_as_json(case)
    rejected = case.frame.get("rejected_proposals", [])
    if rejected:
        prompt += (
            "\n\nThe user previously REMOVED these proposals at a "
            "confirmation gate - do NOT propose them (or trivial rephrasings "
            "of them) again:\n" + json.dumps(rejected, ensure_ascii=False)
        )
    data = _call_skill(
        provider,
        "option-criteria-gen",
        prompt + "\n\nGenerate your proposals.",
        case,
        phase="generation",
    )
    pruned_this_round: list[str] = []

    # -- alternatives: existing (extracted) + generated proposals, user-gated
    extracted_names = {a.name for a in case.alternatives}
    # The model sometimes re-proposes what the case file already holds
    # (with a slightly different description) - dedupe by name so the gate
    # never shows the same alternative twice.
    proposals = [
        p for p in data.get("alternatives", [])
        if p.get("name") and p["name"] not in extracted_names
    ]
    if proposals:
        print("\nProposed additional alternatives (model-generated):")
    names = [f"{a.name} - {a.description}".rstrip(" -") for a in case.alternatives]
    for p in proposals:
        label = f"{p['name']} - {p.get('description', '')}".rstrip(" -")
        note = p.get("feasibility_note")
        names.append(label + (f"  [note: {note}]" if note else ""))
    confirmed = _edit_list(list(names), "alternatives")
    pruned_this_round += [
        n.partition(" - ")[0].strip() for n in names if n not in confirmed
    ]
    case.alternatives = []
    for i, label in enumerate(confirmed, start=1):
        name, _, description = label.partition(" - ")
        source = "extracted" if name.strip() in extracted_names else "generated"
        case.alternatives.append(
            Alternative(
                id=f"a{i}",
                name=name.strip(),
                description=description.split("  [note:")[0].strip(),
                source=source,
            )
        )

    # -- criteria proposals, user-gated. Allocation and uncertain-bet types
    # are analyzed without criteria (optimizer / expected utility), so no
    # empty list is shown for confirmation.
    if case.decision_type in ("allocation", "uncertain-bet"):
        case.criteria = []
        print(
            f"\n(No criteria needed: {case.decision_type} decisions are "
            "analyzed by "
            + ("the allocation optimizer" if case.decision_type == "allocation"
               else "expected utility over outcomes")
            + ".)"
        )
        _record_pruned(case, pruned_this_round)
        return pruned_this_round
    # Merge criteria the same way alternatives are merged: previously
    # confirmed ones stay in the list alongside this round's new proposals
    # (a critic-triggered regeneration must never wipe confirmed criteria).
    existing_crit = [
        {"name": c.name, "direction": c.direction, "scale_hint": c.scale_note}
        for c in case.criteria
    ]
    existing_names = {c["name"] for c in existing_crit}
    new_props = [
        c for c in data.get("criteria", [])
        if c.get("name") and c["name"] not in existing_names
    ]
    all_crit = existing_crit + new_props

    def _crit_label(c: dict) -> str:
        return f"{c['name']} ({c['direction']}; {c.get('scale_hint', '')})"

    crit_labels = [_crit_label(c) for c in all_crit]
    confirmed_crit = _edit_list(list(crit_labels), "criteria")
    pruned_this_round += [
        known_label.partition(" (")[0].strip()
        for known_label in crit_labels
        if known_label not in confirmed_crit
    ]
    case.criteria = []
    known = {_crit_label(c): c for c in all_crit}
    for i, label in enumerate(confirmed_crit, start=1):
        if label in known:
            c = known[label]
            name, direction, scale = c["name"], c["direction"], c.get("scale_hint", "")
        else:
            # User-added: "name" or "name, min". Only treat the final
            # comma-part as a direction when it actually IS one - names
            # containing commas (e.g. "$5,000 floor") must stay intact.
            head, _, tail = label.rpartition(",")
            if head and tail.strip().lower() in ("max", "min"):
                name, direction = head, tail.strip().lower()
            else:
                name, direction = label, "max"
            scale = ""
        case.criteria.append(
            Criterion(
                id=f"c{i}",
                name=name.strip(),
                direction=direction if direction in ("max", "min") else "max",
                scale_note=scale,
            )
        )
    _record_pruned(case, pruned_this_round)
    return pruned_this_round


def _record_pruned(case: CaseFile, pruned: list[str]) -> None:
    """Remember gate-pruned proposal names so later generation rounds never
    re-propose them, and the audit trail shows what the user rejected."""
    if not pruned:
        return
    store = case.frame.setdefault("rejected_proposals", [])
    for name in pruned:
        if name and name not in store:
            store.append(name)
    case.elicitation_log.append({"method": "confirmation-gate-pruned", "names": pruned})


def run_fact_research(provider: LLMProvider, case: CaseFile) -> None:
    """Researcher role: WebSearch-enabled fresh query for objective facts
    that inform scoring. Only runs with the user's explicit consent (asked
    by the orchestrator before calling this)."""
    from . import registry

    if not registry.is_registered("fact-research"):
        raise RuntimeError("fact-research skill missing from registry")
    print("  [researching online - this can take a minute...]")
    raw = provider.research(
        "Case file:\n" + _frame_as_json(case)
        + "\n\nResearch facts that would inform scoring these alternatives "
        "on these criteria. Search English sources; report findings in "
        "English.",
        system=load_skill("fact-research"),
    )
    try:
        data = _parse_json(raw)
    except ValueError:
        print("  Research returned no usable facts; continuing without.")
        return
    facts = [f for f in data.get("facts", []) if f.get("finding")]
    if not facts:
        print("  No verifiable facts found; continuing without.")
        return
    print("\nResearched facts (sources in the report appendix):")
    for f in facts:
        value = f" [{f['value']}]" if f.get("value") else ""
        print(f"  - {f['finding']}{value}  ({f.get('source', '?')})")
    case.frame["researched_facts"] = facts
    case.frame.setdefault("context_facts", []).extend(
        frame_item(f["finding"], "research", f.get("source", "?")) for f in facts
    )
    case.elicitation_log.append(
        {"method": "fact-research", "count": len(facts),
         "not_found": data.get("not_found", [])}
    )
    case.log_event("research", "fact-research", f"{len(facts)} sourced fact(s) added")


def run_critic(provider: LLMProvider, case: CaseFile) -> bool:
    """Clean-context critique of the confirmed frame. Returns True if any
    blocker was raised (caller decides whether to loop back)."""
    data = _call_skill(
        provider,
        "critic",
        "Case file under review:\n" + _frame_as_json(case) + "\n\nYour critique.",
        case,
        phase="critique",
    )
    issues = data.get("issues", [])
    if not issues:
        print("\nCritic: no objections.")
        return False
    print("\nCritic findings:")
    has_blocker = False
    for issue in issues:
        severity = issue.get("severity", "note")
        has_blocker = has_blocker or severity == "blocker"
        print(f"  [{severity}] {issue.get('text', '')}")
        if issue.get("suggested_fix"):
            print(f"           fix: {issue['suggested_fix']}")
        issue.setdefault("skill", "critic")
        issue.setdefault(
            "resolution", "open" if severity == "blocker" else "noted"
        )
        case.critique_log.append(issue)
    return has_blocker


def run_ethics_check(provider: LLMProvider, case: CaseFile) -> None:
    """Lightweight ethics/externalities flagger (clean context). Serious
    findings escalate into the critique log as open blockers, so the
    readiness gate forces an explicit user decision on them."""
    data = _call_skill(
        provider,
        "ethics-check",
        "Case file under review:\n" + _frame_as_json(case) + "\n\nYour review.",
        case,
        phase="ethics",
    )
    concerns = [c for c in data.get("concerns", []) if c.get("text")]
    case.ethics = concerns
    case.ethics_checked = True
    if not concerns:
        print("\nEthics check: no material concerns identified.")
        return
    print("\nEthics check:")
    for c in concerns:
        severity = c.get("severity", "flag")
        print(f"  [{severity}] ({c.get('target', 'general')}) {c['text']}")
        if severity == "serious":
            case.critique_log.append(
                {
                    "skill": "ethics-check",
                    "severity": "blocker",
                    "text": f"Ethics: {c['text']}",
                    "resolution": "open",
                }
            )


def run_premortem(provider: LLMProvider, case: CaseFile, winner_name: str) -> None:
    """Premortem on the recommendation: 'a year from now this choice failed -
    why?' Risks land in the critique log and the report's risk section."""
    data = _call_skill(
        provider,
        "premortem",
        "Case file:\n" + _frame_as_json(case)
        + f"\n\nThe analysis recommends: {winner_name}."
        + "\nImagine this choice clearly FAILED one year later. Your premortem.",
        case,
        phase="premortem",
    )
    risks = data.get("risks", [])
    if risks:
        print("\nPremortem risks:")
    for risk in risks:
        print(f"  [{risk.get('severity', 'medium')}] {risk.get('text', '')}")
        case.critique_log.append(
            {
                "skill": "premortem",
                "severity": risk.get("severity", "medium"),
                "text": risk.get("text", ""),
                "mitigation": risk.get("mitigation", ""),
                "resolution": "noted",
            }
        )


def write_narrative(provider: LLMProvider, case: CaseFile) -> str:
    """Executive narrative in English, generated from the case file and
    analysis numbers (never inventing new numbers)."""
    data = _call_skill(
        provider,
        "report-writer",
        "Case file with analysis results:\n"
        + json.dumps(case.to_dict(), ensure_ascii=False, default=str)
        + "\n\nWrite the narrative (in English).",
        case,
        phase="report",
    )
    return str(data.get("narrative", "")).strip()
