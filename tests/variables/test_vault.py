"""L1 unit tests for VaultBackend path derivation + outcome mapping (C.3 Slice 3).

The REAL k8s-TokenRequest + hvac reader is grounded at L2 against a live Vault
(cluster-dependent) — not here. VaultBackend takes an injected reader, so its
path-derivation + result-mapping logic is L1-testable with a fake.
"""
from variables.types import VarRef
from variables.vault import ReadOutcome, VaultBackend, vault_path


def _refs(*specs):
    return VarRef.list_from_spec(list(specs))


class TestVaultPath:
    def test_project_scope(self):
        r = _refs({"name": "GH_TOKEN"})[0]
        assert (
            vault_path("dev", "abad", "executor", r)
            == "secret/apps/vtaskforge/dev/projects/abad/executor/GH_TOKEN"
        )

    def test_shared_scope(self):
        r = _refs({"name": "LLM_KEY", "source": {"kind": "vault", "scope": "shared"}})[0]
        assert vault_path("prod", "abad", "judge", r) == "secret/apps/vtaskforge/prod/shared/LLM_KEY"


class _FakeReader:
    def __init__(self, outcomes):
        self.outcomes = outcomes  # path -> ReadOutcome
        self.calls = []

    def read(self, role, path):
        self.calls.append((role, path))
        return self.outcomes.get(path, ReadOutcome(result="not_found"))


class TestVaultBackend:
    def test_success_maps_value_version_and_path(self):
        path = "secret/apps/vtaskforge/dev/projects/abad/executor/GH_TOKEN"
        reader = _FakeReader({path: ReadOutcome(result="success", value=b"ghp_x", version=4)})
        out = VaultBackend(reader).fetch("abad", "executor", _refs({"name": "GH_TOKEN"}), "dev")
        assert out["GH_TOKEN"].result == "success"
        assert out["GH_TOKEN"].value == b"ghp_x"
        assert out["GH_TOKEN"].version == 4
        assert out["GH_TOKEN"].audit_metadata["vault_path"] == path
        assert reader.calls == [("executor", path)]

    def test_not_found_default(self):
        out = VaultBackend(_FakeReader({})).fetch("abad", "executor", _refs({"name": "X"}), "dev")
        assert out["X"].result == "not_found"
        assert out["X"].value is None

    def test_propagates_failure_results(self):
        p = "secret/apps/vtaskforge/dev/projects/abad/executor/Y"
        for res in ("empty", "unreachable", "permission_denied"):
            reader = _FakeReader({p: ReadOutcome(result=res)})
            out = VaultBackend(reader).fetch("abad", "executor", _refs({"name": "Y"}), "dev")
            assert out["Y"].result == res

    def test_ignores_literal_refs(self):
        refs = _refs({"name": "LOG", "source": {"kind": "literal", "value": "info"}})
        assert VaultBackend(_FakeReader({})).fetch("abad", "executor", refs, "dev") == {}

    def test_uses_slug_and_role_in_path(self):
        reader = _FakeReader({})
        VaultBackend(reader).fetch("compass-group", "judge", _refs({"name": "T"}), "dev")
        assert reader.calls[0] == ("judge", "secret/apps/vtaskforge/dev/projects/compass-group/judge/T")
