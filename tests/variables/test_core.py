"""L1 unit tests for the variables agnostic core (C.3 Slice 1).

Pure in-process logic — no Vault, no K8s, no deployable surface (§3.2.5: L1 only).
"""
import pytest

from variables.literal import LiteralBackend
from variables.registry import BackendRegistry
from variables.types import FetchResult, VarRef


class TestVarRefParse:
    def test_minimal_defaults(self):
        refs = VarRef.list_from_spec([{"name": "GH_TOKEN"}])
        assert len(refs) == 1
        r = refs[0]
        assert r.name == "GH_TOKEN"
        assert r.kind == "vault"          # default source kind
        assert r.scope == "project"       # default vault scope
        assert r.required is True
        assert r.target_env == "GH_TOKEN"  # default binding: env = name
        assert r.target_file is None
        assert r.value is None

    def test_optional_flag(self):
        r = VarRef.list_from_spec([{"name": "SENTRY_DSN", "optional": True}])[0]
        assert r.required is False

    def test_required_false_explicit(self):
        r = VarRef.list_from_spec([{"name": "X", "required": False}])[0]
        assert r.required is False

    def test_literal_source(self):
        r = VarRef.list_from_spec(
            [{"name": "LOG", "source": {"kind": "literal", "value": "info"}}]
        )[0]
        assert r.kind == "literal"
        assert r.value == "info"

    def test_shared_scope(self):
        r = VarRef.list_from_spec(
            [{"name": "K", "source": {"kind": "vault", "scope": "shared"}}]
        )[0]
        assert r.scope == "shared"

    def test_vault_version_pin(self):
        r = VarRef.list_from_spec(
            [{"name": "K", "source": {"kind": "vault", "version": 3}}]
        )[0]
        assert r.version == 3

    def test_explicit_target_env(self):
        r = VarRef.list_from_spec([{"name": "K", "target": {"env": "ANTHROPIC_API_KEY"}}])[0]
        assert r.target_env == "ANTHROPIC_API_KEY"
        assert r.target_file is None

    def test_explicit_target_file_no_default_env(self):
        # Explicit target with only file → no env binding (not defaulted to name).
        r = VarRef.list_from_spec([{"name": "K", "target": {"file": "/home/agent/sa.json"}}])[0]
        assert r.target_file == "/home/agent/sa.json"
        assert r.target_env is None

    def test_empty_or_none(self):
        assert VarRef.list_from_spec([]) == []
        assert VarRef.list_from_spec(None) == []


class TestLiteralBackend:
    def test_fetch_returns_value_bytes(self):
        refs = VarRef.list_from_spec([{"name": "LOG", "source": {"kind": "literal", "value": "info"}}])
        out = LiteralBackend().fetch("proj", "executor", refs, "dev")
        assert set(out) == {"LOG"}
        assert out["LOG"].value == b"info"
        assert out["LOG"].version is None

    def test_ignores_non_literal_refs(self):
        refs = VarRef.list_from_spec([{"name": "GH_TOKEN"}])  # vault kind
        assert LiteralBackend().fetch("proj", "executor", refs, "dev") == {}


class TestBackendRegistry:
    def test_dispatch_literal(self):
        reg = BackendRegistry()
        reg.register("literal", LiteralBackend())
        refs = VarRef.list_from_spec([{"name": "LOG", "source": {"kind": "literal", "value": "info"}}])
        out = reg.fetch("proj", "executor", refs, "dev")
        assert out["LOG"].value == b"info"

    def test_unknown_kind_raises(self):
        reg = BackendRegistry()  # nothing registered
        refs = VarRef.list_from_spec([{"name": "GH_TOKEN"}])  # vault, unregistered
        with pytest.raises(KeyError):
            reg.fetch("proj", "executor", refs, "dev")

    def test_merges_multiple_kinds(self):
        reg = BackendRegistry()
        reg.register("literal", LiteralBackend())

        class _FakeVault:
            def fetch(self, project_id, role, refs, env):
                return {r.name: FetchResult(value=b"secret", version=1, audit_metadata={}) for r in refs}

        reg.register("vault", _FakeVault())
        refs = VarRef.list_from_spec([
            {"name": "GH_TOKEN"},
            {"name": "LOG", "source": {"kind": "literal", "value": "info"}},
        ])
        out = reg.fetch("proj", "executor", refs, "dev")
        assert out["GH_TOKEN"].value == b"secret"
        assert out["LOG"].value == b"info"
