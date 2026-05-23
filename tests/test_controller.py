"""Unit tests for the vafi controller.

Tests the controller poll-claim-log cycle with a mocked WorkSource.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, Mock

from controller.config import AgentConfig
from controller.controller import Controller
from controller.types import AgentInfo, TaskInfo


class MockWorkSource:
    """Mock WorkSource for testing controller logic."""

    def __init__(self):
        self.register = AsyncMock()
        self.poll = AsyncMock()
        self.claim = AsyncMock()
        self.fail = AsyncMock()
        self.heartbeat = AsyncMock()
        self.agent_heartbeat = AsyncMock()
        self.set_agent_offline = AsyncMock()
        self.complete = AsyncMock()
        self.get_repo_info = AsyncMock()
        self.get_task_repo_info = AsyncMock()
        self.list_integrations = AsyncMock(return_value=[])
        self.report_integration_result = AsyncMock()
        self.get_rework_context = AsyncMock()
        self.count_rework_attempts = AsyncMock(return_value=0)
        self.submit = AsyncMock()
        self.list_submittable = AsyncMock()
        self.submit_review = AsyncMock()
        self.get_task_context = AsyncMock()
        self.add_note = AsyncMock()


@pytest.fixture
def mock_work_source():
    """Provide a mock work source for testing."""
    return MockWorkSource()


@pytest.fixture
def test_config():
    """Provide test configuration."""
    return AgentConfig(
        agent_id="test-agent",
        agent_role="executor",
        agent_tags=["executor", "test"],
        vtf_api_url="http://test-vtf:8000",
        poll_interval=1,  # Short interval for testing
    )


@pytest.fixture
def sample_agent():
    """Sample agent info for testing."""
    return AgentInfo(id="agent-123", token="test-token")


@pytest.fixture
def sample_task():
    """Sample task info for testing."""
    return TaskInfo(
        id="task-456",
        title="Test Task",
        spec="test: true\ndescription: A test task",
        project_id="project-789",
        test_command={"command": "echo test"},
        needs_review=False,
        assigned_to=None
    )


class TestController:
    """Test cases for the Controller class."""

    @pytest.mark.asyncio
    async def test_initialization(self, mock_work_source, test_config):
        """Test controller initialization."""
        controller = Controller(mock_work_source, test_config)
        assert controller.work_source is mock_work_source
        assert controller.config is test_config
        assert controller._agent_info is None

    @pytest.mark.asyncio
    async def test_successful_registration(self, mock_work_source, test_config, sample_agent):
        """Test successful agent registration."""
        # Setup mock
        mock_work_source.register.return_value = sample_agent
        mock_work_source.poll.return_value = None  # No work available

        controller = Controller(mock_work_source, test_config)

        # Use timeout to prevent infinite loop
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(controller.run(), timeout=0.1)

        # Verify registration was called
        mock_work_source.register.assert_called_once_with(
            name="test-agent",
            tags=["executor", "test"]
        )

    @pytest.mark.asyncio
    async def test_registration_default_name_disambiguates_by_tags(self, mock_work_source, sample_agent):
        """Two deployments sharing a role but with different tag sets must
        register with distinct names. Otherwise vtf upserts them as a single
        agent and the executor / executor-pi separation collapses.
        """
        mock_work_source.register.return_value = sample_agent
        mock_work_source.poll.return_value = None

        # claude executor: role=executor, tags=[executor]
        cfg_claude = AgentConfig(
            agent_id="",  # no override -> default
            agent_role="executor",
            agent_tags=["executor"],
            vtf_api_url="http://test-vtf:8000",
            poll_interval=1,
        )
        controller_claude = Controller(mock_work_source, cfg_claude)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(controller_claude.run(), timeout=0.1)
        claude_call = mock_work_source.register.call_args
        mock_work_source.register.reset_mock()

        # pi executor: same role, different tags
        cfg_pi = AgentConfig(
            agent_id="",
            agent_role="executor",
            agent_tags=["executor", "pi"],
            vtf_api_url="http://test-vtf:8000",
            poll_interval=1,
        )
        controller_pi = Controller(mock_work_source, cfg_pi)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(controller_pi.run(), timeout=0.1)
        pi_call = mock_work_source.register.call_args

        # Names must differ even though role is the same
        assert claude_call.kwargs["name"] != pi_call.kwargs["name"]
        assert claude_call.kwargs["name"] == "executor"
        assert pi_call.kwargs["name"] == "executor-pi"

    @pytest.mark.asyncio
    async def test_registration_falls_back_to_role_when_tags_empty(self, mock_work_source, sample_agent):
        """If tags are empty for some reason, fall back to role rather than
        producing an empty name.
        """
        mock_work_source.register.return_value = sample_agent
        mock_work_source.poll.return_value = None

        cfg = AgentConfig(
            agent_id="",
            agent_role="judge",
            agent_tags=[],
            vtf_api_url="http://test-vtf:8000",
            poll_interval=1,
        )
        controller = Controller(mock_work_source, cfg)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(controller.run(), timeout=0.1)
        assert mock_work_source.register.call_args.kwargs["name"] == "judge"

    @pytest.mark.asyncio
    async def test_registration_uses_explicit_agent_id_when_set(self, mock_work_source, sample_agent):
        """VF_AGENT_ID override always wins over the tag/role default."""
        mock_work_source.register.return_value = sample_agent
        mock_work_source.poll.return_value = None

        cfg = AgentConfig(
            agent_id="vafi-executor-prod-1",
            agent_role="executor",
            agent_tags=["executor", "pi"],  # would be "executor-pi" by default
            vtf_api_url="http://test-vtf:8000",
            poll_interval=1,
        )
        controller = Controller(mock_work_source, cfg)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(controller.run(), timeout=0.1)
        assert mock_work_source.register.call_args.kwargs["name"] == "vafi-executor-prod-1"

    @pytest.mark.asyncio
    async def test_poll_no_work_available(self, mock_work_source, test_config, sample_agent):
        """Test polling when no work is available."""
        # Setup mocks
        mock_work_source.register.return_value = sample_agent
        mock_work_source.poll.return_value = None

        controller = Controller(mock_work_source, test_config)

        # Use timeout to prevent infinite loop
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(controller.run(), timeout=0.1)

        # Verify polling was attempted
        mock_work_source.poll.assert_called_with("agent-123", ["executor", "test"])

    @pytest.mark.asyncio
    async def test_successful_task_claim_and_execution_failure(self, mock_work_source, test_config, sample_agent, sample_task):
        """Test successful task claim but execution failure due to environment."""
        # Setup mocks
        mock_work_source.register.return_value = sample_agent
        mock_work_source.poll.side_effect = [sample_task, None]  # Return task once, then no work
        mock_work_source.claim.return_value = sample_task

        # Mock repo info
        from controller.types import RepoInfo
        mock_repo = RepoInfo(url="https://github.com/test/repo.git", branch="main")
        mock_work_source.get_repo_info.return_value = mock_repo

        controller = Controller(mock_work_source, test_config)

        # Use timeout to prevent infinite loop
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(controller.run(), timeout=0.1)

        # Verify claim and fail were called (execution will fail due to permissions)
        mock_work_source.claim.assert_called_once_with("task-456", "agent-123")
        # Should fail due to permission error on /sessions directory
        mock_work_source.fail.assert_called_once()
        call_args = mock_work_source.fail.call_args[0]
        assert call_args[0] == "task-456"
        assert "Permission denied" in call_args[1] or "Execution failed" in call_args[1]

    @pytest.mark.asyncio
    async def test_claim_failure_handling(self, mock_work_source, test_config, sample_agent, sample_task):
        """Test handling of claim failures."""
        # Setup mocks
        mock_work_source.register.return_value = sample_agent
        mock_work_source.poll.side_effect = [sample_task, None]
        mock_work_source.claim.side_effect = Exception("Claim failed")

        controller = Controller(mock_work_source, test_config)

        # Use timeout to prevent infinite loop
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(controller.run(), timeout=0.1)

        # Verify error handling - should try to fail the task
        mock_work_source.fail.assert_called_once_with("task-456", "error during processing: Claim failed")

    @pytest.mark.asyncio
    async def test_poll_single_iteration(self, mock_work_source, test_config, sample_agent):
        """Test a single poll iteration without running the full loop."""
        mock_work_source.poll.return_value = None

        controller = Controller(mock_work_source, test_config)
        controller._agent_info = sample_agent

        # Test single poll iteration
        await controller._poll_and_process()

        # Verify poll was called correctly
        mock_work_source.poll.assert_called_once_with("agent-123", ["executor", "test"])

    @pytest.mark.asyncio
    async def test_shutdown_signal_handling(self, mock_work_source, test_config, sample_agent):
        """Test that controller handles shutdown signals properly."""
        mock_work_source.register.return_value = sample_agent
        mock_work_source.poll.return_value = None

        controller = Controller(mock_work_source, test_config)

        # Simulate shutdown signal
        async def trigger_shutdown():
            await asyncio.sleep(0.05)  # Small delay
            controller._shutdown.set()

        # Run controller with shutdown trigger
        await asyncio.gather(
            controller.run(),
            trigger_shutdown()
        )

        # Should exit cleanly
        assert controller._shutdown.is_set()

    @pytest.mark.asyncio
    async def test_marks_agent_offline_on_shutdown(self, mock_work_source, test_config, sample_agent):
        """Test that controller marks agent offline on graceful shutdown."""
        mock_work_source.register.return_value = sample_agent
        mock_work_source.poll.return_value = None

        controller = Controller(mock_work_source, test_config)

        async def trigger_shutdown():
            await asyncio.sleep(0.05)
            controller._shutdown.set()

        await asyncio.gather(
            controller.run(),
            trigger_shutdown()
        )

        mock_work_source.set_agent_offline.assert_called_once_with("agent-123")

    @pytest.mark.asyncio
    async def test_agent_heartbeat_runs_during_idle(self, mock_work_source, test_config, sample_agent):
        """Test that agent heartbeat fires while polling idle."""
        mock_work_source.register.return_value = sample_agent
        mock_work_source.poll.return_value = None

        # Use short poll interval so heartbeat fires
        test_config.poll_interval = 0.05

        controller = Controller(mock_work_source, test_config)

        async def trigger_shutdown():
            await asyncio.sleep(0.15)
            controller._shutdown.set()

        await asyncio.gather(
            controller.run(),
            trigger_shutdown()
        )

        # Agent heartbeat should have fired at least once
        assert mock_work_source.agent_heartbeat.call_count >= 1


class TestReworkCap:
    """VF_MAX_REWORK enforcement: fail tasks that exceed the cap before harness invocation."""

    @pytest.mark.asyncio
    async def test_fails_task_when_rework_cap_exceeded(
        self, mock_work_source, test_config, sample_agent, sample_task
    ):
        """When count_rework_attempts >= max_rework, fail the task and skip execute."""
        test_config.max_rework = 3
        mock_work_source.poll.return_value = sample_task
        mock_work_source.claim.return_value = sample_task
        mock_work_source.count_rework_attempts.return_value = 3

        controller = Controller(mock_work_source, test_config)
        controller._agent_info = sample_agent
        controller.execute = AsyncMock()

        await controller._poll_and_execute()

        controller.execute.assert_not_called()
        mock_work_source.fail.assert_called_once()
        call_args = mock_work_source.fail.call_args[0]
        assert call_args[0] == sample_task.id
        assert "Rework limit exceeded" in call_args[1]
        assert "3 prior rejections" in call_args[1]
        assert "max 3" in call_args[1]

    @pytest.mark.asyncio
    async def test_fails_task_when_rework_count_above_cap(
        self, mock_work_source, test_config, sample_agent, sample_task
    ):
        """Boundary: a count *above* the cap (e.g. 4 with max=3) also fails."""
        test_config.max_rework = 3
        mock_work_source.poll.return_value = sample_task
        mock_work_source.claim.return_value = sample_task
        mock_work_source.count_rework_attempts.return_value = 4

        controller = Controller(mock_work_source, test_config)
        controller._agent_info = sample_agent
        controller.execute = AsyncMock()

        await controller._poll_and_execute()

        controller.execute.assert_not_called()
        mock_work_source.fail.assert_called_once()

    @pytest.mark.asyncio
    async def test_proceeds_when_rework_count_below_cap(
        self, mock_work_source, test_config, sample_agent, sample_task
    ):
        """Boundary: count == max - 1 still invokes the harness."""
        from controller.types import ExecutionResult

        test_config.max_rework = 3
        mock_work_source.poll.return_value = sample_task
        mock_work_source.claim.return_value = sample_task
        mock_work_source.count_rework_attempts.return_value = 2

        controller = Controller(mock_work_source, test_config)
        controller._agent_info = sample_agent
        controller.execute = AsyncMock(return_value=ExecutionResult(
            success=True, session_id=None, completion_report="", cost_usd=0.0,
            num_turns=0, gate_results=[],
        ))

        await controller._poll_and_execute()

        controller.execute.assert_called_once()
        mock_work_source.fail.assert_not_called()
        mock_work_source.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_proceeds_when_count_rework_attempts_raises(
        self, mock_work_source, test_config, sample_agent, sample_task
    ):
        """If count_rework_attempts errors, proceed with harness (don't stall the task)."""
        from controller.types import ExecutionResult

        test_config.max_rework = 3
        mock_work_source.poll.return_value = sample_task
        mock_work_source.claim.return_value = sample_task
        mock_work_source.count_rework_attempts.side_effect = RuntimeError("vtf down")

        controller = Controller(mock_work_source, test_config)
        controller._agent_info = sample_agent
        controller.execute = AsyncMock(return_value=ExecutionResult(
            success=True, session_id=None, completion_report="", cost_usd=0.0,
            num_turns=0, gate_results=[],
        ))

        await controller._poll_and_execute()

        controller.execute.assert_called_once()
        mock_work_source.fail.assert_not_called()


class TestHarnessFailureWithGatesPass:
    """vafi#39: harness exit_code != 0 with passing gates must accept the
    delivery.

    The harness exit code is a *hint*; the gate is the load-bearing
    acceptance signal. If the F7/F10 delivery gate (and the optional
    test_command gate) verify the branch on origin, the work is real
    regardless of how the harness exited. Same hardening direction as
    vafi#36 (D1+D2): the structured signal overrides the raw signal.
    """

    @pytest.mark.asyncio
    async def test_execute_harness_failure_with_passing_gates_succeeds(
        self, mock_work_source, test_config, sample_agent, sample_task,
        tmp_path, monkeypatch,
    ):
        from controller.types import ExecutionResult, GateResult, RepoInfo
        from controller import gates as gates_mod

        test_config.sessions_dir = str(tmp_path)
        mock_work_source.get_task_repo_info.return_value = RepoInfo(
            url="x", branch="main"
        )

        controller = Controller(mock_work_source, test_config)
        controller._agent_info = sample_agent
        controller._write_task_context = AsyncMock()

        async def _noop_clone(*a, **kw):
            return None

        async def _failing_invoke(*a, **kw):
            return ExecutionResult(
                success=False, session_id="sess-1",
                completion_report="harness boom: exit_code=1",
                cost_usd=0.0, num_turns=3, gate_results=[],
            )

        monkeypatch.setattr(
            controller._invoker, "_ensure_repo_cloned", _noop_clone
        )
        monkeypatch.setattr(controller._invoker, "invoke", _failing_invoke)

        passing = [GateResult(
            name="delivery", command="...", exit_code=0,
            stdout="deliverable verified on origin", passed=True,
        )]

        class _FakeRunner:
            async def run_gates(self, workdir, task):
                return passing

        monkeypatch.setattr(
            gates_mod.GateRunner, "from_task",
            classmethod(lambda cls, t, r: _FakeRunner()),
        )

        result = await controller.execute(sample_task)

        assert result.success is True
        assert result.harness_anomaly is not None
        assert "harness" in result.harness_anomaly.lower()
        # The harness completion_report is preserved so adjudicators can
        # still see what the harness said before the gates overrode it.
        assert "exit_code=1" in result.harness_anomaly

    @pytest.mark.asyncio
    async def test_execute_harness_failure_with_failing_gates_fails(
        self, mock_work_source, test_config, sample_agent, sample_task,
        tmp_path, monkeypatch,
    ):
        """Honest-failure regression: harness reports failure AND gates fail
        → success=False, no anomaly (the harness's verdict was correct)."""
        from controller.types import ExecutionResult, GateResult, RepoInfo
        from controller import gates as gates_mod

        test_config.sessions_dir = str(tmp_path)
        mock_work_source.get_task_repo_info.return_value = RepoInfo(
            url="x", branch="main"
        )

        controller = Controller(mock_work_source, test_config)
        controller._agent_info = sample_agent
        controller._write_task_context = AsyncMock()

        async def _noop_clone(*a, **kw):
            return None

        async def _failing_invoke(*a, **kw):
            return ExecutionResult(
                success=False, session_id="sess-2",
                completion_report="harness died before push",
                cost_usd=0.0, num_turns=1, gate_results=[],
            )

        monkeypatch.setattr(
            controller._invoker, "_ensure_repo_cloned", _noop_clone
        )
        monkeypatch.setattr(controller._invoker, "invoke", _failing_invoke)

        failing = [GateResult(
            name="delivery", command="...", exit_code=1,
            stdout="branch not found on origin", passed=False,
        )]

        class _FakeRunner:
            async def run_gates(self, workdir, task):
                return failing

        monkeypatch.setattr(
            gates_mod.GateRunner, "from_task",
            classmethod(lambda cls, t, r: _FakeRunner()),
        )

        result = await controller.execute(sample_task)

        assert result.success is False
        assert result.harness_anomaly is None

    @pytest.mark.asyncio
    async def test_execute_harness_success_with_failing_gates_fails(
        self, mock_work_source, test_config, sample_agent, sample_task,
        tmp_path, monkeypatch,
    ):
        """Existing behavior preserved: harness OK, gates fail → success=False,
        no anomaly (gates are the verdict)."""
        from controller.types import ExecutionResult, GateResult, RepoInfo
        from controller import gates as gates_mod

        test_config.sessions_dir = str(tmp_path)
        mock_work_source.get_task_repo_info.return_value = RepoInfo(
            url="x", branch="main"
        )

        controller = Controller(mock_work_source, test_config)
        controller._agent_info = sample_agent
        controller._write_task_context = AsyncMock()

        async def _noop_clone(*a, **kw):
            return None

        async def _ok_invoke(*a, **kw):
            return ExecutionResult(
                success=True, session_id="sess-3",
                completion_report="all done",
                cost_usd=0.0, num_turns=2, gate_results=[],
            )

        monkeypatch.setattr(
            controller._invoker, "_ensure_repo_cloned", _noop_clone
        )
        monkeypatch.setattr(controller._invoker, "invoke", _ok_invoke)

        failing = [GateResult(
            name="test_command", command="pytest", exit_code=1,
            stdout="2 failed", passed=False,
        )]

        class _FakeRunner:
            async def run_gates(self, workdir, task):
                return failing

        monkeypatch.setattr(
            gates_mod.GateRunner, "from_task",
            classmethod(lambda cls, t, r: _FakeRunner()),
        )

        result = await controller.execute(sample_task)

        assert result.success is False
        assert result.harness_anomaly is None

    @pytest.mark.asyncio
    async def test_poll_posts_anomaly_note_then_completes(
        self, mock_work_source, test_config, sample_agent, sample_task,
    ):
        """At the poll layer: when execute() returns success=True with a
        harness_anomaly string, the controller posts a vafi:harness_exit_anomaly
        note via add_note BEFORE calling complete(), so the anomaly is
        permanently surfaced on the task even though the delivery is accepted.
        """
        from controller.types import ExecutionResult

        mock_work_source.poll.return_value = sample_task
        mock_work_source.claim.return_value = sample_task

        controller = Controller(mock_work_source, test_config)
        controller._agent_info = sample_agent
        controller.execute = AsyncMock(return_value=ExecutionResult(
            success=True, session_id="s", completion_report="ok",
            cost_usd=0.0, num_turns=0, gate_results=[],
            harness_anomaly="harness reported success=False but gates passed; "
                            "completion_report='harness boom: exit_code=1'",
        ))

        await controller._poll_and_execute()

        # Note posted with the anomaly tag + the agent as actor.
        mock_work_source.add_note.assert_called_once()
        note_args = mock_work_source.add_note.call_args
        assert note_args[0][0] == sample_task.id
        assert "vafi:harness_exit_anomaly" in note_args[0][1]
        # Delivery accepted: complete (not fail).
        mock_work_source.complete.assert_called_once()
        mock_work_source.fail.assert_not_called()