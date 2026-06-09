"""Unit tests for gate execution functionality.

Tests the GateRunner class and gate execution pipeline including success,
failure, and error handling scenarios.
"""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import patch, Mock

from controller.gates import GateRunner, GateConfig
from controller.types import TaskInfo, GateResult, RepoInfo


@pytest.fixture
def sample_task():
    """Sample task for testing."""
    return TaskInfo(
        id="test-task-123",
        title="Test Gate Task",
        spec="description: Test task for gate execution",
        project_id="test-project",
        test_command={"command": "echo 'test passed'"},
        needs_review=False,
        assigned_to=None
    )


@pytest.fixture
def temp_workdir(tmp_path):
    """Temporary working directory for gate execution."""
    return tmp_path / "gate-workdir"


class TestGateConfig:
    """Test cases for GateConfig dataclass."""

    def test_gate_config_creation(self):
        """Test GateConfig creation with default and custom values."""
        # Default required=True
        gate1 = GateConfig(name="test-gate", command="echo test")
        assert gate1.name == "test-gate"
        assert gate1.command == "echo test"
        assert gate1.required is True

        # Custom required=False
        gate2 = GateConfig(name="optional-gate", command="echo optional", required=False)
        assert gate2.required is False


class TestGateRunner:
    """Test cases for GateRunner class."""

    def test_initialization(self):
        """Test GateRunner initialization."""
        gates = [
            GateConfig(name="gate1", command="echo test1"),
            GateConfig(name="gate2", command="echo test2")
        ]
        runner = GateRunner(gates)
        assert runner.gates == gates

    def test_empty_gates_initialization(self):
        """Test GateRunner with empty gates list."""
        runner = GateRunner([])
        assert runner.gates == []

    @pytest.mark.asyncio
    async def test_run_gates_no_gates(self, sample_task, temp_workdir):
        """Test run_gates with no configured gates."""
        runner = GateRunner([])
        results = await runner.run_gates(temp_workdir, sample_task)
        assert results == []

    @pytest.mark.asyncio
    async def test_run_single_successful_gate(self, sample_task, temp_workdir):
        """Test execution of a single successful gate."""
        gate = GateConfig(name="success-gate", command="echo 'success'")
        runner = GateRunner([gate])

        # Mock subprocess execution
        with patch('controller.gates.asyncio.create_subprocess_shell') as mock_subprocess:
            mock_process = Mock()
            mock_process.returncode = 0

            async def mock_communicate():
                return (b"success\n", None)

            mock_process.communicate = mock_communicate
            mock_subprocess.return_value = mock_process

            results = await runner.run_gates(temp_workdir, sample_task)

            assert len(results) == 1
            result = results[0]
            assert result.name == "success-gate"
            assert result.command == "echo 'success'"
            assert result.exit_code == 0
            assert result.stdout == "success\n"
            assert result.passed is True

    @pytest.mark.asyncio
    async def test_run_single_failing_gate(self, sample_task, temp_workdir):
        """Test execution of a single failing gate."""
        gate = GateConfig(name="fail-gate", command="exit 1")
        runner = GateRunner([gate])

        # Mock subprocess execution
        with patch('controller.gates.asyncio.create_subprocess_shell') as mock_subprocess:
            mock_process = Mock()
            mock_process.returncode = 1

            async def mock_communicate():
                return (b"command failed\n", None)

            mock_process.communicate = mock_communicate
            mock_subprocess.return_value = mock_process

            results = await runner.run_gates(temp_workdir, sample_task)

            assert len(results) == 1
            result = results[0]
            assert result.name == "fail-gate"
            assert result.command == "exit 1"
            assert result.exit_code == 1
            assert result.stdout == "command failed\n"
            assert result.passed is False

    @pytest.mark.asyncio
    async def test_run_multiple_gates_mixed_results(self, sample_task, temp_workdir):
        """Test execution of multiple gates with mixed success/failure."""
        gates = [
            GateConfig(name="pass-gate", command="echo 'pass'"),
            GateConfig(name="fail-gate", command="exit 1"),
            GateConfig(name="pass2-gate", command="echo 'pass2'")
        ]
        runner = GateRunner(gates)

        # Mock subprocess execution with different return codes
        with patch('controller.gates.asyncio.create_subprocess_shell') as mock_subprocess:
            mock_processes = []

            # First gate: success
            mock_proc1 = Mock()
            mock_proc1.returncode = 0
            async def mock_comm1(): return (b"pass\n", None)
            mock_proc1.communicate = mock_comm1

            # Second gate: failure
            mock_proc2 = Mock()
            mock_proc2.returncode = 1
            async def mock_comm2(): return (b"fail\n", None)
            mock_proc2.communicate = mock_comm2

            # Third gate: success
            mock_proc3 = Mock()
            mock_proc3.returncode = 0
            async def mock_comm3(): return (b"pass2\n", None)
            mock_proc3.communicate = mock_comm3

            mock_subprocess.side_effect = [mock_proc1, mock_proc2, mock_proc3]

            results = await runner.run_gates(temp_workdir, sample_task)

            assert len(results) == 3
            assert results[0].passed is True
            assert results[1].passed is False
            assert results[2].passed is True

    @pytest.mark.asyncio
    async def test_gate_execution_exception_handling(self, sample_task, temp_workdir):
        """Test handling of gate execution exceptions."""
        gate = GateConfig(name="error-gate", command="echo test")
        runner = GateRunner([gate])

        # Mock subprocess to raise exception
        with patch('controller.gates.asyncio.create_subprocess_shell') as mock_subprocess:
            mock_subprocess.side_effect = OSError("Command not found")

            results = await runner.run_gates(temp_workdir, sample_task)

            assert len(results) == 1
            result = results[0]
            assert result.name == "error-gate"
            assert result.command == "echo test"
            assert result.exit_code == -1
            assert "execution error" in result.stdout.lower()
            assert result.passed is False

    @pytest.mark.asyncio
    async def test_subprocess_call_parameters(self, sample_task, temp_workdir):
        """Test that subprocess is called with correct parameters."""
        temp_workdir.mkdir(parents=True)
        gate = GateConfig(name="test-gate", command="echo 'test command'")
        runner = GateRunner([gate])

        with patch('controller.gates.asyncio.create_subprocess_shell') as mock_subprocess:
            mock_process = Mock()
            mock_process.returncode = 0
            async def mock_communicate(): return (b"test output", None)
            mock_process.communicate = mock_communicate
            mock_subprocess.return_value = mock_process

            await runner.run_gates(temp_workdir, sample_task)

            # Verify subprocess call parameters
            mock_subprocess.assert_called_once_with(
                "echo 'test command'",
                cwd=str(temp_workdir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )

    @pytest.mark.asyncio
    async def test_bytes_decoding(self, sample_task, temp_workdir):
        """Test that bytes output is properly decoded."""
        gate = GateConfig(name="decode-gate", command="echo test")
        runner = GateRunner([gate])

        with patch('controller.gates.asyncio.create_subprocess_shell') as mock_subprocess:
            mock_process = Mock()
            mock_process.returncode = 0

            # Return bytes with unicode content
            async def mock_communicate():
                return ("test unicode: ñáéíóú".encode('utf-8'), None)

            mock_process.communicate = mock_communicate
            mock_subprocess.return_value = mock_process

            results = await runner.run_gates(temp_workdir, sample_task)

            assert len(results) == 1
            assert results[0].stdout == "test unicode: ñáéíóú"

    @pytest.mark.asyncio
    async def test_empty_stdout_handling(self, sample_task, temp_workdir):
        """Test handling of empty stdout."""
        gate = GateConfig(name="empty-gate", command="true")
        runner = GateRunner([gate])

        with patch('controller.gates.asyncio.create_subprocess_shell') as mock_subprocess:
            mock_process = Mock()
            mock_process.returncode = 0

            async def mock_communicate():
                return (None, None)  # No output

            mock_process.communicate = mock_communicate
            mock_subprocess.return_value = mock_process

            results = await runner.run_gates(temp_workdir, sample_task)

            assert len(results) == 1
            assert results[0].stdout == ""


class TestFromTaskCommand:
    """Test cases for creating GateRunner from task test_command."""

    def test_from_task_command_with_command(self):
        """Test creating GateRunner from task with test_command."""
        test_command = {"command": "pytest tests/test_example.py"}
        runner = GateRunner.from_task_command(test_command)

        assert len(runner.gates) == 1
        gate = runner.gates[0]
        assert gate.name == "task-test"
        assert gate.command == "pytest tests/test_example.py"
        assert gate.required is True

    def test_from_task_command_empty(self):
        """Test creating GateRunner from empty test_command."""
        runner = GateRunner.from_task_command({})
        assert len(runner.gates) == 0

    def test_from_task_command_none(self):
        """Test creating GateRunner from None test_command."""
        runner = GateRunner.from_task_command(None)
        assert len(runner.gates) == 0

    def test_from_task_command_missing_command_field(self):
        """Test creating GateRunner from test_command without command field."""
        test_command = {"other_field": "value", "timeout": 30}
        runner = GateRunner.from_task_command(test_command)
        assert len(runner.gates) == 0

    def test_from_task_command_complex_command(self):
        """Test creating GateRunner from complex test command."""
        test_command = {
            "command": "cd /home/user/project && source .venv/bin/activate && pytest tests/ -v",
            "timeout": 300
        }
        runner = GateRunner.from_task_command(test_command)

        assert len(runner.gates) == 1
        gate = runner.gates[0]
        assert gate.command == "cd /home/user/project && source .venv/bin/activate && pytest tests/ -v"

class TestFromTask:
    """F7/F10: GateRunner.from_task always synthesizes a required delivery
    gate (against origin) so a no-test_command task is no longer a vacuous
    pass and an ephemeral-workdir-only commit no longer satisfies success.
    See docs/f7-f10-delivery-gate-DESIGN.md.
    """

    repo = RepoInfo(url="https://github.com/o/r.git", branch="main")

    def _task(self, test_command):
        return TaskInfo(
            id="abc123",
            title="t",
            spec="s",
            project_id="p",
            test_command=test_command,
            needs_review=False,
            assigned_to=None,
        )

    def test_delivery_gate_present_when_no_test_command(self):
        runner = GateRunner.from_task(self._task(None), self.repo)
        assert len(runner.gates) == 1
        g = runner.gates[0]
        assert g.name == "deliverable-pushed"
        assert g.required is True
        # references the deterministic deliverable branch + base branch
        assert "vafi/task-abc123" in g.command
        assert "ls-remote" in g.command
        assert "main" in g.command

    def test_delivery_gate_present_when_empty_test_command(self):
        runner = GateRunner.from_task(self._task({}), self.repo)
        assert [g.name for g in runner.gates] == ["deliverable-pushed"]

    def test_test_command_gate_appended_after_delivery(self):
        runner = GateRunner.from_task(self._task({"command": "pytest -q"}), self.repo)
        # delivery (floor) -> test_command -> tests-were-red efficacy (#32).
        assert [g.name for g in runner.gates] == [
            "deliverable-pushed", "task-test", "tests-were-red"]
        assert runner.gates[1].command == "pytest -q"
        assert runner.gates[1].required is True

    def test_efficacy_gate_appended_when_test_command_present(self):
        """ViloForge/vafi#32: the tests-were-red efficacy gate is synthesized
        (required) whenever there is a test_command to witness, and embeds the
        deliverable branch, base branch, and the command it runs without impl."""
        runner = GateRunner.from_task(self._task({"command": "pytest -q"}), self.repo)
        eff = runner.gates[-1]
        assert eff.name == "tests-were-red"
        assert eff.required is True
        assert "vafi/task-abc123" in eff.command   # deliverable ref
        assert "refs/efficacy/base" in eff.command  # base reconstruction
        assert "pytest -q" in eff.command           # the witnessed command

    def test_no_efficacy_gate_without_test_command(self):
        """No test_command ⇒ nothing to witness ⇒ no efficacy gate (delivery
        gate still backstops; bare/no-AC tasks unaffected)."""
        names = [g.name for g in GateRunner.from_task(self._task(None), self.repo).gates]
        assert "tests-were-red" not in names

    def test_from_task_command_shim_unchanged_v16(self):
        # V16: the old classmethod keeps its old (no-delivery) semantics so
        # existing direct callers/tests do not regress.
        assert len(GateRunner.from_task_command({}).gates) == 0
        assert len(GateRunner.from_task_command(None).gates) == 0
