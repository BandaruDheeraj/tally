# tally-billing

Turn Arize AX eval results into Stripe meter events — usage-based billing for AI agents, driven by output quality.

**How it works:** Your AI agent runs and sends traces to Arize. Tally watches those traces, reads the evaluation scores Arize computes, and fires Stripe meter events only when the agent actually delivered value. Bad output = no charge.

---

## Prerequisites

Before you start, you need accounts and API keys for three services:

| Service | What it's used for | Where to get it |
|---|---|---|
| **Arize AX** | Stores agent traces and eval results | [app.arize.com](https://app.arize.com) → Settings → API Keys |
| **Stripe** | Receives meter events for billing | [dashboard.stripe.com](https://dashboard.stripe.com) → Developers → API Keys |
| **Anthropic** or **OpenRouter** | LLM for the demo travel agent | [console.anthropic.com](https://console.anthropic.com) or [openrouter.ai](https://openrouter.ai) |

You also need **Python 3.12+**:
```bash
python --version  # must be 3.12 or higher
```

---

## Installation

```bash
git clone https://github.com/BandaruDheeraj/tally.git
cd tally
pip install -e .
```

For the demo web app, install the extra dependencies:
```bash
pip install -e ".[demo]"
```

---

## Quickstart — run the demo app

The demo is a travel agent web app that shows tally billing live as you use it.

### Step 1 — Set environment variables

```bash
export ARIZE_API_KEY="ak-..."           # from Arize Settings → API Keys
export ARIZE_SPACE_ID="U3BhY2U6..."     # from Arize Settings → Space ID
export ANTHROPIC_API_KEY="sk-ant-..."   # or use OPENROUTER_API_KEY instead
export TALLY_PROJECT="tally-demo"       # must match a project name in Arize
```

> **Shadow mode** (no Stripe charges): leave `STRIPE_KEY` unset. All billing decisions are computed and shown but no real charges are made. Set `STRIPE_KEY` only when you're ready to go live.

### Step 2 — Create a project in Arize

Log into [app.arize.com](https://app.arize.com), go to your space, and create a new project named `tally-demo` (or whatever you set `TALLY_PROJECT` to). Tally will send traces there.

### Step 3 — Start the demo

```bash
python -m uvicorn demo.app.main:app --port 8000
```

Open **http://localhost:8000** in your browser.

### Step 4 — Plan a trip

Type a destination (e.g. `Tokyo`) and your name, then click **Plan Trip**. Watch the pipeline run live:

1. The agent searches for flights and hotels
2. The LLM analyses the deals and rates their quality
3. A trace span is sent to Arize
4. Tally polls Arize until the eval result appears
5. The billing decision fires: **BILL 1 unit** if deals were good, **NO CHARGE** if not

The right panel shows a running billing ledger for your session.

### Step 5 — Test the no-charge path

Try a nonsense destination like `XYZZY_NOWHERE_12345`. The LLM will rate the results `poor` and tally will return **NO CHARGE** — the customer pays nothing when the agent doesn't deliver value.

---

## Using tally in your own project

### 1. Instrument your agent

Decorate the function you want to bill with `@tally.outcome`:

```python
import tally

@tally.outcome(span="my_agent_run", customer="user_id")
def run_agent(user_id: str, prompt: str) -> str:
    # your agent logic here
    return result
```

This sends a trace span to Arize with the customer ID attached.

### 2. Define a billing policy

Create a `pricing.py` file that describes when to charge and how much:

```python
import tally

def translate(evals):
    quality = evals.get("output_quality")
    if quality is not None and quality.label == "good":
        return bill(units=1)
    return no_charge(reason="output_quality=" + str(quality.label if quality else "none"))

policy = tally.policy(
    name="my-agent",          # must match your Stripe meter name
    span="my_agent_run",      # must match the span name in @tally.outcome
    customer="user_id",       # span attribute that holds the customer ID
    evals=["output_quality"], # eval names to wait for in Arize
    translate=translate,      # your billing logic
)
```

**`translate` rules:**
- Return `bill(units=N)` to charge N units
- Return `no_charge(reason="...")` to skip billing
- Return `hold(reason="...")` to defer the decision for manual review
- Only `bill`, `no_charge`, and `hold` are available — no imports, no side effects

### 3. Run the billing worker

```python
from tally.feed.ax import AXEvalSource
from tally.log.ax import AXRecordStore
from tally.worker import run_once
from datetime import datetime, timezone

source = AXEvalSource.from_env()   # reads ARIZE_API_KEY, ARIZE_SPACE_ID, ARIZE_PROJECT
store = AXRecordStore.from_env()

run_once(
    policies=[policy],
    source=source,
    store=store,
    cursor=datetime(2024, 1, 1, tzinfo=timezone.utc),
    stripe_key=None,        # set to your Stripe key to go live
    mode="shadow",          # "shadow" = dry run, "live" = real charges
)
```

Or run it as a continuous loop:

```python
from tally.worker import run

run(
    policies=[policy],
    source=source,
    store=store,
    stripe_key="sk_live_...",
    mode="live",
    sleep_seconds=60,       # poll every 60 seconds
)
```

### 4. Set up Stripe

1. Create a **meter** in Stripe Dashboard → Billing → Meters. The meter name must match your policy's `name` field (or set `meter="..."` explicitly in `tally.policy()`).
2. Attach the meter to a usage-based price on your subscription product.
3. Pass `stripe_key="sk_live_..."` and `mode="live"` to the worker.

### 5. Environment variables reference

| Variable | Required | Description |
|---|---|---|
| `ARIZE_API_KEY` | Yes | Arize API key |
| `ARIZE_SPACE_ID` | Yes | Arize space ID |
| `ARIZE_PROJECT` | Yes | Arize project name (for `AXEvalSource.from_env()`) |
| `TALLY_PROJECT` | No | Demo app project name (default: `tally-demo`) |
| `STRIPE_KEY` | Live mode only | Stripe restricted key with meter write access |
| `ANTHROPIC_API_KEY` | Demo only | Anthropic API key for the travel agent LLM |
| `OPENROUTER_API_KEY` | Demo only | Alternative to Anthropic via OpenRouter |

---

## How the billing pipeline works

```
Your agent runs
      │
      ▼
Span sent to Arize (via OTLP)
      │
      ▼
Arize evaluates the output (deal_quality, output_quality, etc.)
      │
      ▼
Tally polls Arize for eval results
      │
      ▼
translate(evals) → bill / no_charge / hold
      │
      ▼
Stripe meter event fired (in live mode)
```

Tally processes each span exactly once. Decisions are stored in a `RecordStore` (Arize or in-memory) so restarts are safe and nothing is double-billed.

---

## Development

```bash
pip install -e ".[dev]"
pytest tests/ --cov=tally --cov-fail-under=80
ruff check .
mypy tally/
```

---

## License

Apache 2.0
