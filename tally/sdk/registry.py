from __future__ import annotations

from tally.policy.declaration import Policy

_REGISTRY: list[Policy] = []


def register(policy: Policy) -> None:
    _REGISTRY.append(policy)


def get_registry() -> list[Policy]:
    return list(_REGISTRY)
