"""Core types for the variables substrate runtime (C.3).

Execution-model-agnostic: identical under the current k8s controller (α) and any
future ExecutionBackend (bare-VM/AWS). See docs/variables-substrate-C3-PLAN.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

Role = str        # "executor" | "judge"
VarName = str

# Per-variable fetch outcome (mirrors vtaskforge VariableAudit.result).
RESULT_SUCCESS = "success"
RESULT_NOT_FOUND = "not_found"
RESULT_EMPTY = "empty"
RESULT_UNREACHABLE = "unreachable"
RESULT_PERMISSION_DENIED = "permission_denied"


@dataclass
class FetchResult:
    """The outcome of fetching one variable from a backend.

    `value` is the raw bytes on success (None on failure). `result` classifies
    the outcome for the validator + audit.
    """

    value: bytes | None = None                # raw bytes — supports binary blobs
    version: int | None = None                # backend version (None for literal)
    audit_metadata: dict[str, Any] = field(default_factory=dict)
    result: str = RESULT_SUCCESS


@dataclass(frozen=True)
class VarRef:
    """A controller-side reference to one declared task variable.

    Parsed from the task spec's `variables:` shape with the design's defaults
    (source vault/project, target env = name, required = true). No Vault path is
    carried — it is convention-derived by the backend at fetch time.
    """

    name: str
    kind: str = "vault"           # "vault" | "literal"
    scope: str = "project"        # vault: "project" | "shared"
    value: str | None = None      # literal only
    version: int | None = None    # vault: opt-in pin
    target_env: str | None = None
    target_file: str | None = None
    required: bool = True

    @classmethod
    def list_from_spec(cls, variables) -> list["VarRef"]:
        """Parse the task-spec `variables:` list (list of dicts) into VarRefs."""
        if not variables:
            return []
        return [cls._one(entry) for entry in variables]

    @classmethod
    def _one(cls, entry: dict) -> "VarRef":
        name = entry["name"]
        source = entry.get("source") or {}
        kind = source.get("kind", "vault")

        # required: explicit `required`, else inverse of `optional`, else True.
        if "required" in entry:
            required = bool(entry["required"])
        elif "optional" in entry:
            required = not bool(entry["optional"])
        else:
            required = True

        # target: absent → default {env: name}; present → only what is given.
        if "target" not in entry:
            target_env, target_file = name, None
        else:
            target = entry.get("target") or {}
            target_env = target.get("env")
            target_file = target.get("file")

        return cls(
            name=name,
            kind=kind,
            scope=source.get("scope", "project"),
            value=source.get("value"),
            version=source.get("version"),
            target_env=target_env,
            target_file=target_file,
            required=required,
        )
