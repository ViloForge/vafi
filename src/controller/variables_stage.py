"""VariablesStage — controller-side glue for the C.3 variables substrate.

Bundles the pure `VariableMaterializer` with the I/O it needs at spawn time
(project-slug resolution + audit emission), so `controller.execute` stays small:

    plan = await stage.prepare(task)          # resolve slug + materialize
    if not plan.ok: fail-loud, do not spawn
    ... invoke(injection=plan) ...
    await stage.record(task, plan)            # best-effort audit emit

Design notes:
  * A task with no `variables:` → an empty `ok=True` plan (V16 no-op); the slug
    is NOT fetched (off the hot path).
  * Audit emission is **best-effort forensics** — a failure is logged, never
    raised, and never blocks task spawn (design §"audit is not a gate").
  * `secrets_snapshot` persistence is **deferred**: vtaskforge has no write
    surface for it yet (the `Task.secrets_snapshot` JSONField is not in any
    serializer). The plan still computes it; we log and move on.
"""
from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timezone
from typing import Callable

from variables.audit_emitter import HttpAuditEmitter
from variables.literal import LiteralBackend
from variables.materializer import InjectionPlan, VariableMaterializer
from variables.registry import BackendRegistry
from variables.vault import VaultBackend
from variables.vault_reader import KubernetesVaultReader

logger = logging.getLogger("controller.variables")


def resolve_vault_verify(config) -> bool | str:
    """Map config to httpx's ``verify`` for the Vault reader.

    Precedence (skip_verify is the explicit escape hatch and wins):
      * ``vault_skip_verify`` true  -> ``False`` (no TLS verification)
      * else ``vault_ca_cert`` set  -> the CA bundle path (verify against it)
      * else                        -> ``True`` (system trust store)
    """
    if config.vault_skip_verify:
        return False
    if config.vault_ca_cert:
        return config.vault_ca_cert
    return True


class VariablesStage:
    def __init__(
        self,
        materializer: VariableMaterializer,
        emitter,
        work_source,
        *,
        controller_env: str,
        role: str,
        controller_id: str,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._materializer = materializer
        self._emitter = emitter
        self._work_source = work_source
        self._controller_env = controller_env
        self._role = role
        self._controller_id = controller_id
        self._now = now or (lambda: datetime.now(timezone.utc).isoformat())

    @classmethod
    def from_config(cls, config, work_source, agent_token: str) -> "VariablesStage":
        reader = KubernetesVaultReader(
            config.vault_addr, verify=resolve_vault_verify(config)
        )
        registry = BackendRegistry()
        registry.register("vault", VaultBackend(reader))
        registry.register("literal", LiteralBackend())
        emitter = HttpAuditEmitter(config.vtf_api_url, agent_token)
        return cls(
            VariableMaterializer(registry),
            emitter,
            work_source,
            controller_env=config.controller_env,
            role=config.agent_role,
            controller_id=config.pod_name or config.agent_id or "vafi-controller",
        )

    async def prepare(self, task) -> InjectionPlan:
        """Resolve the project slug and materialize the task's variables."""
        variables = getattr(task, "variables", None) or []
        if not variables:
            return InjectionPlan(ok=True)  # V16 no-op — slug not even fetched
        slug = await self._work_source.get_project_slug(task.project_id)
        return self._materializer.materialize(
            variables_spec=variables,
            project_slug=slug,
            project_pk=task.project_id,
            role=self._role,
            controller_env=self._controller_env,
            controller_id=self._controller_id,
            timestamp=self._now(),
        )

    async def record(self, task, plan: InjectionPlan) -> None:
        """Best-effort: emit one audit row per Vault read (task FK filled in).

        Never raises — audit is forensics, not a spawn gate. Snapshot persistence
        is deferred until vtaskforge exposes a write surface.
        """
        for rec in plan.audit_records:
            try:
                await self._emitter.emit(dataclasses.replace(rec, task=task.id))
            except Exception as exc:  # noqa: BLE001 - best-effort by design
                logger.warning(
                    "variable audit emit failed for %s (task %s): %s",
                    rec.variable_name, task.id, exc,
                )
        if plan.snapshot:
            logger.debug(
                "secrets_snapshot computed for task %s (%d vars) — persistence deferred",
                task.id, len(plan.snapshot),
            )
