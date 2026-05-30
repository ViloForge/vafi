"""VaultBackend — fetches `kind: vault` variables from Vault KV v2.

This module holds the **groundable-at-L1** logic: the hardcoded Vault path
convention and the mapping of a read outcome to a FetchResult. The actual Vault
I/O (K8s TokenRequest → Vault k8s-auth login → KV v2 read) lives behind an
injected `VaultReader` and is **grounded at L2 against a live Vault** on the dev
cluster (per external-contract-grounding — hvac / TokenRequest behaviour is not
asserted from mocks). The real `VaultReader` adapter lands with that L2 work.

α note: a single SA per role (`vtaskforge-executor` / `vtaskforge-judge`) with a
project-agnostic wildcard policy; the path encodes the project (slug). β swaps
in per-project SAs + policy templating — same paths, behind the same seam.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .types import (
    RESULT_NOT_FOUND,
    FetchResult,
    Role,
    VarName,
    VarRef,
)

# Hardcoded substrate name (design Point 12 — not parameterised in v1).
SUBSTRATE = "vtaskforge"


def vault_path(env: str, project_slug: str, role: Role, ref: VarRef) -> str:
    """Convention-derived KV path. `project_slug` is the K8s-safe Vault identity.

    project: secret/apps/vtaskforge/<env>/projects/<slug>/<role>/<name>
    shared:  secret/apps/vtaskforge/<env>/shared/<name>
    """
    if ref.scope == "shared":
        return f"secret/apps/{SUBSTRATE}/{env}/shared/{ref.name}"
    return f"secret/apps/{SUBSTRATE}/{env}/projects/{project_slug}/{role}/{ref.name}"


@dataclass
class ReadOutcome:
    """The outcome of one Vault read, as classified by the VaultReader adapter."""

    result: str                       # success | not_found | empty | unreachable | permission_denied
    value: bytes | None = None
    version: int | None = None


class VaultReader(Protocol):
    """The L2-grounded I/O seam: mint a role SA token, log into Vault, read a path.

    Classifies Vault responses into a ReadOutcome (404→not_found, 403→
    permission_denied, conn error→unreachable, present-but-blank→empty). The real
    implementation is grounded against a live Vault; tests inject a fake.
    """

    def read(self, role: Role, path: str) -> ReadOutcome:
        ...


class VaultBackend:
    """Resolves `kind: vault` variables via an injected VaultReader."""

    def __init__(self, reader: VaultReader):
        self._reader = reader

    def fetch(
        self, project_id: str, role: Role, refs: list[VarRef], controller_env: str
    ) -> dict[VarName, FetchResult]:
        # `project_id` here is the project's SLUG — the K8s-safe Vault-path
        # identity resolved by the controller (see C.2 §Q1).
        out: dict[VarName, FetchResult] = {}
        for ref in refs:
            if ref.kind != "vault":
                continue
            path = vault_path(controller_env, project_id, role, ref)
            outcome = self._reader.read(role, path)
            out[ref.name] = FetchResult(
                value=outcome.value,
                version=outcome.version,
                result=outcome.result,
                audit_metadata={
                    "kind": "vault",
                    "vault_path": path,
                    "scope": ref.scope,
                },
            )
        return out
