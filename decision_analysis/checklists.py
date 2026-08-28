"""Information-sufficiency checklists: the deterministic 'standard' that
decides when framing questions stop.

The standard is a finite set of information slots per decision type. Slots
close as filled / unknown / not_applicable; questioning ends when every slot
within the stakes-calibrated scope is closed - so termination is guaranteed
by slot closure, not by an arbitrary round cap. The LLM maps user language
onto slots and phrases questions; every state transition and the sufficiency
judgment happen here in code.
"""

from __future__ import annotations

from .casefile import CaseFile

# Statuses: open (not yet addressed), partial (mentioned but needs
# clarification), filled, unknown (user cannot say - becomes reported
# uncertainty), not_applicable.
OPEN_STATUSES = ("open", "partial")
CLOSED_STATUSES = ("filled", "unknown", "not_applicable")

MAX_ATTEMPTS_PER_SLOT = 2
MAX_LLM_ADDED_SLOTS = 3

_COMMON = [
    {
        "slot": "objectives",
        "ask_about": "what the user ultimately wants from this decision",
        "level": "required",
        "must_fill": True,
        "impact_if_unknown": "criteria and options cannot be grounded",
    },
    {
        "slot": "constraint_scan",
        "ask_about": "veto constraints: budget, timing, location, family, legal",
        "level": "required",
        "impact_if_unknown": "generated options may be infeasible",
    },
    {
        "slot": "deadline",
        "ask_about": "when the decision must be made",
        "level": "required",
        "impact_if_unknown": "cannot tell whether waiting is an option",
    },
]

TEMPLATES: dict[str, list[dict]] = {
    "discrete-choice": _COMMON + [
        {
            "slot": "status_quo",
            "ask_about": "what happens if nothing changes; is staying put an option",
            "level": "recommended",
            "impact_if_unknown": "the baseline alternative cannot be modeled",
        },
        {
            "slot": "stakeholders",
            "ask_about": "who else is affected or must agree",
            "level": "recommended",
            "impact_if_unknown": "acceptable options may be vetoed later",
        },
    ],
    "go-no-go": _COMMON + [
        {
            "slot": "no_action_consequences",
            "ask_about": "what actually happens if the user does NOT act",
            "level": "required",
            "impact_if_unknown": "the 'no' branch becomes a straw man",
        },
        {
            "slot": "trigger",
            "ask_about": "why this decision is on the table now",
            "level": "recommended",
            "impact_if_unknown": "urgency may be misjudged",
        },
    ],
    "allocation": _COMMON + [
        {
            "slot": "total_resource",
            "ask_about": "the total amount being allocated",
            "level": "required",
            "impact_if_unknown": "allocation cannot be computed",
        },
        {
            "slot": "divisibility",
            "ask_about": "smallest unit and any per-item minimums",
            "level": "recommended",
            "impact_if_unknown": "proposed splits may be impractical",
        },
    ],
    "timing": _COMMON + [
        {
            "slot": "waiting_cost",
            "ask_about": "what waiting costs (money, opportunities, stress)",
            "level": "required",
            "impact_if_unknown": "now-vs-later cannot be compared",
        },
        {
            "slot": "future_information",
            "ask_about": "what new information could arrive by waiting",
            "level": "recommended",
            "impact_if_unknown": "the value of waiting is invisible",
        },
    ],
    "uncertain-bet": _COMMON + [
        {
            "slot": "outcomes",
            "ask_about": "the possible outcomes and rough likelihoods",
            "level": "required",
            "impact_if_unknown": "expected values cannot be formed",
        },
        {
            "slot": "loss_capacity",
            "ask_about": "the largest loss the user could absorb",
            "level": "required",
            "impact_if_unknown": "ruinous options cannot be screened out",
        },
    ],
}


def _new_slot(spec: dict) -> dict:
    return {
        "slot": spec["slot"],
        "ask_about": spec["ask_about"],
        "level": spec.get("level", "recommended"),
        "must_fill": spec.get("must_fill", False),
        "impact_if_unknown": spec.get("impact_if_unknown", ""),
        "status": "open",
        "value": "",
        "note": "",
        "attempts": 0,
        "source": spec.get("source", "template"),
    }


def init_checklist(case: CaseFile) -> None:
    if case.frame.get("checklist"):
        return
    template = TEMPLATES.get(case.decision_type, TEMPLATES["discrete-choice"])
    case.frame["checklist"] = [_new_slot(s) for s in template]


def levels_in_scope(case: CaseFile) -> set[str]:
    """The sufficiency bar, calibrated to stakes and reversibility."""
    high = case.stakes == "high" or case.reversibility == "irreversible"
    low = case.stakes == "low" and case.reversibility != "irreversible"
    if low:
        return {"required"}
    if high:
        return {"required", "recommended", "conditional"}
    return {"required", "recommended"}


def slots_in_scope(case: CaseFile) -> list[dict]:
    scope = levels_in_scope(case)
    return [s for s in case.frame.get("checklist", []) if s["level"] in scope]


def open_slots(case: CaseFile) -> list[dict]:
    return [s for s in slots_in_scope(case) if s["status"] in OPEN_STATUSES]


def is_sufficient(case: CaseFile) -> bool:
    return not open_slots(case)


def find_slot(case: CaseFile, slot_id: str) -> dict | None:
    for s in case.frame.get("checklist", []):
        if s["slot"] == slot_id:
            return s
    return None


def apply_updates(case: CaseFile, updates: list[dict]) -> None:
    """Apply LLM-proposed status updates; code validates every transition."""
    for upd in updates:
        slot = find_slot(case, upd.get("slot", ""))
        if slot is None:
            continue
        status = upd.get("status", "")
        if status not in ("filled", "partial", "unknown", "not_applicable"):
            continue
        # A must-fill slot cannot be closed as unknown by the model; keep it
        # open for clarification until attempts run out (see enforce_caps).
        if slot.get("must_fill") and status in ("unknown", "not_applicable"):
            status = "partial"
        slot["status"] = status
        if upd.get("value"):
            slot["value"] = str(upd["value"])
        if upd.get("note"):
            slot["note"] = str(upd["note"])


def add_llm_slots(case: CaseFile, new_slots: list[dict]) -> None:
    """Case-specific slots proposed by the LLM (capped, always recommended)."""
    added = sum(1 for s in case.frame["checklist"] if s["source"] == "llm")
    for spec in new_slots:
        if added >= MAX_LLM_ADDED_SLOTS:
            break
        if not spec.get("slot") or find_slot(case, spec["slot"]) is not None:
            continue
        case.frame["checklist"].append(
            _new_slot(
                {
                    "slot": spec["slot"],
                    "ask_about": spec.get("ask_about", spec["slot"]),
                    "level": "recommended",
                    "impact_if_unknown": spec.get("impact_if_unknown", ""),
                    "source": "llm",
                }
            )
        )
        added += 1


def record_attempt(case: CaseFile, slot_id: str) -> None:
    slot = find_slot(case, slot_id)
    if slot is not None:
        slot["attempts"] += 1


def enforce_attempt_caps(case: CaseFile) -> None:
    """A slot asked MAX_ATTEMPTS times and still open closes automatically:
    unknown for normal slots; a must-fill slot degrades to filled-with-a-note
    so the pipeline can proceed (the critic sees the note)."""
    for slot in case.frame.get("checklist", []):
        if slot["status"] in OPEN_STATUSES and slot["attempts"] >= MAX_ATTEMPTS_PER_SLOT:
            if slot.get("must_fill"):
                slot["status"] = "filled"
                slot["note"] = "vague after repeated asks - framing needs attention"
            else:
                slot["status"] = "unknown"
                slot["note"] = slot["note"] or "user could not specify after repeated asks"


def close_all_open(case: CaseFile, note: str) -> None:
    """User override ('enough, start the analysis'): close remaining slots."""
    for slot in case.frame.get("checklist", []):
        if slot["status"] in OPEN_STATUSES:
            if slot.get("must_fill"):
                slot["status"] = "filled"
                slot["note"] = "left vague by user choice"
            else:
                slot["status"] = "unknown"
                slot["note"] = note


def unknown_slots(case: CaseFile) -> list[dict]:
    return [s for s in case.frame.get("checklist", []) if s["status"] == "unknown"]
