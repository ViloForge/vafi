"""L1 unit tests for VariableMaterializer — the spawn-time orchestrator (Slice 5).

Pure over injected seams: a fake backend registered for `vault`/`literal`. Asserts
the no-op (V16), the success injection plan (env/files/snapshot/audits/redactor),
and the fail-loud refusal (nothing injected, audits still produced).
"""
from variables.materializer import VariableMaterializer
from variables.registry import BackendRegistry
from variables.types import FetchResult, VarName
from variables.vault import ReadOutcome, VaultBackend
from variables.literal import LiteralBackend


class _FakeReader:
    def __init__(self, outcomes):
        self.outcomes = outcomes  # path -> ReadOutcome

    def read(self, role, path):
        return self.outcomes.get(path, ReadOutcome(result="not_found"))


def _registry(outcomes):
    reg = BackendRegistry()
    reg.register("vault", VaultBackend(_FakeReader(outcomes)))
    reg.register("literal", LiteralBackend())
    return reg


def _mat(outcomes):
    # deterministic clock: two calls (t0, t1) → 50ms
    ticks = iter([1.000, 1.050, 1.000, 1.050, 1.000, 1.050])
    return VariableMaterializer(_registry(outcomes), monotonic=lambda: next(ticks))


_P = "secret/apps/vtaskforge/dev/projects/abad/executor"
_COMMON = dict(
    project_slug="abad",
    project_pk="proj_pk_42",
    role="executor",
    controller_env="dev",
    controller_id="vafi-executor-x",
    timestamp="2026-05-30T12:00:00Z",
)


class TestNoOp:
    def test_no_variables_block_is_empty_ok_plan(self):
        plan = VariableMaterializer(_registry({})).materialize(variables_spec=None, **_COMMON)
        assert plan.ok and not plan.env and not plan.files
        assert not plan.audit_records and plan.snapshot == {}

    def test_empty_list_is_no_op(self):
        plan = VariableMaterializer(_registry({})).materialize(variables_spec=[], **_COMMON)
        assert plan.ok and not plan.env and not plan.audit_records


class TestSuccess:
    def test_env_and_file_bindings_snapshot_and_audits(self):
        outcomes = {
            f"{_P}/GH_TOKEN": ReadOutcome(result="success", value=b"ghp_longsecret", version=7),
            f"{_P}/SA_JSON": ReadOutcome(result="success", value=b'{"k":"v"}', version=2),
        }
        spec = [
            {"name": "GH_TOKEN"},  # default env-bound = name
            {"name": "SA_JSON", "target": {"file": "/home/agent/sa.json"}},
        ]
        plan = _mat(outcomes).materialize(variables_spec=spec, **_COMMON)
        assert plan.ok
        assert plan.env == {"GH_TOKEN": "ghp_longsecret"}
        assert len(plan.files) == 1 and plan.files[0].path == "/home/agent/sa.json"
        assert plan.files[0].content == b'{"k":"v"}'
        assert plan.snapshot == {"GH_TOKEN": 7, "SA_JSON": 2}
        # one audit per vault read, carrying the project PK + batch duration.
        assert {a.variable_name for a in plan.audit_records} == {"GH_TOKEN", "SA_JSON"}
        assert all(a.project == "proj_pk_42" for a in plan.audit_records)
        assert all(a.duration_ms == 50 for a in plan.audit_records)
        # redactor masks the >=8-byte vault value.
        assert "[MASKED:GH_TOKEN]" in plan.redactor.redact("leak ghp_longsecret here")

    def test_literal_injected_without_vault_audit(self):
        spec = [{"name": "LOG_LEVEL", "source": {"kind": "literal", "value": "info"}}]
        plan = _mat({}).materialize(variables_spec=spec, **_COMMON)
        assert plan.ok
        assert plan.env == {"LOG_LEVEL": "info"}
        assert plan.audit_records == []  # literals never touch Vault
        assert plan.snapshot == {}  # no version

    def test_optional_not_found_is_omitted(self):
        spec = [{"name": "MAYBE", "optional": True}]
        plan = _mat({}).materialize(variables_spec=spec, **_COMMON)
        assert plan.ok
        assert "MAYBE" not in plan.env
        assert plan.audit_records[0].result == "not_found"  # still audited


class TestFailLoud:
    def test_required_missing_aborts_with_nothing_injected(self):
        spec = [{"name": "GH_TOKEN"}]  # required, not found
        plan = _mat({}).materialize(variables_spec=spec, **_COMMON)
        assert not plan.ok
        assert plan.failure_reason == "missing_required_variables"
        assert plan.env == {} and plan.files == []
        # audit of the failed read is still produced.
        assert plan.audit_records[0].result == "not_found"

    def test_permission_denied_aborts(self):
        outcomes = {f"{_P}/GH_TOKEN": ReadOutcome(result="permission_denied")}
        plan = _mat(outcomes).materialize(variables_spec=[{"name": "GH_TOKEN"}], **_COMMON)
        assert not plan.ok
        assert plan.failure_reason == "permission_denied"
        assert plan.env == {}

    def test_unreachable_aborts(self):
        outcomes = {f"{_P}/GH_TOKEN": ReadOutcome(result="unreachable")}
        plan = _mat(outcomes).materialize(variables_spec=[{"name": "GH_TOKEN"}], **_COMMON)
        assert not plan.ok
        assert plan.failure_reason == "vault_unreachable"
        assert plan.env == {}
