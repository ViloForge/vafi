"""The SecretBackend seam — the pluggable boundary for fetching secret values.

α uses VaultBackend (k8s-auth, Slice 3) + LiteralBackend. A future bare-VM/AWS
ExecutionBackend plugs in here without touching the fetch/validate/redact/audit
core. See docs/variables-substrate-C3-PLAN.md §"Execution model".
"""
from __future__ import annotations

from typing import Protocol

from .types import FetchResult, Role, VarName, VarRef


class SecretBackend(Protocol):
    def fetch(
        self,
        project_id: str,
        role: Role,
        refs: list[VarRef],
        controller_env: str,
    ) -> dict[VarName, FetchResult]:
        ...
