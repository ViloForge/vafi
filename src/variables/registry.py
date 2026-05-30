"""BackendRegistry — dispatches variable refs to backends by source kind."""
from __future__ import annotations

from .types import FetchResult, Role, VarName, VarRef


class BackendRegistry:
    def __init__(self) -> None:
        self._backends: dict[str, object] = {}

    def register(self, kind: str, backend) -> None:
        self._backends[kind] = backend

    def fetch(
        self, project_id: str, role: Role, refs: list[VarRef], controller_env: str
    ) -> dict[VarName, FetchResult]:
        by_kind: dict[str, list[VarRef]] = {}
        for r in refs:
            by_kind.setdefault(r.kind, []).append(r)

        out: dict[VarName, FetchResult] = {}
        for kind, krefs in by_kind.items():
            if kind not in self._backends:
                raise KeyError(f"no backend registered for source kind '{kind}'")
            out.update(self._backends[kind].fetch(project_id, role, krefs, controller_env))
        return out
