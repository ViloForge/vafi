"""Audit + snapshot emit core (C.3 Slice 4).

Two pure builders + an emitter seam:

  * ``build_audit_record`` — one forensic ``VariableAudit`` per Vault read. The
    record carries only metadata for grep + a Vault-audit join: NEVER the value,
    a hash of it, or a prefix of it (design §"Audit-log integrity"). The
    ``AuditRecord`` dataclass structurally has no value field, and ``to_body``
    emits exactly ``AUDIT_BODY_FIELDS`` — the writable subset of the vtaskforge
    ``VariableAuditSerializer`` (grounded against that serializer at L3).
  * ``build_secrets_snapshot`` — the per-task ``{variable_name: vault_version}``
    map persisted as ``Task.secrets_snapshot`` for replay/forensics. Includes
    every read that resolved to a real KV version (success or empty-but-present);
    literals and availability/value failures have no version and are excluded.
  * ``AuditEmitter`` — the I/O seam (append-only POST to vtaskforge). The HTTP
    adapter is grounded at L3 against a live vtaskforge; tests inject a fake.

Only ``kind: vault`` reads are audited — literals never touch Vault.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol

from .types import (
    RESULT_SUCCESS,
    FetchResult,
    VarName,
    VarRef,
)

# The writable fields of the vtaskforge VariableAuditSerializer (audit_id +
# created_at are server-side read-only). Asserted exhaustively in the L1 tests
# and bound to the real serializer at L3.
AUDIT_BODY_FIELDS = (
    "timestamp",
    "task",
    "project",
    "variable_name",
    "variable_scope",
    "vault_path",
    "vault_version",
    "result",
    "size_bytes",
    "duration_ms",
    "controller_id",
)


@dataclass(frozen=True)
class AuditRecord:
    """One forensic fetch-attempt row. No value/hash/prefix field exists."""

    timestamp: str            # controller wall clock, ISO 8601 UTC
    task: str | None          # vtaskforge Task PK (FK; null-safe)
    project: str | None       # vtaskforge Project PK (FK; null-safe)
    variable_name: str
    variable_scope: str       # "project" | "shared"
    vault_path: str | None
    vault_version: int | None
    result: str
    size_bytes: int | None    # value byte length on success; None otherwise
    duration_ms: int
    controller_id: str

    def to_body(self) -> dict:
        """The POST body for /v1/variable-audits/ — exactly AUDIT_BODY_FIELDS."""
        return asdict(self)


def build_audit_record(
    *,
    ref: VarRef,
    fetch_result: FetchResult,
    task_id: str | None,
    project_id: str | None,
    controller_id: str,
    timestamp: str,
    duration_ms: int,
) -> AuditRecord:
    """Build the forensic record for one Vault read. Never reads ``value`` into
    anything but its byte length on success."""
    is_success = fetch_result.result == RESULT_SUCCESS
    size_bytes = (
        len(fetch_result.value)
        if is_success and fetch_result.value is not None
        else None
    )
    meta = fetch_result.audit_metadata or {}
    scope = meta.get("scope", ref.scope)
    return AuditRecord(
        timestamp=timestamp,
        task=task_id,
        project=project_id,
        variable_name=ref.name,
        variable_scope=scope,
        vault_path=meta.get("vault_path"),
        vault_version=fetch_result.version,
        result=fetch_result.result,
        size_bytes=size_bytes,
        duration_ms=duration_ms,
        controller_id=controller_id,
    )


def build_secrets_snapshot(
    refs: list[VarRef], results: dict[VarName, FetchResult]
) -> dict[VarName, int]:
    """``{name: version}`` for every read that resolved to a real KV version.

    Captures replay data from day one (design §"Per-task version snapshot").
    Literals (version None) and not_found/unreachable/permission_denied (no
    version) are excluded.
    """
    snapshot: dict[VarName, int] = {}
    for ref in refs:
        res = results.get(ref.name)
        if res is not None and res.version is not None:
            snapshot[ref.name] = res.version
    return snapshot


class AuditEmitter(Protocol):
    """Append-only sink for audit records (vafi controller → vtaskforge).

    The real adapter POSTs each record to ``/v1/variable-audits/`` and is
    grounded at L3 against a live vtaskforge. Emission MUST NOT block task spawn
    on failure (audit is best-effort forensics, not a gate) — see the controller
    wiring in Slice 5.
    """

    async def emit(self, record: AuditRecord) -> None:
        ...
