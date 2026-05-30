"""Integration test for the C.3 variables path in controller.execute (Slice 5).

The security-critical behaviour: a task whose required secret cannot be satisfied
**fails loud before the harness is ever invoked**. Tasks without variables, or
with no stage configured, are byte-identical to today (V16) — injection=None.
"""
from unittest.mock import AsyncMock, Mock

import pytest

from controller.config import AgentConfig
from controller.controller import Controller
from controller.types import TaskInfo
from controller.variables_stage import VariablesStage
from variables.literal import LiteralBackend
from variables.materializer import VariableMaterializer
from variables.registry import BackendRegistry
from variables.vault import ReadOutcome, VaultBackend


class _NoSecretsReader:
    def read(self, role, path):
        return ReadOutcome(result="not_found")  # nothing seeded → required missing


class _SlugWS:
    async def get_project_slug(self, project_id):
        return "abad"


def _stage_with_no_secrets():
    reg = BackendRegistry()
    reg.register("vault", VaultBackend(_NoSecretsReader()))
    reg.register("literal", LiteralBackend())
    return VariablesStage(
        VariableMaterializer(reg), AsyncMock(), _SlugWS(),
        controller_env="dev", role="executor", controller_id="ctl",
        now=lambda: "2026-05-30T00:00:00Z",
    )


def _task(variables):
    return TaskInfo(
        id="tk-failloud", title="t", spec="", project_id="proj_pk",
        test_command={}, needs_review=False, assigned_to=None, variables=variables,
    )


@pytest.fixture
def controller():
    ws = Mock()
    ws.register = AsyncMock()
    ws.heartbeat = AsyncMock()
    ws.agent_heartbeat = AsyncMock()
    c = Controller(ws, AgentConfig(agent_id="t", agent_role="executor", sessions_dir="/tmp/s"))
    c._agent_info = Mock(id="agent-1")
    c._invoker = Mock()
    c._invoker.invoke = AsyncMock()
    c._invoker._ensure_repo_cloned = AsyncMock()
    return c


class TestFailLoud:
    async def test_required_missing_secret_aborts_before_harness(self, controller):
        controller._variables_stage = _stage_with_no_secrets()
        result = await controller.execute(_task([{"name": "GH_TOKEN"}]))
        assert result.success is False
        assert "fail-loud" in result.completion_report
        assert "missing_required_variables" in result.completion_report
        # The harness was NEVER invoked — no agent process spawned.
        controller._invoker.invoke.assert_not_called()
        controller._invoker._ensure_repo_cloned.assert_not_called()

    async def test_no_variables_does_not_engage_the_stage(self, controller):
        # A task with no variables must not touch the stage at all (V16 no-op).
        controller._variables_stage = Mock()
        controller._variables_stage.prepare = AsyncMock()
        # Make the rest of execute() bail quickly after the (skipped) variables block.
        controller.work_source.get_task_repo_info = AsyncMock(side_effect=RuntimeError("stop"))
        try:
            await controller.execute(_task([]))
        except RuntimeError:
            pass
        controller._variables_stage.prepare.assert_not_called()
