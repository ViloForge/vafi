"""L1 unit tests for the audit + snapshot emit core (C.3 Slice 4).

Pure builders: a FetchResult set → forensic VariableAudit POST bodies + the
per-task `{name: version}` secrets_snapshot. The I/O emitter is grounded
separately at L3 against a live vtaskforge (test_audit_l3 / vtaskforge side).

The load-bearing invariant: an audit body NEVER carries the value, a hash of it,
or a prefix of it (design §"Audit-log integrity"). VariableAudit on the
vtaskforge side has no such field; this asserts the vafi side can't smuggle one.
"""
import httpx
import pytest

from variables.audit import (
    AUDIT_BODY_FIELDS,
    AuditRecord,
    build_audit_record,
    build_secrets_snapshot,
)
from variables.audit_emitter import HttpAuditEmitter
from variables.types import FetchResult, VarRef


def _ref(name="GH_TOKEN", **kw):
    return VarRef.list_from_spec([{"name": name, **kw}])[0]


def _vault_success(value=b"ghp_secretvalue", version=7, path=None):
    path = path or "secret/apps/vtaskforge/dev/projects/abad/executor/GH_TOKEN"
    return FetchResult(
        value=value,
        version=version,
        result="success",
        audit_metadata={"kind": "vault", "vault_path": path, "scope": "project"},
    )


class TestBuildAuditRecord:
    def test_success_sets_size_and_version_and_path(self):
        fr = _vault_success(value=b"ghp_xxxxx", version=7)
        rec = build_audit_record(
            ref=_ref(),
            fetch_result=fr,
            task_id="tk_123",
            project_id="proj_pk_1",
            controller_id="vafi-executor-abc",
            timestamp="2026-05-30T12:00:00Z",
            duration_ms=42,
        )
        assert rec.variable_name == "GH_TOKEN"
        assert rec.variable_scope == "project"
        assert rec.result == "success"
        assert rec.size_bytes == len(b"ghp_xxxxx")
        assert rec.vault_version == 7
        assert rec.vault_path.endswith("/executor/GH_TOKEN")
        assert rec.task == "tk_123"
        assert rec.project == "proj_pk_1"
        assert rec.duration_ms == 42
        assert rec.controller_id == "vafi-executor-abc"

    def test_failure_has_null_size(self):
        for result in ("not_found", "empty", "unreachable", "permission_denied"):
            fr = FetchResult(
                value=None,
                version=None,
                result=result,
                audit_metadata={"vault_path": "secret/apps/vtaskforge/dev/projects/abad/executor/X"},
            )
            rec = build_audit_record(
                ref=_ref("X"),
                fetch_result=fr,
                task_id="tk",
                project_id="p",
                controller_id="c",
                timestamp="2026-05-30T12:00:00Z",
                duration_ms=5,
            )
            assert rec.size_bytes is None
            assert rec.result == result

    def test_shared_scope_preserved(self):
        fr = _vault_success(path="secret/apps/vtaskforge/dev/shared/LLM_KEY")
        fr.audit_metadata["scope"] = "shared"
        rec = build_audit_record(
            ref=_ref("LLM_KEY", source={"kind": "vault", "scope": "shared"}),
            fetch_result=fr,
            task_id="tk",
            project_id="p",
            controller_id="c",
            timestamp="2026-05-30T12:00:00Z",
            duration_ms=1,
        )
        assert rec.variable_scope == "shared"


class TestNoValueLeak:
    def test_body_never_contains_value_or_hash_keys(self):
        rec = build_audit_record(
            ref=_ref(),
            fetch_result=_vault_success(value=b"ghp_TOPSECRET"),
            task_id="tk",
            project_id="p",
            controller_id="c",
            timestamp="2026-05-30T12:00:00Z",
            duration_ms=1,
        )
        body = rec.to_body()
        # No value-bearing key of any spelling.
        for forbidden in ("value", "hash", "prefix", "secret", "data"):
            assert not any(forbidden in k.lower() for k in body), f"leaky key matching {forbidden!r}: {list(body)}"
        # And the secret bytes appear nowhere in the serialized body.
        assert "TOPSECRET" not in str(body)

    def test_body_keys_are_exactly_the_contract_fields(self):
        rec = build_audit_record(
            ref=_ref(),
            fetch_result=_vault_success(),
            task_id="tk",
            project_id="p",
            controller_id="c",
            timestamp="2026-05-30T12:00:00Z",
            duration_ms=1,
        )
        assert set(rec.to_body()) == set(AUDIT_BODY_FIELDS)


class TestBuildSecretsSnapshot:
    def test_maps_name_to_version_for_versioned_reads(self):
        # Any read that resolved to a real KV version is pinned for replay —
        # success AND empty-but-present (the secret exists at version N, blank).
        refs = [_ref("GH_TOKEN"), _ref("NPM_TOKEN"), _ref("BLANK"), _ref("MISSING")]
        results = {
            "GH_TOKEN": _vault_success(version=7),
            "NPM_TOKEN": _vault_success(version=3),
            "BLANK": FetchResult(value=b"", version=2, result="empty"),
            "MISSING": FetchResult(result="not_found"),  # no version
        }
        assert build_secrets_snapshot(refs, results) == {"GH_TOKEN": 7, "NPM_TOKEN": 3, "BLANK": 2}

    def test_excludes_versionless(self):
        # literal (no version) + availability/value failures (no version) excluded.
        refs = [_ref("LIT", source={"kind": "literal", "value": "x"}), _ref("U"), _ref("D")]
        results = {
            "LIT": FetchResult(value=b"x", version=None, result="success"),
            "U": FetchResult(result="unreachable"),
            "D": FetchResult(result="permission_denied"),
        }
        assert build_secrets_snapshot(refs, results) == {}


class _FakeAsyncClient:
    """Captures the single POST the emitter makes; returns a canned response."""

    def __init__(self, status_code=201):
        self._status = status_code
        self.calls = []

    async def post(self, url, *, json, headers, timeout):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return httpx.Response(
            self._status,
            json={**json, "audit_id": "uuid-x"},
            request=httpx.Request("POST", url),
        )


def _record():
    return build_audit_record(
        ref=_ref(),
        fetch_result=_vault_success(),
        task_id="tk",
        project_id="p",
        controller_id="c",
        timestamp="2026-05-30T12:00:00Z",
        duration_ms=1,
    )


class TestHttpAuditEmitter:
    async def test_posts_body_to_audits_url_with_token(self):
        fake = _FakeAsyncClient(status_code=201)
        emitter = HttpAuditEmitter("https://vtf.example/", "tok123", http_client=fake)
        await emitter.emit(_record())
        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["url"] == "https://vtf.example/v1/variable-audits/"
        assert call["headers"]["authorization"] == "Token tok123"
        assert set(call["json"]) == set(AUDIT_BODY_FIELDS)

    async def test_raises_on_non_2xx_so_caller_can_log_and_continue(self):
        fake = _FakeAsyncClient(status_code=400)
        emitter = HttpAuditEmitter("https://vtf.example", "tok", http_client=fake)
        with pytest.raises(httpx.HTTPStatusError):
            await emitter.emit(_record())
