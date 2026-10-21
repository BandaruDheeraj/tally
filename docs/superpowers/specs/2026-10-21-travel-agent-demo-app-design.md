# Travel Agent Demo App

**Date:** 2026-10-21  
**Status:** Approved

---

## Purpose

A full-stack web demo of the tally billing library that doubles as a real travel agent application. The app lets a user plan trips by destination, streams a live AI-generated trip plan, then shows exactly how tally evaluates the output and makes a billing decision — running the full production pipeline end-to-end (Agent → Arize → Tally → Shadow Stripe).

**Audiences:**
- **Investors / execs** — see a polished, working product that tells the tally story visually
- **Developers** — see the actual policy code, eval results, and pipeline stages that drive each billing decision

---

## Architecture

```
demo/app/
├── main.py          # FastAPI app — 3 routes + SSE stream handler
├── static/
│   ├── index.html   # single-page shell (no build step)
│   ├── app.js       # ~150 lines vanilla JS (SSE client, DOM updates)
│   └── style.css    # two-column layout + billing card styles
```

**Dependencies added to pyproject.toml:**
- `fastapi>=0.111`
- `uvicorn>=0.29`

**Reused from existing code (no duplication):**
- `demo/trip_planner._search_deals()` — DuckDuckGo deal search
- `demo/trip_planner._analyse_deals()` — LLM analysis → deal_quality label + score
- `demo/trip_planner._send_span()` — sends OTLP span to Arize
- `tally.feed.ax.AXEvalSource` — polls Arize GraphQL for eval results
- `tally.policy.engine.decide()` — billing decision
- `tally.log.memory.MemoryRecordStore` — in-process decision store

---

## UI Layout

Two-column layout, full viewport height.

### Left panel (65%) — Chat
- Header: "TravelAI" branding + "powered by tally" badge
- Message thread: user messages (destination input) and agent replies (streaming trip plan)
- Each agent reply has a **billing card** that appears below the plan once the decision arrives:
  - Green `BILL · 1 unit` badge or grey `NO CHARGE` badge
  - Eval line: `deal_quality = good (0.87)`
  - Reason: e.g. "Good deals found — charged 1 credit"
- Pipeline status indicator inside each agent message (shows current stage while streaming)
- Text input + Send button at the bottom

### Right panel (35%) — Tally Billing Ledger
- Title: "Tally Billing" + "shadow mode" pill
- Running table: Destination | Decision | Score | Units — one row per trip this session
- Totals footer: "3 billed · 1 no charge · $0 (shadow)"
- Collapsible **"How it works"** section showing the actual `translate()` policy function in a syntax-highlighted code block — developers can see exactly which rule drove each decision

---

## Data Flow

### Route: `POST /api/plan`

Request body: `{ "destination": "Tokyo", "user_id": "alice" }`

Returns an `text/event-stream` SSE response. Event types:

| Event      | Payload example                                              |
|------------|--------------------------------------------------------------|
| `status`   | `"Searching for deals..."`                                   |
| `chunk`    | `"Tokyo in October offers..."` (repeated — plan text)        |
| `status`   | `"Sending span to Arize..."`                                 |
| `status`   | `"Waiting for eval... (attempt 1)"`                          |
| `eval`     | `{ "label": "good", "score": 0.87 }`                         |
| `decision` | `{ "outcome": "BILL", "units": 1, "reason": "deal_quality=good" }` |
| `done`     | `{}`                                                         |

### Route: `GET /`
Serves `static/index.html`.

### Route: `GET /api/decisions`
Returns all decisions from `MemoryRecordStore` as JSON. Used on page reload to restore session ledger.

### Tally pipeline (full production flow)

1. `_search_deals(destination)` — DuckDuckGo search, runs in thread pool
2. `_analyse_deals(destination, results)` — LLM (OpenRouter or Anthropic) → `plan`, `deal_quality_label`, `deal_quality_score`
3. Plan text streamed to client via `chunk` events
4. `_send_span(...)` — OTLP span sent to Arize with eval attributes embedded
5. `AXEvalSource.get_evals(trace_id, span_id)` — polls Arize GraphQL every 5 seconds, streams `status` events per attempt, times out after 2 minutes
6. `decide(policy, unit, evals, ...)` — tally policy engine produces BILL / NO_CHARGE / HOLD
7. Decision written to `MemoryRecordStore`
8. `decision` SSE event fired; `done` event closes stream

### Error handling
- LLM failure → falls back to heuristic (`_analyse_deals` already has this)
- Arize span send failure → stream `status` event with error, continue to poll anyway (span may have been received)
- Arize eval poll timeout (2 min) → stream `decision` event with `outcome: "HOLD"` and reason `"eval_timeout"`
- Any unhandled exception in the stream handler → stream `status` event with error message, then `done`

---

## Credentials Required

Set as environment variables before running:

```
ARIZE_API_KEY      # required — send spans + poll evals
ARIZE_SPACE_ID     # required
ANTHROPIC_API_KEY  # required (or OPENROUTER_API_KEY)
TALLY_PROJECT      # optional, default: tally-demo
```

No `STRIPE_KEY` needed — runs in shadow mode only.

---

## Running the App

```bash
pip install -e ".[demo]"
uvicorn demo.app.main:app --reload --port 8000
# open http://localhost:8000
```

A `[demo]` extras group will be added to `pyproject.toml` with `fastapi` and `uvicorn`.

---

## Out of Scope

- Authentication / multi-user sessions (demo uses a fixed `user_id` from the input)
- Persistent storage across server restarts (MemoryRecordStore resets on restart)
- Live Stripe billing (shadow mode only)
- Arize write-back of billing decisions (AXRecordStore not used — MemoryRecordStore only)
