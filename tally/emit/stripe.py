from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Literal

import requests

from tally.feed.source import _parse_dt
from tally.log.records import Record, make_record
from tally.log.store import RecordStore

logger = logging.getLogger(__name__)

_STRIPE_METER_URL = "https://api.stripe.com/v1/billing/meter_events"
_OLD_DECISION_THRESHOLD = timedelta(days=35)


class StripeEmitter:
    def __init__(
        self,
        *,
        store: RecordStore,
        mode: Literal["shadow", "live"],
        stripe_key: str | None,
        meter: str,
    ) -> None:
        if mode == "live" and not stripe_key:
            raise ValueError("STRIPE_RESTRICTED_KEY is required in live mode")
        self._store = store
        self._mode = mode
        self._stripe_key = stripe_key
        self._meter = meter

    def _make_emission(self, r: Record, payload: dict[str, object]) -> Record:
        return make_record(
            type="EMISSION",
            trace_id=r.trace_id, span_id=r.span_id, customer_id=r.customer_id,
            policy_name=r.policy_name, policy_version=r.policy_version,
            policy_hash=r.policy_hash,
            payload=payload,
        )

    def emit(self) -> None:
        """Process all pending BILL decisions: emit Stripe events or shadow-write."""
        now = datetime.now(tz=timezone.utc)
        decisions = self._store.list(type="DECISION")
        emitted_spans = {
            (r.trace_id, r.span_id)
            for r in self._store.list(type="EMISSION")
        }

        pending: list[Record] = [
            r for r in decisions
            if r.payload.get("decision") == "BILL"
            and (r.trace_id, r.span_id) not in emitted_spans
        ]

        if not pending:
            return

        live_since: datetime | None = None
        if self._mode == "live":
            mode_records = self._store.list(type="MODE")
            live_switches = [
                _parse_dt(str(r.payload["switched_at"]))
                for r in mode_records
                if r.payload.get("mode") == "live" and "switched_at" in r.payload
            ]
            if live_switches:
                live_since = max(live_switches)

        # Parse decided_at once; apply mode gate; carry timestamp through to _emit_customer
        final_pending: list[tuple[Record, datetime]] = []
        for r in pending:
            decided_at_str = str(r.payload.get("decided_at", ""))
            decided_at = _parse_dt(decided_at_str) if decided_at_str else now
            if live_since is not None and decided_at <= live_since:
                self._store.write(self._make_emission(r, {
                    "stripe_identifier": f"{r.trace_id}::{r.span_id}",
                    "shadow": True,
                    "status": "shadow",
                }))
            else:
                final_pending.append((r, decided_at))

        if not final_pending:
            return

        by_customer: dict[str, list[tuple[Record, datetime]]] = {}
        for r, decided_at in final_pending:
            by_customer.setdefault(r.customer_id, []).append((r, decided_at))

        if len(by_customer) == 1:
            self._emit_customer(next(iter(by_customer.values())), now)
        else:
            with ThreadPoolExecutor(max_workers=len(by_customer)) as executor:
                for customer_records in by_customer.values():
                    executor.submit(self._emit_customer, customer_records, now)

    def _emit_customer(
        self, records: list[tuple[Record, datetime]], now: datetime
    ) -> None:
        for r, decided_at in records:
            if now - decided_at > _OLD_DECISION_THRESHOLD:
                self._store.write(self._make_emission(r, {
                    "stripe_identifier": f"{r.trace_id}::{r.span_id}",
                    "shadow": False,
                    "status": "failed",
                }))
                logger.warning(
                    "BILL decision too old to emit trace_id=%s span_id=%s",
                    r.trace_id, r.span_id,
                )
                continue

            identifier = f"{r.trace_id}::{r.span_id}"
            units_raw = r.payload.get("units", 1)
            units = int(units_raw) if isinstance(units_raw, (int, float)) else 1

            if self._mode == "shadow":
                self._store.write(self._make_emission(r, {
                    "stripe_identifier": identifier,
                    "shadow": True,
                    "status": "shadow",
                }))
            else:
                self._post_stripe(r, identifier, units, decided_at)

    def _post_stripe(
        self, r: Record, identifier: str, units: int, decided_at: datetime
    ) -> None:
        try:
            resp = requests.post(
                _STRIPE_METER_URL,
                headers={"Authorization": f"Bearer {self._stripe_key}"},
                data={
                    "event_name": self._meter,
                    "stripe_customer_id": r.customer_id,
                    "value": str(units),
                    "identifier": identifier,
                    "timestamp": str(int(decided_at.timestamp())),
                },
                timeout=30,
            )
            resp.raise_for_status()
            stripe_response: dict[str, object] = resp.json()
            status = "sent"
        except Exception:
            logger.exception(
                "Stripe emit failed trace_id=%s span_id=%s", r.trace_id, r.span_id
            )
            stripe_response = {}
            status = "failed"

        self._store.write(self._make_emission(r, {
            "stripe_identifier": identifier,
            "stripe_response": stripe_response,
            "shadow": False,
            "status": status,
        }))
