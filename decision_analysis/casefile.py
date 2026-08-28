"""Decision Case File: the shared blackboard every component reads and writes.

The case file is the single synchronization medium between the orchestrator,
skills, and (later) subagents. It serializes to plain JSON so a session can be
resumed and every parameter's provenance can be audited in the report appendix.

Provenance convention: frame lists (objectives, hard_constraints,
context_facts) hold items shaped {"text", "source_kind", "source_ref"} -
source_kind in {"initial", "qa", "research", "inferred", "user"}, source_ref
the supporting quote, question, or source name. frame_item() normalizes
legacy bare strings.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


def frame_item(value, source_kind: str = "initial", source_ref: str = "") -> dict:
    """Normalize a frame entry (string or dict) to the provenance shape."""
    if isinstance(value, dict):
        return {
            "text": str(value.get("text", "")),
            "source_kind": str(value.get("source_kind", source_kind)),
            "source_ref": str(value.get("source_ref", value.get("quote", source_ref))),
        }
    return {"text": str(value), "source_kind": source_kind, "source_ref": source_ref}


def frame_texts(items: list) -> list[str]:
    """Plain texts of a frame list, tolerating both shapes."""
    return [i["text"] if isinstance(i, dict) else str(i) for i in items]


@dataclass
class Criterion:
    id: str
    name: str
    direction: str  # "max" (more is better) or "min" (less is better)
    weight: float = 0.0
    weight_source: str = "unset"  # e.g. "swing", "stated", "inferred"
    scale_note: str = ""


@dataclass
class Alternative:
    id: str
    name: str
    description: str = ""
    status: str = "active"  # "active" | "dominated" | "infeasible"
    source: str = "user"  # "user" | "extracted" | "generated"


@dataclass
class Assessment:
    """Three-point estimate for one alternative on one criterion.

    A single point value is stored as low == mode == high.
    """

    low: float
    mode: float
    high: float
    basis: str = "user"  # "user" | "research" | "estimate"

    @property
    def pert_mean(self) -> float:
        return (self.low + 4.0 * self.mode + self.high) / 6.0

    @property
    def is_point(self) -> bool:
        return self.low == self.mode == self.high


def _empty_frame() -> dict:
    return {"objectives": [], "hard_constraints": [], "context_facts": []}


@dataclass
class CaseFile:
    statement: str = ""
    raw_input: str = ""  # the user's original free-text description, verbatim
    decision_maker: str = "anonymous"
    decision_type: str = "discrete-choice"
    stakes: str = "medium"  # "low" | "medium" | "high"
    reversibility: str = "partial"  # "reversible" | "partial" | "irreversible"
    language: str = "en"  # detected input language (metadata; output is English)
    created_at: str = ""
    completed_at: str = ""
    engine_version: str = ""
    model_id: str = ""  # LLM model used, "" in wizard mode
    # frame: {"objectives": [...], "hard_constraints": [...],
    #         "context_facts": [...], "checklist": [...]}
    frame: dict = field(default_factory=_empty_frame)
    criteria: list[Criterion] = field(default_factory=list)
    alternatives: list[Alternative] = field(default_factory=list)
    # assessments[alternative_id][criterion_id] -> Assessment
    assessments: dict[str, dict[str, Assessment]] = field(default_factory=dict)
    analysis: dict = field(default_factory=dict)
    # preferences: e.g. {"risk_exponent": {"value": .., "source": .., "confidence": ..},
    #                    "loss_aversion": {...}, "baseline_alt_id": "a3"}
    preferences: dict = field(default_factory=dict)
    critique_log: list[dict] = field(default_factory=list)
    elicitation_log: list[dict] = field(default_factory=list)
    skill_log: list[dict] = field(default_factory=list)  # allow/deny decisions
    # ethics: [{"target": alt name or "general", "severity": "flag"|"serious",
    #           "text": ..}]; [] after a clean check; None-like absence = not run
    ethics: list[dict] = field(default_factory=list)
    ethics_checked: bool = False
    # trace: chronological workflow log [{"at", "phase", "actor", "action"}]
    trace: list[dict] = field(default_factory=list)

    def log_event(self, phase: str, actor: str, action: str) -> None:
        self.trace.append(
            {
                "at": datetime.now().isoformat(timespec="seconds"),
                "phase": phase,
                "actor": actor,  # "user" | "code" | skill name
                "action": action,
            }
        )

    # ---- convenience -----------------------------------------------------

    def active_alternatives(self) -> list[Alternative]:
        return [a for a in self.alternatives if a.status == "active"]

    def assessment(self, alt_id: str, crit_id: str) -> Assessment:
        return self.assessments[alt_id][crit_id]

    def set_assessment(self, alt_id: str, crit_id: str, assessment: Assessment) -> None:
        self.assessments.setdefault(alt_id, {})[crit_id] = assessment

    def is_matrix_complete(self) -> bool:
        return all(
            crit.id in self.assessments.get(alt.id, {})
            for alt in self.active_alternatives()
            for crit in self.criteria
        )

    def slug(self) -> str:
        text = re.sub(r"[^a-zA-Z0-9]+", "-", self.statement.lower()).strip("-")
        text = text[:48].rstrip("-")
        # Non-ASCII statements (e.g. Chinese) strip to nothing; a content hash
        # keeps case files from different decisions from colliding.
        digest = hashlib.sha1(self.statement.encode("utf-8")).hexdigest()[:8]
        return f"{text}-{digest}" if text else f"decision-{digest}"

    # ---- persistence -----------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "statement": self.statement,
            "raw_input": self.raw_input,
            "decision_maker": self.decision_maker,
            "decision_type": self.decision_type,
            "stakes": self.stakes,
            "reversibility": self.reversibility,
            "language": self.language,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "engine_version": self.engine_version,
            "model_id": self.model_id,
            "frame": self.frame,
            "criteria": [vars(c) for c in self.criteria],
            "alternatives": [vars(a) for a in self.alternatives],
            "assessments": {
                alt_id: {crit_id: vars(a) for crit_id, a in row.items()}
                for alt_id, row in self.assessments.items()
            },
            "analysis": self.analysis,
            "preferences": self.preferences,
            "critique_log": self.critique_log,
            "elicitation_log": self.elicitation_log,
            "skill_log": self.skill_log,
            "ethics": self.ethics,
            "ethics_checked": self.ethics_checked,
            "trace": self.trace,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CaseFile":
        case = cls(
            statement=data.get("statement", ""),
            raw_input=data.get("raw_input", ""),
            decision_maker=data.get("decision_maker", "anonymous"),
            decision_type=data.get("decision_type", "discrete-choice"),
            stakes=data.get("stakes", "medium"),
            reversibility=data.get("reversibility", "partial"),
            language=data.get("language", "en"),
            created_at=data.get("created_at", ""),
            completed_at=data.get("completed_at", ""),
            engine_version=data.get("engine_version", ""),
            model_id=data.get("model_id", ""),
            frame=data.get("frame", _empty_frame()),
            criteria=[Criterion(**c) for c in data.get("criteria", [])],
            alternatives=[Alternative(**a) for a in data.get("alternatives", [])],
            analysis=data.get("analysis", {}),
            preferences=data.get("preferences", {}),
            critique_log=data.get("critique_log", []),
            elicitation_log=data.get("elicitation_log", []),
            skill_log=data.get("skill_log", []),
            ethics=data.get("ethics", []),
            ethics_checked=data.get("ethics_checked", False),
            trace=data.get("trace", []),
        )
        for alt_id, row in data.get("assessments", {}).items():
            for crit_id, a in row.items():
                case.set_assessment(alt_id, crit_id, Assessment(**a))
        return case

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "CaseFile":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
