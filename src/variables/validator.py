"""PreSpawnValidator — fail-loud, before any pod/process is created.

Implements the design's failure-mode matrix (vtaskforge-variables-DESIGN.md
§"Fail-loud taxonomy"). Invariants:
  - `unreachable` and `permission_denied` are ALWAYS fatal (availability/policy,
    not value-shape) — regardless of optional/required.
  - For value-shape outcomes: required `not_found`/`empty` → fatal; optional
    `not_found` → omitted; optional `empty` → injected as "".
If any required variable cannot be satisfied, the run is aborted with a
structured failure_detail and NOTHING is injected.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .types import (
    RESULT_EMPTY,
    RESULT_NOT_FOUND,
    RESULT_PERMISSION_DENIED,
    RESULT_UNREACHABLE,
    FetchResult,
    VarName,
    VarRef,
)


@dataclass
class ValidationOutcome:
    ok: bool
    failure_reason: str | None = None
    failure_detail: dict = field(default_factory=dict)
    injectable: dict[VarName, FetchResult] = field(default_factory=dict)


class PreSpawnValidator:
    def validate(
        self,
        refs: list[VarRef],
        results: dict[VarName, FetchResult],
        vault_paths: dict[VarName, str],
    ) -> ValidationOutcome:
        missing: list[str] = []
        empty: list[str] = []
        permission_denied: list[str] = []
        unreachable: list[str] = []
        injectable: dict[VarName, FetchResult] = {}

        for ref in refs:
            res = results.get(ref.name)
            # No result at all == could not be read == availability failure.
            if res is None or res.result == RESULT_UNREACHABLE:
                unreachable.append(ref.name)
                continue
            if res.result == RESULT_PERMISSION_DENIED:
                permission_denied.append(ref.name)  # SA/policy bug — always fatal
                continue
            if res.result == RESULT_NOT_FOUND:
                if ref.required:
                    missing.append(ref.name)
                # optional → omit
                continue
            if res.result == RESULT_EMPTY:
                if ref.required:
                    empty.append(ref.name)
                else:
                    injectable[ref.name] = res  # optional empty → inject ""
                continue
            # success
            injectable[ref.name] = res

        if not (unreachable or permission_denied or missing or empty):
            return ValidationOutcome(ok=True, injectable=injectable)

        if unreachable:
            reason = "vault_unreachable"
        elif permission_denied:
            reason = "permission_denied"
        else:
            reason = "missing_required_variables"

        flagged = missing + empty + permission_denied + unreachable
        detail = {
            "vault_unreachable": bool(unreachable),
            "missing": missing,
            "empty": empty,
            "permission_denied": permission_denied,
            "unreachable": unreachable,
            "vault_paths": {n: vault_paths.get(n) for n in flagged},
        }
        return ValidationOutcome(ok=False, failure_reason=reason, failure_detail=detail, injectable={})
