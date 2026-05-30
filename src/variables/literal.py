"""LiteralBackend — values declared inline in the task spec (kind: literal)."""
from __future__ import annotations

from .types import FetchResult, Role, VarName, VarRef


class LiteralBackend:
    """Resolves `source: {kind: literal, value: ...}` variables. No I/O."""

    def fetch(
        self, project_id: str, role: Role, refs: list[VarRef], controller_env: str
    ) -> dict[VarName, FetchResult]:
        out: dict[VarName, FetchResult] = {}
        for r in refs:
            if r.kind != "literal":
                continue
            out[r.name] = FetchResult(
                value=(r.value or "").encode("utf-8"),
                version=None,
                audit_metadata={"kind": "literal"},
            )
        return out
