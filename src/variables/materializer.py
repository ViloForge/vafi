"""VariableMaterializer — the spawn-time orchestrator (C.3 Slice 5 core).

Ties the agnostic seams together into one decision: given a task's declared
``variables:`` spec, produce an **injection plan** the controller applies before
spawning the harness — or a fail-loud refusal that aborts the spawn.

Pipeline (pure over injected seams — registry/validator; the real Vault I/O and
audit emit are grounded separately at L2/L3):

    parse spec → registry.fetch → PreSpawnValidator.validate
      ok=False → InjectionPlan(ok=False, …) + audits (NOTHING injected)
      ok=True  → build env + files from injectable, + audits + snapshot + redactor

Invariants:
  * A task with **no** ``variables:`` block yields an empty, ok=True plan — zero
    behaviour change (V16). The controller must short-circuit identically.
  * ``project_slug`` is the K8s-safe Vault-path identity; ``project_pk`` is the
    vtaskforge FK for audit rows (TaskInfo.project_id == the PK). They differ.
  * On any fatal outcome NOTHING is injected, but audit rows for every read are
    still produced (forensics of the failed attempt).
  * Only ``kind: vault`` reads produce audit rows (literals never touch Vault).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from .audit import AuditRecord, build_audit_record, build_secrets_snapshot
from .redactor import Redactor
from .registry import BackendRegistry
from .types import VarName, VarRef
from .validator import PreSpawnValidator
from .vault import vault_path


@dataclass(frozen=True)
class FileInjection:
    """A secret to be written to a file (relative to the task workdir) at spawn."""

    path: str
    content: bytes


@dataclass
class InjectionPlan:
    ok: bool
    env: dict[str, str] = field(default_factory=dict)
    files: list[FileInjection] = field(default_factory=list)
    audit_records: list[AuditRecord] = field(default_factory=list)
    snapshot: dict[VarName, int] = field(default_factory=dict)
    redactor: Redactor = field(default_factory=lambda: Redactor([]))
    failure_reason: str | None = None
    failure_detail: dict = field(default_factory=dict)


class VariableMaterializer:
    def __init__(
        self,
        registry: BackendRegistry,
        validator: PreSpawnValidator | None = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._registry = registry
        self._validator = validator or PreSpawnValidator()
        self._monotonic = monotonic

    def materialize(
        self,
        *,
        variables_spec: list[dict] | None,
        project_slug: str,
        project_pk: str | None,
        role: str,
        controller_env: str,
        controller_id: str,
        timestamp: str,
    ) -> InjectionPlan:
        refs = VarRef.list_from_spec(variables_spec or [])
        if not refs:
            return InjectionPlan(ok=True)  # V16 no-op — no variables declared

        t0 = self._monotonic()
        results = self._registry.fetch(project_slug, role, refs, controller_env)
        duration_ms = int((self._monotonic() - t0) * 1000)

        vault_paths = {
            r.name: vault_path(controller_env, project_slug, role, r)
            for r in refs
            if r.kind == "vault"
        }

        # Forensic audit row per Vault read (success OR failure), never literals.
        audit_records = [
            build_audit_record(
                ref=r,
                fetch_result=results[r.name],
                task_id=None,  # the controller fills task FK at emit time
                project_id=project_pk,
                controller_id=controller_id,
                timestamp=timestamp,
                duration_ms=duration_ms,
            )
            for r in refs
            if r.kind == "vault" and r.name in results
        ]
        snapshot = build_secrets_snapshot(refs, results)
        redactor = Redactor.from_results(refs, results)

        outcome = self._validator.validate(refs, results, vault_paths)
        if not outcome.ok:
            # Fail loud: nothing injected; audits + snapshot still surface.
            return InjectionPlan(
                ok=False,
                audit_records=audit_records,
                snapshot=snapshot,
                redactor=redactor,
                failure_reason=outcome.failure_reason,
                failure_detail=outcome.failure_detail,
            )

        env, files = self._bind(refs, outcome.injectable)
        return InjectionPlan(
            ok=True,
            env=env,
            files=files,
            audit_records=audit_records,
            snapshot=snapshot,
            redactor=redactor,
        )

    @staticmethod
    def _bind(refs, injectable) -> tuple[dict[str, str], list[FileInjection]]:
        """Bind each injectable value to its env var and/or file target.

        One value, one-or-more bindings (design Point 5). env values decode with
        surrogateescape so an arbitrary-byte secret survives the POSIX env round
        trip; binary blobs should prefer a file binding.
        """
        env: dict[str, str] = {}
        files: list[FileInjection] = []
        for ref in refs:
            res = injectable.get(ref.name)
            if res is None:
                continue  # optional not_found → omitted
            value = res.value or b""
            if ref.target_env:
                env[ref.target_env] = value.decode("utf-8", "surrogateescape")
            if ref.target_file:
                files.append(FileInjection(path=ref.target_file, content=value))
        return env, files
