"""LLM provider abstraction.

M1 ships only NullProvider so the whole pipeline runs without an API key:
every step that would need LLM judgment (natural-language understanding,
option generation, critique) is handled by the CLI wizard instead. The
Claude Agent SDK provider slots in here at M1.5 without touching the engine.
"""

from __future__ import annotations

from typing import Protocol


class LLMProvider(Protocol):
    available: bool

    def complete(self, prompt: str, system: str = "") -> str: ...

    def research(self, prompt: str, system: str = "") -> str: ...


class NullProvider:
    """No-LLM mode: signals the orchestrator to use structured wizard input."""

    available = False

    def complete(self, prompt: str, system: str = "") -> str:
        raise RuntimeError(
            "NullProvider cannot complete prompts; run in wizard mode "
            "or configure an LLM provider."
        )

    def research(self, prompt: str, system: str = "") -> str:
        raise RuntimeError("NullProvider cannot research; no LLM configured.")


class ClaudeSDKProvider:
    """LLM calls through the Claude Agent SDK (spawns the bundled claude CLI).

    Tools are fully disabled - we only need text in, text out. Each complete()
    is an independent one-shot query; conversation state lives in the case
    file, not in the model.
    """

    available = True

    def __init__(self, model: str | None = None) -> None:
        self.model = model

    def complete(self, prompt: str, system: str = "") -> str:
        import asyncio

        return asyncio.run(self._query(prompt, system, tools=[], max_turns=1))

    def research(self, prompt: str, system: str = "") -> str:
        """Separate research channel: a fresh query with the WebSearch tool
        enabled (the 'Researcher' role - search noise stays out of the main
        flow; only the final structured answer comes back)."""
        import asyncio

        return asyncio.run(
            self._query(prompt, system, tools=["WebSearch"], max_turns=15)
        )

    async def _query(
        self, prompt: str, system: str, tools: list[str], max_turns: int
    ) -> str:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            query,
        )

        options = ClaudeAgentOptions(
            system_prompt=system or None,
            tools=tools,
            allowed_tools=tools,
            max_turns=max_turns,
            model=self.model,
        )
        text_parts: list[str] = []
        result_text = ""
        # Let the iterator run to natural completion - an early return from
        # inside the async-for aborts the generator mid-flight and the SDK
        # logs an aclose() warning. ResultMessage is the final message anyway.
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    text = getattr(block, "text", None)
                    if text:
                        text_parts.append(text)
            elif isinstance(message, ResultMessage):
                if message.subtype != "success":
                    raise RuntimeError(f"LLM query failed: {message.subtype}")
                result_text = message.result or ""
        return result_text or "\n".join(text_parts)


class ScriptedProvider:
    """Testing provider: returns canned responses in order.

    Lets the full M1.5 LLM flow run offline (no key, no network) in tests
    and demos; also documents the exact JSON contracts each skill expects.
    """

    available = True

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, prompt: str, system: str = "") -> str:
        self.calls.append((system, prompt))
        if not self._responses:
            raise RuntimeError("ScriptedProvider ran out of canned responses")
        return self._responses.pop(0)

    def research(self, prompt: str, system: str = "") -> str:
        return self.complete(prompt, system)


def create_provider() -> "LLMProvider":
    """Return a working LLM provider, or NullProvider when the SDK is missing
    or no API key is configured (env or .env)."""
    import os

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return NullProvider()
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        return NullProvider()
    return ClaudeSDKProvider(model=os.environ.get("ANTHROPIC_MODEL") or None)
