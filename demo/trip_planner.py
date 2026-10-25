"""Trip planner demo agent.

Searches the web for the best flight + hotel deals for a destination,
uses Claude to analyse and rank them, self-evaluates deal quality, then
sends an instrumented OTLP span to Arize so tally can bill per plan.

Usage:
    python demo/trip_planner.py --destination "Tokyo" --user alice
    python demo/trip_planner.py --destination "Paris" --destination "Rome" --user bob

Required env vars:
    ARIZE_API_KEY, ARIZE_SPACE_ID, ANTHROPIC_API_KEY
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

import requests

OTLP_URL = "https://otlp.arize.com/v1/traces"
SPAN_NAME = "plan_trip"
PROJECT = os.environ.get("TALLY_PROJECT", "tally-demo")


# ---------------------------------------------------------------------------
# Web search (DuckDuckGo — free, no API key)
# ---------------------------------------------------------------------------

def _search_deals(destination: str) -> list[dict[str, str]]:
    try:
        try:
            from ddgs import DDGS  # type: ignore[import]
        except ImportError:
            from duckduckgo_search import DDGS  # type: ignore[import]
    except ImportError:
        print("  [ddgs not installed — using stub results]")
        return [
            {"title": f"Cheap flights to {destination}",
             "body": f"Round-trip flights to {destination} from $299 — book now for the best prices.",
             "href": ""},
            {"title": f"Best hotels {destination} 2025",
             "body": f"Top-rated hotels in {destination} from $65/night. Free cancellation available.",
             "href": ""},
            {"title": f"{destination} travel deals package",
             "body": f"All-inclusive {destination} packages from $799pp including flights and 4★ hotel.",
             "href": ""},
        ]

    queries = [
        f"best flight deals {destination} cheap 2025",
        f"budget hotels {destination} deals 2025",
        f"{destination} travel package discount",
    ]
    results: list[dict[str, str]] = []
    try:
        with DDGS() as ddgs:
            for q in queries:
                try:
                    for r in ddgs.text(q, max_results=4):
                        results.append({
                            "title": r.get("title", ""),
                            "body": r.get("body", "")[:300],
                            "href": r.get("href", ""),
                        })
                except Exception:
                    pass
    except Exception:
        pass
    if not results:
        print(f"  [search returned no results — using stub]")
        results = [
            {"title": f"Cheap flights to {destination}",
             "body": f"Round-trip flights to {destination} from $299. Best deals available now.",
             "href": ""},
            {"title": f"Best budget hotels {destination}",
             "body": f"Top-rated hotels in {destination} from $65/night with free cancellation.",
             "href": ""},
        ]
    return results


# ---------------------------------------------------------------------------
# LLM analysis — OpenRouter (preferred) or Anthropic direct
# ---------------------------------------------------------------------------

def _extract_json(raw: str) -> str:
    """Strip markdown code fences if present, return the JSON string."""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        # drop first line (```json or ```) and last line (```)
        inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        raw = "\n".join(inner).strip()
    return raw


_PROMPT_TEMPLATE = """\
You are a travel deals expert. Based on the search results below, write a concise trip \
plan for {destination} that highlights the best deals found.

Search results:
{snippets}

Respond with ONLY a valid JSON object (no markdown):
{{
  "plan": "<2-3 paragraph trip plan with specific deal recommendations and estimated prices>",
  "deal_quality": "<good|poor>",
  "score": <0.0 to 1.0>
}}

Rules:
- "deal_quality" must be exactly "good" or "poor"
- "score" must be a number between 0.0 and 1.0
- "good" = the response gives the user actionable travel information (real deals, prices, or a clear honest explanation of what IS available)
- "poor" = the response is useless — no real information, only made-up prices, or completely generic filler with nothing actionable
- Rate based on output quality, not destination validity — a response that honestly explains limitations and offers what IS available is "good\""""


def _call_openrouter(prompt: str, api_key: str) -> str:
    """Call OpenRouter chat completions (OpenAI-compatible)."""
    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Title": "tally-trip-planner",
        },
        json={
            "model": "anthropic/claude-haiku-4-5",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1000,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return str(resp.json()["choices"][0]["message"]["content"]).strip()


def _call_anthropic(prompt: str, api_key: str) -> str:
    """Call Anthropic API directly."""
    import anthropic as _anthropic  # type: ignore[import]
    client = _anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    return str(msg.content[0].text).strip()


def _analyse_deals(
    destination: str,
    results: list[dict[str, str]],
) -> tuple[str, str, float]:
    """Return (plan_text, deal_quality_label, deal_quality_score)."""

    def _fallback() -> tuple[str, str, float]:
        plan = f"Trip plan for {destination}:\n"
        for r in results[:6]:
            plan += f"  • {r['title']}: {r['body'][:160]}\n"
        has_prices = any(
            any(p in r["body"] for p in ["$", "£", "€", "deal", "cheap", "discount", "off"])
            for r in results
        )
        return plan, ("good" if has_prices else "poor"), (0.75 if has_prices else 0.35)

    snippets = "\n".join(f"- {r['title']}: {r['body']}" for r in results[:9])
    prompt = _PROMPT_TEMPLATE.format(destination=destination, snippets=snippets)

    # Try OpenRouter first
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    if openrouter_key:
        print("  [using OpenRouter]")
        try:
            raw = _extract_json(_call_openrouter(prompt, openrouter_key))
            obj = json.loads(raw)
            return str(obj["plan"]), str(obj["deal_quality"]), float(obj["score"])
        except Exception as e:
            print(f"  [OpenRouter error: {e} — trying Anthropic]")

    # Try Anthropic direct
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        print("  [using Anthropic]")
        try:
            raw = _extract_json(_call_anthropic(prompt, anthropic_key))
            obj = json.loads(raw)
            return str(obj["plan"]), str(obj["deal_quality"]), float(obj["score"])
        except Exception as e:
            print(f"  [Anthropic error: {e} — using fallback]")

    if not openrouter_key and not anthropic_key:
        print("  [no LLM key set — using search-result fallback]")
    return _fallback()


# ---------------------------------------------------------------------------
# Arize OTLP write
# ---------------------------------------------------------------------------

def _send_span(
    api_key: str,
    space_id: str,
    user_id: str,
    session_id: str,
    destination: str,
    plan: str,
) -> tuple[str, str]:
    """Send a plan_trip span to Arize. Returns (trace_id, span_id).

    No eval attributes are embedded — Arize runs its own LLM evaluator
    (Deal Quality task) against input.value / output.value after ingest.
    """
    trace_id = uuid.uuid4().hex
    span_id = uuid.uuid4().hex[:16]
    now_ns = int(time.time() * 1_000_000_000)

    payload = {
        "resourceSpans": [{
            "resource": {"attributes": [
                {"key": "openinference.project.name",
                 "value": {"stringValue": PROJECT}},
            ]},
            "scopeSpans": [{"scope": {"name": "trip-planner"}, "spans": [{
                "traceId": trace_id.ljust(32, "0")[:32],
                "spanId": span_id.ljust(16, "0")[:16],
                "name": SPAN_NAME,
                "kind": 1,
                "startTimeUnixNano": now_ns - 5_000_000_000,
                "endTimeUnixNano": now_ns,
                "attributes": [
                    # OpenInference semantic conventions
                    {"key": "openinference.span.kind",
                     "value": {"stringValue": "CHAIN"}},
                    {"key": "session.id",
                     "value": {"stringValue": session_id}},
                    {"key": "user.id",
                     "value": {"stringValue": user_id}},
                    {"key": "input.value",
                     "value": {"stringValue": f"Plan a trip to {destination}"}},
                    {"key": "input.mime_type",
                     "value": {"stringValue": "text/plain"}},
                    {"key": "output.value",
                     "value": {"stringValue": plan}},
                    {"key": "output.mime_type",
                     "value": {"stringValue": "text/plain"}},
                    {"key": "destination",
                     "value": {"stringValue": destination}},
                ],
            }]}],
        }]
    }

    resp = requests.post(
        OTLP_URL,
        headers={
            "api_key": api_key,
            "space_id": space_id,
            "content-type": "application/json",
        },
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    return trace_id, span_id


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def plan_trip(
    destination: str,
    user_id: str,
    session_id: str,
    api_key: str,
    space_id: str,
) -> None:
    print(f"\n{'='*60}")
    print(f"Planning trip to: {destination}  (user: {user_id}  session: {session_id[:8]}...)")
    print(f"{'='*60}")

    print("Searching the web for deals...")
    results = _search_deals(destination)
    print(f"  Found {len(results)} results")

    print("Analysing deals...")
    plan, quality_label, quality_score = _analyse_deals(destination, results)

    print("\nTrip Plan:")
    print("-" * 60)
    print(plan.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8"))
    print("-" * 60)
    print(f"Deal quality : {quality_label}  (score={quality_score:.2f})")

    print("\nSending span to Arize...")
    trace_id, span_id = _send_span(
        api_key, space_id, user_id, session_id, destination, plan,
    )
    print(f"  OK  trace={trace_id[:12]}...  span={span_id[:12]}...")


def main() -> None:
    parser = argparse.ArgumentParser(description="Trip planner demo")
    parser.add_argument("--destination", action="append", required=True,
                        metavar="DEST", help="Destination (repeat for multiple)")
    parser.add_argument("--user", default="demo-user",
                        help="User/customer ID (default: demo-user)")
    args = parser.parse_args()

    api_key = os.environ.get("ARIZE_API_KEY", "")
    space_id = os.environ.get("ARIZE_SPACE_ID", "")

    if not api_key or not space_id:
        print("Error: set ARIZE_API_KEY and ARIZE_SPACE_ID", file=sys.stderr)
        sys.exit(1)

    # One session ID per script run — all destinations are grouped together in Arize
    session_id = uuid.uuid4().hex

    for dest in args.destination:
        plan_trip(dest, args.user, session_id, api_key, space_id)

    print(f"\nDone — {len(args.destination)} trip(s) sent to Arize project '{PROJECT}'")
    print("Run demo/trip_pricing.py to see tally billing decisions.")


if __name__ == "__main__":
    main()
