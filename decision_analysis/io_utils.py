"""Console input helpers.

read_answer() guards against multi-line pastes: when the user pastes text
containing newlines into a free-text prompt, plain input() returns only the
first line and the remaining lines silently answer the NEXT questions,
desynchronizing the Q&A record. On an interactive Windows console we detect
pending buffered lines right after input() returns and join them into one
answer. Piped stdin (tests, scripts) is left untouched.
"""

from __future__ import annotations

import sys


def read_answer(prompt: str) -> str:
    line = input(prompt)
    if sys.platform == "win32" and sys.stdin.isatty():
        try:
            import msvcrt

            extra: list[str] = []
            while msvcrt.kbhit():
                more = sys.stdin.readline().rstrip("\r\n")
                if more.strip():
                    extra.append(more.strip())
            if extra:
                line = " ".join([line.strip(), *extra])
                print(f"  (joined {len(extra)} pasted line(s) into one answer)")
        except (ImportError, OSError):
            pass
    return line.strip()
