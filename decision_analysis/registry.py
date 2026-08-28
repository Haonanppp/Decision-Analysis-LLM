"""Skill registry and allow/deny policy (PLAN section 7).

The registry is built by scanning skills/*/SKILL.md manifests at startup -
it is the whitelist. Every LLM skill invocation passes through the registry
gate, and runtime requests for new capabilities (a skill or the LLM saying
"I need X") are evaluated against the policy below, with every decision
logged to the case file for the report appendix.

Policy:
1. Requested capability matches a registered skill -> allow (use it).
2. Close to an existing skill -> deny, point at the existing one.
3. Needs external access (network, filesystem) or out of decision-analysis
   scope -> deny (escalate to the user is a later refinement).
"""

from __future__ import annotations

import re
from pathlib import Path

from .casefile import CaseFile

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def load_registry() -> dict[str, dict]:
    """Scan skill manifests: {name: {description, dir}}."""
    registry: dict[str, dict] = {}
    for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---\n", text, flags=re.DOTALL)
        if not match:
            continue
        front = match.group(1)
        name_m = re.search(r"^name:\s*(.+)$", front, flags=re.MULTILINE)
        desc_m = re.search(r"^description:\s*(.+)$", front, flags=re.MULTILINE)
        name = (name_m.group(1).strip() if name_m else skill_md.parent.name)
        registry[name] = {
            "description": desc_m.group(1).strip() if desc_m else "",
            "dir": str(skill_md.parent),
        }
    return registry


_REGISTRY: dict[str, dict] | None = None


def registry() -> dict[str, dict]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = load_registry()
    return _REGISTRY


def is_registered(name: str) -> bool:
    return name in registry()


def evaluate_skill_request(case: CaseFile, requested: str, reason: str = "") -> dict:
    """Allow/deny a runtime request for a capability. Logged to the case."""
    reg = registry()
    if requested in reg:
        decision = {"requested": requested, "decision": "allow",
                    "note": "already registered"}
    else:
        # Cheap similarity: shared word stem with an existing skill name.
        tokens = set(re.split(r"[-_\s]+", requested.lower()))
        near = next(
            (n for n in reg if tokens & set(re.split(r"[-_\s]+", n.lower()))),
            None,
        )
        if near:
            decision = {"requested": requested, "decision": "deny",
                        "note": f"use existing skill '{near}' instead"}
        else:
            decision = {"requested": requested, "decision": "deny",
                        "note": "not in registry; out of scope or needs "
                                "external access - requires user approval"}
    decision["reason"] = reason
    case.skill_log.append(decision)
    return decision
