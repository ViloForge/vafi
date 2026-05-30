"""L1 tests for VariablesStage — controller-side variables glue (C.3 Slice 5).

Real VariableMaterializer over a fake VaultReader; fake worksource + recording
emitter. Asserts the no-op (no fetch), the success plan + audit emission with the
task FK filled, the fail-loud plan, and best-effort emit (never raises).
"""
import pytest

from controller.types import TaskInfo
from controller.variables_stage import VariablesStage
from variables.literal import LiteralBackend
from variables.materializer import VariableMaterializer
from variables.registry import BackendRegistry
from variables.vault import ReadOutcome, VaultBackend


class _FakeReader:
    def __init__(self, outcomes):
        self.outcomes = outcomes

    def read(self, role, path):
        return self.outcomes.get(path, ReadOutcome(result="not_found"))


class _FakeWorkSource:
    def __init__(self, slug="abad"):
        self._slug = slug
        self.slug_calls = []

    async def get_project_slug(self, project_id):
        self.slug_calls.append(project_id)
        return self._slug


class _RecordingEmitter:
    def __init__(self, fail=False):
        self.fail = fail
        self.emitted = []

    async def emit(self, record):
        if self.fail:
            raise RuntimeError("boom")
        self.emitted.append(record)


def _stage(outcomes, work_source=None, emitter=None):
    reg = BackendRegistry()
    reg.register("vault", VaultBackend(_FakeReader(outcomes)))
    reg.register("literal", LiteralBackend())
    return VariablesStage(
        VariableMaterializer(reg),
        emitter or _RecordingEmitter(),
        work_source or _FakeWorkSource(),
        controller_env="dev", role="executor", controller_id="ctl-1",
        now=lambda: "2026-05-30T12:00:00Z",
    )


def _task(variables, project_id="proj_pk_1", task_id="tk1"):
    return TaskInfo(
        id=task_id, title="t", spec="", project_id=project_id,
        test_command={}, needs_review=False, assigned_to=None, variables=variables,
    )


_P = "secret/apps/vtaskforge/dev/projects/abad/executor"


class TestPrepare:
    async def test_no_variables_is_noop_without_slug_fetch(self):
        ws = _FakeWorkSource()
        plan = await _stage({}, work_source=ws).prepare(_task([]))
        assert plan.ok and not plan.env
        assert ws.slug_calls == []  # slug NOT fetched off the hot path

    async def test_success_resolves_slug_and_builds_env(self):
        ws = _FakeWorkSource(slug="abad")
        outcomes = {f"{_P}/GH_TOKEN": ReadOutcome(result="success", value=b"ghp_longsecret", version=4)}
        plan = await _stage(outcomes, work_source=ws).prepare(_task([{"name": "GH_TOKEN"}]))
        assert plan.ok
        assert plan.env == {"GH_TOKEN": "ghp_longsecret"}
        assert ws.slug_calls == ["proj_pk_1"]  # slug fetched by PK

    async def test_required_missing_is_fail_loud(self):
        plan = await _stage({}).prepare(_task([{"name": "GH_TOKEN"}]))
        assert not plan.ok
        assert plan.failure_reason == "missing_required_variables"
        assert plan.env == {}


class TestRecord:
    async def test_emits_audit_per_read_with_task_fk(self):
        emitter = _RecordingEmitter()
        outcomes = {f"{_P}/GH_TOKEN": ReadOutcome(result="success", value=b"ghp_longsecret", version=4)}
        stage = _stage(outcomes, emitter=emitter)
        task = _task([{"name": "GH_TOKEN"}], task_id="tk-42")
        plan = await stage.prepare(task)
        await stage.record(task, plan)
        assert len(emitter.emitted) == 1
        assert emitter.emitted[0].task == "tk-42"  # FK filled at emit time
        assert emitter.emitted[0].variable_name == "GH_TOKEN"

    async def test_record_is_best_effort_never_raises(self):
        emitter = _RecordingEmitter(fail=True)
        outcomes = {f"{_P}/GH_TOKEN": ReadOutcome(result="success", value=b"ghp_longsecret", version=4)}
        stage = _stage(outcomes, emitter=emitter)
        task = _task([{"name": "GH_TOKEN"}])
        plan = await stage.prepare(task)
        await stage.record(task, plan)  # must not raise despite emitter failure
