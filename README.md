# Decision Analysis LLM

An agentic decision-analysis assistant. Describe a decision you are facing in
plain language — in any language — and it frames the problem, asks only the
questions that still matter, elicits your preferences through simple choices
rather than abstract scores, analyzes the alternatives with the model that
fits your decision type, and delivers an audit-ready report with charts.

The division of labor is strict: an LLM (via the Claude Agent SDK) handles
judgment — understanding your situation, proposing alternatives and criteria,
critiquing the analysis — while all numbers come from deterministic Python:
scoring, optimization, Monte Carlo simulation, and sensitivity analysis are
reproducible and seed-stable.

## What it does

- **Understands the decision first.** Your free-text description is decomposed
  into a structured frame (objectives, constraints, alternatives, context).
  Follow-up questions are driven by an information-completeness checklist for
  your decision type — nothing you already said is asked again, and leaving an
  answer blank marks it as an explicit unknown instead of stalling the flow.
- **Adapts to the decision type.** Five types are supported, each routed to an
  appropriate analysis model:
  | Type | Example | Model |
  |---|---|---|
  | Discrete choice | Which job offer to take | Multi-attribute utility (MAUT) + Monte Carlo |
  | Go / no-go | Buy this used car or stay car-free | MAUT with an enforced status-quo baseline |
  | Timing | Start job hunting now or wait | MAUT over timing options vs. waiting |
  | Uncertain bet | Guaranteed appointment vs. fellowship application | Expected utility with a prospect-style value function |
  | Allocation | Split a bonus across savings, debt, investing | Deterministic power-utility optimizer with bounds |
- **Elicits preferences indirectly.** Criterion weights come from swing
  weighting; risk attitude from certainty-equivalent bets; loss aversion from
  50/50 deal questions; a scenario-pair check verifies the inferred weights
  actually match your judgment. You answer concrete either-or questions, not
  "rate this 1-10".
- **Researches facts on request.** With your per-session consent, unknowns
  that matter (market prices, policy rules, typical rates) are looked up via
  live web search, and every researched fact is cited in the report.
- **Quantifies uncertainty and its value.** Ranged estimates and unknowns
  flow into Monte Carlo ranking (with optional within-alternative
  correlation), a rank-probability view, and a value-of-information analysis:
  for each unresolved uncertainty, the probability that resolving it would
  change the recommendation — so you know which fact is worth checking before
  committing.
- **Critiques itself.** A clean-context critic reviews the frame and criteria,
  a premortem surfaces failure risks (severe risks on the winner downgrade the
  recommendation to a conditional one), and a lightweight ethics check flags
  concerns.
- **Reports for audit, not just persuasion.** Every report records the
  verbatim original request, the full Q&A record, per-item provenance (which
  statement came from you, the LLM, or a web source), the complete workflow
  log, and reproducibility metadata — as Markdown plus a self-contained
  interactive HTML page (sticky table of contents, collapsible appendices,
  inlined SVG charts, no external assets).

## Installation

Requires Python 3.10+ and an Anthropic API key for LLM mode.

```
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-5
```

Real environment variables take precedence over `.env`. The Agent SDK uses
the API key directly; a Claude app subscription login is not reused.

## Usage

### Command line

```
python -m decision_analysis
```

Describe your decision when prompted and answer the follow-up questions.
Two no-key modes exist: `--no-llm` runs a structured wizard (you fill in the
frame yourself; all math still works), and `--demo` runs a canned job-offer
case end to end.

### Web UI

```
python -m decision_analysis.webserver
```

Open http://127.0.0.1:8420 — the same pipeline in the browser, with buttons,
inline list editors, a phase stepper, and the final report embedded in the
page. `DA_WEB_MOCK=1` serves a scripted no-cost session for UI testing.

### Output

Each session writes `cases/<case-id>/`:

| File | Content |
|---|---|
| `case.json` | The complete case file — frame, answers, preferences, analysis, trace |
| `report.md` | The full report in Markdown |
| `report.html` | Self-contained interactive report (open in any browser) |
| `transcript.html` | The full interaction record (web sessions) |
| `charts/` | Standalone SVG charts |

## Deployment (Railway / any Docker host)

The included `Dockerfile` runs the web UI (Python plus Node.js for the Claude
Code CLI that the Agent SDK drives). On Railway: create a project from this
repo (the Dockerfile is auto-detected), then set the service variables:

| Variable | Value |
|---|---|
| `ANTHROPIC_API_KEY` | your API key (required) |
| `ANTHROPIC_MODEL` | optional model override |
| `WEBUI_TOKEN` | a long random string; users open `https://<app>/?token=<value>` |
| `DA_MAX_SESSIONS` | max concurrent analysis sessions (default 3) |
| `DA_CASES_DIR` | `/data/cases` with a persistent volume attached at `/data` (otherwise case output is lost on redeploy) |

The server binds `0.0.0.0:$PORT` automatically when the platform injects
`PORT`. With `WEBUI_TOKEN` set, every HTTP path and the WebSocket require the
token — including the `/cases` report files. Set a spend limit on the
Anthropic console: each session makes dozens of LLM and web-search calls.

## Project layout

| Path | Role |
|---|---|
| `decision_analysis/orchestrator.py` | Phase sequence: intake → frame → elicit → assess → analyze → report |
| `decision_analysis/casefile.py` | The shared case file (JSON blackboard) all phases read and write |
| `decision_analysis/llm_phases.py` | LLM-driven intake, questioning, generation, and critique |
| `decision_analysis/checklists.py` | Per-type information-completeness checklists that drive questioning |
| `decision_analysis/elicitation.py` | Swing weighting, certainty-equivalent and loss-aversion bisection |
| `decision_analysis/analysis.py` | MAUT scoring, sensitivity, correlation-aware Monte Carlo |
| `decision_analysis/decision_tree.py` | Expected-utility model for uncertain bets |
| `decision_analysis/allocation.py` | Deterministic allocation optimizer |
| `decision_analysis/voi.py` | Value-of-information analysis (EVPI / per-cell EVPPI) |
| `decision_analysis/charts.py` | Stdlib-only SVG charts |
| `decision_analysis/report.py` + `htmlreport.py` | Markdown report and interactive HTML rendering |
| `decision_analysis/webserver.py` + `web/index.html` | FastAPI + WebSocket web UI |
| `skills/` | Skill library — each `SKILL.md` body is the system prompt for one LLM phase |
