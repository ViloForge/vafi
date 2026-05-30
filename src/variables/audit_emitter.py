"""HttpAuditEmitter — the I/O adapter for VariableAudit emission (C.3 Slice 4).

Append-only POST of one ``AuditRecord`` to the vtaskforge
``/v1/variable-audits/`` endpoint (the receiver shipped in vtaskforge PR #18).
Auth mirrors the controller's existing vtf client: a ``Token <token>`` header
(the controller's agent token). Grounded at L3 against a live vtaskforge — the
body it posts is exactly ``AuditRecord.to_body()`` (the ``AUDIT_BODY_FIELDS``
subset of the real ``VariableAuditSerializer``).

Emission is best-effort forensics: callers (Slice 5 controller wiring) MUST NOT
let an emit failure block task spawn. This adapter raises on transport / non-2xx
so the caller can log-and-continue; it never retries inline.
"""
from __future__ import annotations

import httpx

from .audit import AuditRecord


class HttpAuditEmitter:
    """POSTs audit records to vtaskforge. Inject an ``httpx.AsyncClient`` for
    tests / connection reuse; otherwise one is created per call."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/v1/variable-audits/"
        self._headers = {"authorization": f"Token {token}"}
        self._http = http_client
        self._timeout = timeout

    async def emit(self, record: AuditRecord) -> None:
        body = record.to_body()
        if self._http is not None:
            resp = await self._http.post(
                self._url, json=body, headers=self._headers, timeout=self._timeout
            )
        else:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self._url, json=body, headers=self._headers, timeout=self._timeout
                )
        resp.raise_for_status()
