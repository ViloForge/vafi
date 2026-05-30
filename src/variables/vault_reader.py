"""KubernetesVaultReader — the L2-grounded VaultReader adapter (C.3 Slice 3).

Grounded 2026-05-30 against the LIVE viloforge dev Vault (HA 3/3,
``https://vault.vault.svc:8200``) from inside the ``vafi-executor`` pod. The
observed contract (do not re-derive from docs — this is what the real server
does):

  * **k8s auth login** — ``POST /v1/auth/<auth_mount>/login {role, jwt}`` →
    ``auth.client_token`` + ``auth.token_policies`` + ``auth.lease_duration``
    (executor role → policies ``[default, vtaskforge-executor]``, TTL 3600s).
  * **KV v2 read** — ``GET /v1/<kv_mount>/data/<rest>`` with ``X-Vault-Token`` →
    value at ``data.data.<value_key>`` and version at ``data.metadata.version``.
  * **Path infix** — KV v2 *requires* the ``data/`` infix; reading the raw
    convention path (no infix) is denied 403, NOT 404.
  * **Value key** — operator convention is ``value`` (``vault kv put
    .../NAME value=…``); one secret value per variable name.
  * **Outcome → status** — 200 ⇒ success; 200 with blank/absent value ⇒ empty;
    404 ⇒ not_found; 403 ⇒ permission_denied; connect/timeout ⇒ unreachable.

α token source: the executor/judge pod runs **as** the ``vtaskforge-<role>``
ServiceAccount, so the SA JWT is the pod's own projected token
(``DEFAULT_TOKEN_PATH``). β (per-task Job + per-project SA) swaps
``_read_sa_jwt`` for a k8s ``TokenRequest`` mint — the login + read contract
above is unchanged. That invariance is the whole point of the VaultReader seam.
"""
from __future__ import annotations

import time
from typing import Callable

import httpx

from .types import (
    RESULT_EMPTY,
    RESULT_NOT_FOUND,
    RESULT_PERMISSION_DENIED,
    RESULT_SUCCESS,
    RESULT_UNREACHABLE,
    Role,
)
from .vault import ReadOutcome

# Where a pod's projected ServiceAccount token lives (α: pod runs as the SA).
DEFAULT_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"  # noqa: S105

# Re-login this many seconds before the cached token's lease actually expires.
_TTL_SKEW_SECONDS = 30.0


class _CachedToken:
    __slots__ = ("token", "expires_at")

    def __init__(self, token: str, expires_at: float) -> None:
        self.token = token
        self.expires_at = expires_at


class KubernetesVaultReader:
    """Real ``VaultReader``: SA JWT → Vault k8s-auth login → KV v2 read.

    Long-lived (instantiated once in the controller). Caches one Vault token per
    Vault role, refreshing proactively before lease expiry and re-logging-in once
    on an unexpected 403 (token raced its own expiry) before classifying a read
    as ``permission_denied``.
    """

    def __init__(
        self,
        vault_addr: str,
        *,
        auth_mount: str = "kubernetes",
        kv_mount: str = "secret",
        role_prefix: str = "vtaskforge-",
        value_key: str = "value",
        token_path: str = DEFAULT_TOKEN_PATH,
        verify: bool | str = True,
        timeout: float = 10.0,
        http_client: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._addr = vault_addr.rstrip("/")
        self._auth_mount = auth_mount.strip("/")
        self._kv_mount = kv_mount.strip("/")
        self._role_prefix = role_prefix
        self._value_key = value_key
        self._token_path = token_path
        self._timeout = timeout
        self._clock = clock
        self._http = http_client or httpx.Client(verify=verify, timeout=timeout)
        self._tokens: dict[str, _CachedToken] = {}

    # -- public seam ---------------------------------------------------------

    def read(self, role: Role, path: str) -> ReadOutcome:
        """Read one KV v2 secret, returning a classified ``ReadOutcome``."""
        try:
            token = self._token_for(role)
        except _Unreachable:
            return ReadOutcome(result=RESULT_UNREACHABLE)
        except _AuthDenied:
            # SA not bound to the Vault role (config error) — always fatal.
            return ReadOutcome(result=RESULT_PERMISSION_DENIED)

        api_url = self._kv_data_url(path)
        try:
            outcome, retryable_403 = self._read_once(api_url, token)
        except _Unreachable:
            return ReadOutcome(result=RESULT_UNREACHABLE)

        if retryable_403:
            # Token may have raced its own expiry; force a fresh login once.
            self._tokens.pop(self._vault_role(role), None)
            try:
                token = self._token_for(role)
                outcome, _ = self._read_once(api_url, token)
            except _Unreachable:
                return ReadOutcome(result=RESULT_UNREACHABLE)
            except _AuthDenied:
                return ReadOutcome(result=RESULT_PERMISSION_DENIED)
        return outcome

    # -- token lifecycle -----------------------------------------------------

    def _vault_role(self, role: Role) -> str:
        return f"{self._role_prefix}{role}"

    def _read_sa_jwt(self) -> str:
        """α: the pod's own projected SA token. β swaps in a TokenRequest mint."""
        try:
            with open(self._token_path, encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError as exc:  # pragma: no cover - environment failure
            raise _Unreachable(f"SA token unreadable: {exc}") from exc

    def _token_for(self, role: Role) -> str:
        vault_role = self._vault_role(role)
        cached = self._tokens.get(vault_role)
        if cached is not None and self._clock() < cached.expires_at:
            return cached.token
        return self._login(role, vault_role)

    def _login(self, role: Role, vault_role: str) -> str:
        jwt = self._read_sa_jwt()
        url = f"{self._addr}/v1/auth/{self._auth_mount}/login"
        try:
            resp = self._http.post(url, json={"role": vault_role, "jwt": jwt})
        except httpx.TransportError as exc:
            raise _Unreachable(f"login transport error: {exc}") from exc
        if resp.status_code in (400, 403):
            raise _AuthDenied(f"login rejected ({resp.status_code}) for {vault_role}")
        if resp.status_code >= 500:
            raise _Unreachable(f"login server error {resp.status_code}")
        if resp.status_code != 200:
            raise _Unreachable(f"login unexpected status {resp.status_code}")
        auth = (resp.json() or {}).get("auth") or {}
        token = auth.get("client_token")
        if not token:
            raise _Unreachable("login returned no client_token")
        lease = float(auth.get("lease_duration") or 0)
        expires_at = self._clock() + max(lease - _TTL_SKEW_SECONDS, 0.0)
        self._tokens[vault_role] = _CachedToken(token, expires_at)
        return token

    # -- read + classify -----------------------------------------------------

    def _kv_data_url(self, path: str) -> str:
        """``<kv_mount>/apps/…`` → ``<addr>/v1/<kv_mount>/data/apps/…``.

        KV v2 splits data/metadata; the convention path carries the mount as its
        first segment, so we strip it and re-prefix with ``<mount>/data``.
        """
        rest = path
        prefix = f"{self._kv_mount}/"
        if rest.startswith(prefix):
            rest = rest[len(prefix):]
        return f"{self._addr}/v1/{self._kv_mount}/data/{rest}"

    def _read_once(self, api_url: str, token: str) -> tuple[ReadOutcome, bool]:
        """Returns (outcome, retryable_403). retryable_403 signals a re-login try."""
        try:
            resp = self._http.get(api_url, headers={"X-Vault-Token": token})
        except httpx.TransportError as exc:
            raise _Unreachable(f"read transport error: {exc}") from exc

        if resp.status_code == 200:
            return self._classify_200(resp.json()), False
        if resp.status_code == 404:
            return ReadOutcome(result=RESULT_NOT_FOUND), False
        if resp.status_code == 403:
            # Caller decides whether to re-login once before trusting this.
            return ReadOutcome(result=RESULT_PERMISSION_DENIED), True
        if resp.status_code >= 500:
            raise _Unreachable(f"read server error {resp.status_code}")
        # Any other status: treat as not_found-shaped value failure, not fatal.
        return ReadOutcome(result=RESULT_NOT_FOUND), False

    def _classify_200(self, body: dict) -> ReadOutcome:
        outer = body.get("data") or {}
        data = outer.get("data") or {}
        meta = outer.get("metadata") or {}
        version = meta.get("version")
        raw = data.get(self._value_key)
        if raw is None or raw == "":
            # Provisioned path but no usable value (blank, or wrong/absent key).
            return ReadOutcome(result=RESULT_EMPTY, value=b"", version=version)
        value = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
        return ReadOutcome(result=RESULT_SUCCESS, value=value, version=version)

    def close(self) -> None:  # pragma: no cover - lifecycle hook
        self._http.close()


class _Unreachable(Exception):
    """Internal: Vault could not be reached / responded unusably (→ unreachable)."""


class _AuthDenied(Exception):
    """Internal: k8s-auth login was rejected (SA not bound → permission_denied)."""
