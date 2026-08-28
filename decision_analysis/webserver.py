"""Web UI server (plan B): the unchanged pipeline runs in a session thread,
talking to the browser through a WebSocket-backed InteractionChannel.

Run:  python -m decision_analysis.webserver          (default port 8420)
Mock: DA_WEB_MOCK=1 ... - scripted LLM responses, no key, no cost; for UI
      testing only.

Security posture: local / trusted-LAN use. For a public deployment set
WEBUI_TOKEN in the environment - the WebSocket then requires ?token=...;
the API key never leaves the server.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .channel import SessionClosed, StdoutRouter, WebChannel, bind, unbind
from .config import load_env
from .orchestrator import CASES_DIR, run_llm
from .provider import ScriptedProvider, create_provider

WEB_DIR = Path(__file__).resolve().parent / "web"
TOKEN_COOKIE = "da_token"
MAX_SESSIONS = int(os.environ.get("DA_MAX_SESSIONS", "3"))

app = FastAPI(title="Decision Analysis")

_active_lock = threading.Lock()
_active_sessions = 0


def _required_token() -> str:
    return os.environ.get("WEBUI_TOKEN", "")


def _token_ok(supplied: str) -> bool:
    required = _required_token()
    return not required or hmac.compare_digest(supplied, required)


@app.middleware("http")
async def token_gate(request: Request, call_next):
    """Gate every HTTP path (index page and /cases reports) behind
    WEBUI_TOKEN. The token arrives once as ?token=... and is then carried
    by a cookie so chart/iframe subresources load without the query param.
    With WEBUI_TOKEN unset (local use) everything stays open."""
    required = _required_token()
    if not required:
        return await call_next(request)
    supplied = (request.query_params.get("token", "")
                or request.cookies.get(TOKEN_COOKIE, ""))
    if not hmac.compare_digest(supplied, required):
        return PlainTextResponse(
            "Access token required. Open the exact link you were given "
            "(it includes ?token=...).", status_code=403)
    response = await call_next(request)
    if request.cookies.get(TOKEN_COOKIE, "") != required:
        response.set_cookie(TOKEN_COOKIE, required, httponly=True,
                            samesite="lax")
    return response


def _mock_provider() -> ScriptedProvider:
    """Canned discrete-choice run for UI testing without an API key."""
    def j(obj) -> str:
        return json.dumps(obj, ensure_ascii=False)

    return ScriptedProvider([
        j({"statement": "Which laptop should I buy?",
           "decision_type": "discrete-choice", "stakes": "low",
           "reversibility": "reversible", "alternatives": [
               {"name": "MacBook Air", "description": "13-inch, M-series"},
               {"name": "ThinkPad X1", "description": "14-inch, Linux-friendly"}],
           "hard_constraints": [{"text": "Budget under $1,500", "quote": "under 1500"}],
           "objectives": [{"text": "Reliable daily driver", "quote": "daily work"}],
           "preference_hints": [], "context_facts": [],
           "detected_language": "en", "notes": ""}),
        j({"slot_updates": [
               {"slot": "objectives", "status": "filled", "value": "daily work machine"},
               {"slot": "constraint_scan", "status": "filled", "value": "budget $1500"},
               {"slot": "status_quo", "status": "filled", "value": "keep old laptop"},
               {"slot": "stakeholders", "status": "filled", "value": "self"}],
           "new_slots": [], "frame": {},
           "questions": [{"slot": "deadline", "text": "When do you need the new machine by?"}]}),
        j({"slot_updates": [{"slot": "deadline", "status": "filled", "value": "within a month"}],
           "new_slots": [], "frame": {}, "questions": []}),
        j({"alternatives": [{"name": "Keep current laptop",
                             "description": "Status-quo baseline", "feasibility_note": ""}],
           "criteria": [
               {"name": "Price", "direction": "min", "scale_hint": "USD"},
               {"name": "Performance", "direction": "max", "scale_hint": "1-10"},
               {"name": "Battery life", "direction": "max", "scale_hint": "hours"}]}),
        j({"issues": [{"severity": "note", "target": "criteria",
                       "text": "Repairability is not modeled", "suggested_fix": "note it"}]}),
        j({"concerns": []}),
        j({"risks": [{"severity": "medium",
                      "text": "The chosen laptop disappoints on real battery life",
                      "mitigation": "Check independent battery tests before buying"}]}),
        j({"narrative": "The mock analysis recommends the top-ranked laptop; "
                        "this is scripted data for UI testing."}),
    ])


def _session_thread(ch: WebChannel, mock: bool) -> None:
    bind(ch)
    try:
        provider = _mock_provider() if mock else create_provider()
        if not provider.available:
            ch.emit({"type": "error",
                     "text": "LLM mode unavailable: set ANTHROPIC_API_KEY in .env."})
            return
        html_path = run_llm(provider)
        ch.flush()
        report_url = ""
        if html_path:
            html_path = Path(html_path)
            rel = html_path.resolve().relative_to(CASES_DIR.resolve())
            report_url = "/cases/" + rel.as_posix()
            # Preserve the full interaction as part of the case record.
            from .casefile import CaseFile
            from .htmlreport import save_transcript

            case_dir = html_path.parent
            try:
                case = CaseFile.load(case_dir / "case.json")
                save_transcript(case, ch.transcript, case_dir)
            except Exception:
                pass
        ch.emit({"type": "done", "report_url": report_url})
    except SessionClosed:
        pass
    except Exception as exc:  # noqa: BLE001 - surface anything to the tester
        ch.flush()
        ch.emit({"type": "error", "text": f"{type(exc).__name__}: {exc}"})
    finally:
        unbind()


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    global _active_sessions

    supplied = (ws.query_params.get("token", "")
                or ws.cookies.get(TOKEN_COOKIE, ""))
    if not _token_ok(supplied):
        await ws.close(code=4403)
        return
    await ws.accept()
    with _active_lock:
        if _active_sessions >= MAX_SESSIONS:
            await ws.send_json({
                "type": "error",
                "text": "The server is at capacity right now, please try "
                        "again in a few minutes."})
            await ws.close()
            return
        _active_sessions += 1
    mock = bool(os.environ.get("DA_WEB_MOCK"))
    ch = WebChannel()
    thread = threading.Thread(target=_session_thread, args=(ch, mock), daemon=True)
    thread.start()

    loop = asyncio.get_running_loop()

    async def pump_events() -> None:
        while True:
            event = await loop.run_in_executor(None, ch.events.get)
            await ws.send_json(event)
            if event["type"] in ("done", "error"):
                return

    async def pump_answers() -> None:
        while True:
            data = await ws.receive_json()
            if data.get("type") == "answer":
                ch.answer(str(data.get("value", "")))

    events_task = asyncio.create_task(pump_events())
    answers_task = asyncio.create_task(pump_answers())
    try:
        done, _ = await asyncio.wait(
            {events_task, answers_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                raise exc
    except WebSocketDisconnect:
        pass
    finally:
        with _active_lock:
            _active_sessions -= 1
        ch.close()
        events_task.cancel()
        answers_task.cancel()


def main() -> None:
    import uvicorn

    load_env()
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/cases", StaticFiles(directory=str(CASES_DIR)), name="cases")
    sys.stdout = StdoutRouter(sys.stdout)
    # Railway (and most PaaS) inject PORT and need 0.0.0.0; local default
    # stays loopback-only on 8420.
    port = int(os.environ.get("PORT", os.environ.get("WEBUI_PORT", "8420")))
    host = os.environ.get("WEBUI_HOST") or (
        "0.0.0.0" if "PORT" in os.environ else "127.0.0.1")
    print(f"Decision Analysis web UI: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
