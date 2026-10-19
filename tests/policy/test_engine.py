from __future__ import annotations

from datetime import datetime, timedelta

from tally.feed.source import EvalResult
from tally.policy.declaration import Policy, compute_version
from tally.policy.engine import Unit, decide

# ── shared helpers ────────────────────────────────────────────────────────────

NOW = datetime(2024, 6, 1, 12, 0)


def _eval(
    name: str,
    label: str = "yes",
    agreement: str | None = "unanimous",
) -> EvalResult:
    return EvalResult(
        name=name,
        label=label,
        score=1.0,
        explanation="",
        agreement=agreement,
        evaluator_version=None,
        scored_at=datetime(2024, 1, 1),
        raw={},
    )


def _policy(translate_source: str, watch: list[str] | None = None) -> Policy:
    """Build a Policy directly from source text — no inspect.getsource needed."""
    _watch = watch or []
    version = compute_version("p", "my_span", ["quality"], _watch, translate_source)
    return Policy(
        name="p",
        span="my_span",
        customer="customer_id",
        evals=["quality"],
        watch=_watch,
        translate=translate_source,
        version=version,
        meter="p",
        wait=timedelta(days=7),
    )


def _unit(policy: Policy, deadline_days: float = 1.0) -> Unit:
    return Unit(
        trace_id="trace-abc",
        span_id="span-xyz",
        customer_id="cust-1",
        policy_name=policy.name,
        policy_version=policy.version,
        first_seen=NOW,
        deadline=NOW + timedelta(days=deadline_days),
    )


# ── Rule 1: stay pending when declared eval missing and deadline not passed ───


def test_rule1_pending_missing_eval_before_deadline() -> None:
    policy = _policy("def translate(evals):\n    return bill(units=1)\n")
    unit = _unit(policy, deadline_days=1.0)
    result = decide(policy, unit, evals={}, watch={}, now=NOW)
    assert result is None


# ── Rule 2: NO_CHARGE when declared eval missing and deadline passed ──────────


def test_rule2_no_charge_missing_eval_after_deadline() -> None:
    policy = _policy("def translate(evals):\n    return bill(units=1)\n")
    unit = _unit(policy, deadline_days=-1.0)  # deadline 1 day in the past
    result = decide(policy, unit, evals={}, watch={}, now=NOW)
    assert result is not None
    assert result.outcome == "NO_CHARGE"
    assert result.reason == "unscored"


# ── Rule 3: ESCALATE when declared eval has split agreement ──────────────────


def test_rule3_escalate_on_split_declared_eval() -> None:
    policy = _policy("def translate(evals):\n    return bill(units=1)\n")
    unit = _unit(policy)
    evals = {"quality": _eval("quality", agreement="split")}
    result = decide(policy, unit, evals=evals, watch={}, now=NOW)
    assert result is not None
    assert result.outcome == "ESCALATE"


# ── Rule 4a: HOLD when translate raises an exception ─────────────────────────


def test_rule4a_hold_translate_raises() -> None:
    policy = _policy("def translate(evals):\n    raise ValueError('bad input')\n")
    unit = _unit(policy)
    evals = {"quality": _eval("quality")}
    result = decide(policy, unit, evals=evals, watch={}, now=NOW)
    assert result is not None
    assert result.outcome == "HOLD"
    assert result.reason == "translate error"


# ── Rule 4b: HOLD when translate returns an invalid type ─────────────────────


def test_rule4b_hold_translate_invalid_return_type() -> None:
    policy = _policy("def translate(evals):\n    return 42\n")
    unit = _unit(policy)
    evals = {"quality": _eval("quality")}
    result = decide(policy, unit, evals=evals, watch={}, now=NOW)
    assert result is not None
    assert result.outcome == "HOLD"
    assert result.reason == "translate error"


# ── Rule 4c: HOLD when translate returns different values on two calls ────────
# Uses a mutable default argument to increment a counter across calls.
# exec runs once and the function is called twice in the same namespace,
# so the mutable default persists and produces different results on the second call.


def test_rule4c_hold_translate_nondeterministic() -> None:
    src = (
        "def translate(evals, _state=[0]):\n"
        "    _state[0] += 1\n"
        "    return bill(units=_state[0])\n"
    )
    policy = _policy(src)
    unit = _unit(policy)
    evals = {"quality": _eval("quality")}
    result = decide(policy, unit, evals=evals, watch={}, now=NOW)
    assert result is not None
    assert result.outcome == "HOLD"
    assert result.reason == "translate error"


# ── Rule 5: BILL when translate returns bill(units=n) ────────────────────────


def test_rule5_bill_returns_correct_units() -> None:
    policy = _policy("def translate(evals):\n    return bill(units=3)\n")
    unit = _unit(policy)
    evals = {"quality": _eval("quality")}
    result = decide(policy, unit, evals=evals, watch={}, now=NOW)
    assert result is not None
    assert result.outcome == "BILL"
    assert result.units == 3


def test_rule5_bill_uses_eval_label_in_translate() -> None:
    src = (
        "def translate(evals):\n"
        "    if evals['quality'].label == 'yes':\n"
        "        return bill(units=1)\n"
        "    return no_charge(reason='low quality')\n"
    )
    policy = _policy(src)
    unit = _unit(policy)

    evals_yes = {"quality": _eval("quality", label="yes")}
    result_yes = decide(policy, unit, evals=evals_yes, watch={}, now=NOW)
    assert result_yes is not None and result_yes.outcome == "BILL"

    evals_no = {"quality": _eval("quality", label="no")}
    result_no = decide(policy, unit, evals=evals_no, watch={}, now=NOW)
    assert result_no is not None and result_no.outcome == "NO_CHARGE"


# ── Rule 6: NO_CHARGE when translate returns no_charge(reason) ───────────────


def test_rule6_no_charge_from_translate() -> None:
    policy = _policy(
        "def translate(evals):\n    return no_charge(reason='free tier')\n"
    )
    unit = _unit(policy)
    evals = {"quality": _eval("quality")}
    result = decide(policy, unit, evals=evals, watch={}, now=NOW)
    assert result is not None
    assert result.outcome == "NO_CHARGE"
    assert result.reason == "free tier"


# ── Rule 7: HOLD when translate returns hold(reason) ─────────────────────────


def test_rule7_hold_from_translate() -> None:
    policy = _policy(
        "def translate(evals):\n    return hold(reason='needs manual review')\n"
    )
    unit = _unit(policy)
    evals = {"quality": _eval("quality")}
    result = decide(policy, unit, evals=evals, watch={}, now=NOW)
    assert result is not None
    assert result.outcome == "HOLD"
    assert result.reason == "needs manual review"


# ── Review flag: set when a watch eval has split agreement ───────────────────


def test_review_flag_set_for_split_watch_eval() -> None:
    policy = _policy(
        "def translate(evals):\n    return bill(units=1)\n",
        watch=["sentiment"],
    )
    unit = _unit(policy)
    evals = {"quality": _eval("quality")}
    watch = {"sentiment": _eval("sentiment", agreement="split")}
    result = decide(policy, unit, evals=evals, watch=watch, now=NOW)
    assert result is not None
    assert result.review is True
    assert result.outcome == "BILL"  # watch split does not change outcome


def test_review_flag_false_when_watch_unanimous() -> None:
    policy = _policy(
        "def translate(evals):\n    return bill(units=1)\n",
        watch=["sentiment"],
    )
    unit = _unit(policy)
    evals = {"quality": _eval("quality")}
    watch = {"sentiment": _eval("sentiment", agreement="unanimous")}
    result = decide(policy, unit, evals=evals, watch=watch, now=NOW)
    assert result is not None
    assert result.review is False


# ── Determinism: identical inputs always produce equal Decision objects ───────


def test_identical_inputs_produce_equal_decisions() -> None:
    policy = _policy("def translate(evals):\n    return bill(units=2)\n")
    unit = _unit(policy)
    evals = {"quality": _eval("quality")}
    r1 = decide(policy, unit, evals=evals, watch={}, now=NOW)
    r2 = decide(policy, unit, evals=evals, watch={}, now=NOW)
    assert r1 == r2


# ── Eval snapshot: decision records which evals were used ────────────────────


def test_decision_contains_eval_snapshot() -> None:
    policy = _policy("def translate(evals):\n    return bill(units=1)\n")
    unit = _unit(policy)
    evals = {"quality": _eval("quality", label="yes")}
    result = decide(policy, unit, evals=evals, watch={}, now=NOW)
    assert result is not None
    assert "quality" in result.eval_snapshot
    snap = result.eval_snapshot["quality"]
    assert snap["label"] == "yes"


# ── decided_at is set to the `now` argument passed in ────────────────────────


def test_decided_at_matches_now_argument() -> None:
    policy = _policy("def translate(evals):\n    return bill(units=1)\n")
    unit = _unit(policy)
    evals = {"quality": _eval("quality")}
    result = decide(policy, unit, evals=evals, watch={}, now=NOW)
    assert result is not None
    assert result.decided_at == NOW
