"""Interaction channel abstraction (web UI plan B).

The pipeline stays a single sequential program with blocking asks; what
varies is where those asks go. A channel is bound per THREAD:

- unbound (CLI): ask() falls through to console input, print() untouched;
- bound (web session): ask() blocks on an answer queue while the prompt -
  typed, so the frontend can render buttons/editors instead of raw text -
  is emitted as an event; print() output is routed to the same event
  stream by StdoutRouter, keeping order (same thread, same queue).

Prompt kinds: "text" (meta.multiline for the opening description),
"yesno", "choice" (meta.choices=[{value,label}], meta.skip_label when
blank is meaningful), "number" (meta.min/max), "assessment" ('5' or
'3/5/6'), "list_edit" (meta.items; answers are the CLI commands '',
'd N', 'a text' - the backend loop is unchanged, the frontend merely
translates clicks into them).
"""

from __future__ import annotations

import queue
import threading
from typing import Any

_LOCAL = threading.local()

ASK_TIMEOUT_SECONDS = 45 * 60


class SessionClosed(Exception):
    """The web client went away while the pipeline waited for an answer."""


_CLOSE = object()


def bind(channel: "WebChannel") -> None:
    _LOCAL.channel = channel


def unbind() -> None:
    _LOCAL.channel = None


def current() -> "WebChannel | None":
    return getattr(_LOCAL, "channel", None)


def ask(prompt: str, kind: str = "text", **meta: Any) -> str:
    """Route a blocking question to the bound channel, or the console."""
    ch = current()
    if ch is not None:
        return ch.ask(prompt, kind, meta)
    if kind == "text":
        from .io_utils import read_answer

        return read_answer(prompt)
    return input(prompt).strip()


def phase(name: str) -> None:
    """Announce a pipeline phase; no-op on the console (headers are printed
    there anyway)."""
    ch = current()
    if ch is not None:
        ch.emit({"type": "phase", "name": name})


class WebChannel:
    """Queue pair for one web session: events out, answers in."""

    def __init__(self) -> None:
        self.events: queue.Queue = queue.Queue()
        self.answers: queue.Queue = queue.Queue()
        self._line_buffer = ""
        # Full interaction record (events + user answers, in order) - the
        # source for the saved session transcript.
        self.transcript: list[dict] = []

    # -- pipeline side -----------------------------------------------------

    def emit(self, event: dict) -> None:
        self.transcript.append(event)
        self.events.put(event)

    def say(self, text: str) -> None:
        if text:
            self.emit({"type": "output", "text": text})

    def ask(self, prompt: str, kind: str, meta: dict) -> str:
        self.flush()
        self.emit({"type": "prompt", "prompt": prompt.strip(), "kind": kind,
                   "meta": meta})
        answer = self.answers.get(timeout=ASK_TIMEOUT_SECONDS)
        if answer is _CLOSE:
            raise SessionClosed()
        return str(answer).strip()

    # -- stdout routing ----------------------------------------------------

    def write(self, text: str) -> None:
        self._line_buffer += text
        while "\n" in self._line_buffer:
            line, self._line_buffer = self._line_buffer.split("\n", 1)
            self.say(line.rstrip())

    def flush(self) -> None:
        if self._line_buffer.strip():
            self.say(self._line_buffer.rstrip())
        self._line_buffer = ""

    # -- server side -------------------------------------------------------

    def answer(self, value: str) -> None:
        self.transcript.append({"type": "answer", "value": str(value)})
        self.answers.put(value)

    def close(self) -> None:
        self.answers.put(_CLOSE)


class StdoutRouter:
    """Process-wide stdout proxy: writes from a channel-bound thread go to
    that channel; everything else passes through to the real stdout."""

    def __init__(self, original) -> None:
        self.original = original

    def write(self, text: str) -> None:
        ch = current()
        if ch is not None:
            ch.write(text)
        else:
            self.original.write(text)

    def flush(self) -> None:
        ch = current()
        if ch is not None:
            ch.flush()
        else:
            self.original.flush()

    def __getattr__(self, name):
        return getattr(self.original, name)
