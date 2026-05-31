"""HttpSnapshotWriter — persists the secrets snapshot to vtaskforge (C.3 #3).

After materializing a task's variables the controller holds a snapshot of
``{variable_name: vault_version}`` captured at spawn. This adapter POSTs it to
the vtaskforge controller-facing endpoint
``/v1/tasks/<id>/secrets-snapshot/`` (idempotent whole-value replace of
``Task.secrets_snapshot``), enabling forensic replay.

Mirrors ``HttpAuditEmitter``: ``Token <token>`` auth, raises on transport /
non-2xx so the caller (``VariablesStage.record``) can log-and-continue. Like
audit emission this is best-effort forensics — it MUST NOT block task spawn.
"""
from __future__ import annotations

import httpx


class HttpSnapshotWriter:
    """POSTs the secrets snapshot to vtaskforge. Inject an ``httpx.AsyncClient``
    for tests / connection reuse; otherwise one is created per call."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"authorization": f"Token {token}"}
        self._http = http_client
        self._timeout = timeout

    async def write(self, task_id: str, snapshot: dict) -> None:
        url = f"{self._base}/v1/tasks/{task_id}/secrets-snapshot/"
        body = {"snapshot": snapshot}
        if self._http is not None:
            resp = await self._http.post(
                url, json=body, headers=self._headers, timeout=self._timeout
            )
        else:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    url, json=body, headers=self._headers, timeout=self._timeout
                )
        resp.raise_for_status()
