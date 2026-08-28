"""Decision Analysis LLM - M1 minimal runnable pipeline.

LLM-agnostic core engine. All numeric work (swing weighting, MAUT,
sensitivity) is deterministic code; LLM judgment steps are replaced by a
CLI wizard until the Claude Agent SDK provider is plugged in.
"""

__version__ = "0.1.0"
