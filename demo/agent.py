"""Minimal demo agent using @tally.outcome."""
from __future__ import annotations

import tally


@tally.outcome("post_review_comment", customer="customer_id")
def review_pr(pr_id: str, customer_id: str) -> str:
    """Simulate a PR review agent call."""
    return f"Reviewed PR {pr_id} for customer {customer_id}"


if __name__ == "__main__":
    result = review_pr("PR-001", customer_id="cust-demo")
    print(result)
