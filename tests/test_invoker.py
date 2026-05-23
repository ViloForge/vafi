"""Unit tests for the HarnessInvoker class.

Tests the complete harness invocation pipeline including repo cloning,
subprocess execution, and output parsing.
"""

import asyncio
import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch, MagicMock
import pytest

from controller.config import AgentConfig
from controller.invoker import HarnessInvoker
from controller.types import TaskInfo, RepoInfo, ExecutionResult


@pytest.fixture
def test_config():
    """Test configuration for invoker."""
    return AgentConfig(
        agent_id="test-invoker",
        task_timeout=30,
        max_turns=10,
        sessions_dir="/tmp/test-sessions",
    )


@pytest.fixture
def sample_task():
    """Sample task for testing."""
    return TaskInfo(
        id="test-task-123",
        title="Test Task",
        spec="description: Test task for invoker\nsteps:\n  - implement\n  - test",
        project_id="test-project",
        test_command={"command": "pytest tests/"},
        needs_review=False,
        assigned_to=None
    )


@pytest.fixture
def sample_repo():
    """Sample repository info."""
    return RepoInfo(
        url="https://github.com/test/repo.git",
        branch="main"
    )


@pytest.fixture
def temp_workdir(tmp_path):
    """Temporary workdir for testing."""
    return tmp_path / "test-workdir"


class TestHarnessInvoker:
    """Test cases for HarnessInvoker."""

    def test_initialization(self, test_config):
        """Test invoker initialization."""
        invoker = HarnessInvoker(test_config)
        assert invoker.config is test_config

    @pytest.mark.asyncio
    async def test_invoke_complete_pipeline(self, test_config, sample_task, sample_repo, temp_workdir):
        """Test complete invoke pipeline with mocked subprocess."""
        invoker = HarnessInvoker(test_config)

        # Mock successful harness output
        mock_output = {
            "result": "Task completed successfully",
            "is_error": False,
            "session_id": "session-abc123",
            "total_cost_usd": 0.05,
            "num_turns": 3,
            "stop_reason": "end_turn"
        }

        with patch('controller.invoker.subprocess.run') as mock_git, \
             patch('controller.invoker.asyncio.create_subprocess_exec') as mock_subprocess:

            # Mock git clone
            mock_git.return_value = Mock(returncode=0, stderr="", stdout="")

            # Mock harness subprocess
            mock_process = Mock()
            mock_process.returncode = 0

            async def mock_communicate():
                return (json.dumps(mock_output), "")

            mock_process.communicate = mock_communicate
            mock_subprocess.return_value = mock_process

            # Mock prompt
            prompt = "Test prompt for task test-task-123"

            # Execute
            result = await invoker.invoke(sample_task, sample_repo, temp_workdir, prompt)

            # Verify result
            assert isinstance(result, ExecutionResult)
            assert result.success is True
            assert result.session_id == "session-abc123"
            assert result.completion_report == "Task completed successfully"
            assert result.cost_usd == 0.05
            assert result.num_turns == 3

            # Verify git clone was called
            mock_git.assert_called_once()
            git_args = mock_git.call_args[0][0]
            assert git_args[0] == "git"
            assert git_args[1] == "clone"
            assert "--branch" in git_args
            assert "main" in git_args
            assert sample_repo.url in git_args

            # Verify harness was called
            mock_subprocess.assert_called_once()
            harness_args = mock_subprocess.call_args[0]
            assert harness_args[0] == "claude"
            assert "-p" in harness_args
            assert prompt in harness_args
            assert "--output-format" in harness_args
            assert "json" in harness_args

    @pytest.mark.asyncio
    async def test_repo_already_cloned(self, test_config, sample_task, sample_repo, temp_workdir):
        """Test that existing repo is not re-cloned."""
        invoker = HarnessInvoker(test_config)

        # Create existing repo directory with .git
        temp_workdir.mkdir(parents=True)
        (temp_workdir / ".git").mkdir()

        with patch('controller.invoker.subprocess.run') as mock_git, \
             patch('controller.invoker.asyncio.create_subprocess_exec') as mock_subprocess:

            # Mock harness subprocess
            mock_process = Mock()
            mock_process.returncode = 0

            async def mock_communicate():
                return ('{"result": "test", "is_error": false}', "")

            mock_process.communicate = mock_communicate
            mock_subprocess.return_value = mock_process

            # Execute
            await invoker.invoke(sample_task, sample_repo, temp_workdir, "test prompt")

            # Verify git clone was NOT called
            mock_git.assert_not_called()

    @pytest.mark.asyncio
    async def test_git_clone_failure(self, test_config, sample_task, sample_repo, temp_workdir):
        """Test handling of git clone failures."""
        invoker = HarnessInvoker(test_config)

        with patch('controller.invoker.subprocess.run') as mock_git:
            # Mock git clone failure
            mock_git.side_effect = subprocess.CalledProcessError(
                1, ["git", "clone"], stderr="Repository not found"
            )

            # Execute
            result = await invoker.invoke(sample_task, sample_repo, temp_workdir, "test prompt")

            # Verify failure result
            assert isinstance(result, ExecutionResult)
            assert result.success is False
            assert "invocation error" in result.completion_report.lower()

    @pytest.mark.asyncio
    async def test_harness_infrastructure_failure(self, test_config, sample_task, sample_repo, temp_workdir):
        """Test handling of harness infrastructure failures."""
        invoker = HarnessInvoker(test_config)

        with patch('controller.invoker.subprocess.run') as mock_git, \
             patch('controller.invoker.asyncio.create_subprocess_exec') as mock_subprocess:

            # Mock git clone success
            mock_git.return_value = Mock(returncode=0, stderr="", stdout="")

            # Mock harness failure
            mock_process = Mock()
            mock_process.returncode = 1

            async def mock_communicate():
                return ("", "Authentication failed")

            mock_process.communicate = mock_communicate
            mock_subprocess.return_value = mock_process

            # Execute
            result = await invoker.invoke(sample_task, sample_repo, temp_workdir, "test prompt")

            # Verify infrastructure failure handling
            assert result.success is False
            assert "Infrastructure failure" in result.completion_report
            assert "auth" in result.completion_report

    @pytest.mark.asyncio
    async def test_harness_error_response(self, test_config, sample_task, sample_repo, temp_workdir):
        """Test handling of harness error responses."""
        invoker = HarnessInvoker(test_config)

        # Mock harness error output
        error_output = {
            "result": "Failed to complete task due to syntax error",
            "is_error": True,
            "session_id": "session-error123",
            "total_cost_usd": 0.02,
            "num_turns": 1
        }

        with patch('controller.invoker.subprocess.run') as mock_git, \
             patch('controller.invoker.asyncio.create_subprocess_exec') as mock_subprocess:

            # Mock git clone success
            mock_git.return_value = Mock(returncode=0, stderr="", stdout="")

            # Mock harness error response
            mock_process = Mock()
            mock_process.returncode = 0

            async def mock_communicate():
                return (json.dumps(error_output), "")

            mock_process.communicate = mock_communicate
            mock_subprocess.return_value = mock_process

            # Execute
            result = await invoker.invoke(sample_task, sample_repo, temp_workdir, "test prompt")

            # Verify harness error handling
            assert result.success is False
            assert result.session_id == "session-error123"
            assert result.completion_report == "Failed to complete task due to syntax error"
            assert result.cost_usd == 0.02
            assert result.num_turns == 1

    @pytest.mark.asyncio
    async def test_invalid_json_output(self, test_config, sample_task, sample_repo, temp_workdir):
        """Test handling of invalid JSON output from harness."""
        invoker = HarnessInvoker(test_config)

        with patch('controller.invoker.subprocess.run') as mock_git, \
             patch('controller.invoker.asyncio.create_subprocess_exec') as mock_subprocess:

            # Mock git clone success
            mock_git.return_value = Mock(returncode=0, stderr="", stdout="")

            # Mock harness with invalid JSON
            mock_process = Mock()
            mock_process.returncode = 0

            async def mock_communicate():
                return ("Not valid JSON output", "")

            mock_process.communicate = mock_communicate
            mock_subprocess.return_value = mock_process

            # Execute
            result = await invoker.invoke(sample_task, sample_repo, temp_workdir, "test prompt")

            # Verify JSON parse error handling
            assert result.success is False
            assert "Invalid JSON output" in result.completion_report
            assert "Not valid JSON output" in result.completion_report

    @pytest.mark.asyncio
    async def test_harness_timeout(self, test_config, sample_task, sample_repo, temp_workdir):
        """Test handling of harness timeouts."""
        invoker = HarnessInvoker(test_config)

        with patch('controller.invoker.subprocess.run') as mock_git, \
             patch('controller.invoker.asyncio.create_subprocess_exec') as mock_subprocess:

            # Mock git clone success
            mock_git.return_value = Mock(returncode=0, stderr="", stdout="")

            # Mock harness timeout
            mock_process = Mock()
            mock_process.kill = Mock()
            mock_process.wait = AsyncMock()

            # Mock communicate to raise timeout
            async def mock_communicate():
                raise asyncio.TimeoutError()
            mock_process.communicate = mock_communicate
            mock_subprocess.return_value = mock_process

            # Execute
            result = await invoker.invoke(sample_task, sample_repo, temp_workdir, "test prompt")

            # Verify timeout handling
            assert result.success is False
            assert "invocation error" in result.completion_report.lower()

    def test_error_classification(self, test_config):
        """Test infrastructure error classification."""
        invoker = HarnessInvoker(test_config)

        # Test auth error
        result_mock = Mock()
        result_mock.returncode = 1
        result_mock.stderr = "Authentication failed"
        result = invoker._handle_infrastructure_failure(result_mock, "test-task")
        assert "auth" in result.completion_report

        # Test rate limit error
        result_mock.stderr = "Rate limit exceeded"
        result = invoker._handle_infrastructure_failure(result_mock, "test-task")
        assert "rate_limit" in result.completion_report

        # Test OOM error
        result_mock.stderr = "Out of memory"
        result = invoker._handle_infrastructure_failure(result_mock, "test-task")
        assert "oom" in result.completion_report

        # Test timeout error
        result_mock.returncode = 124
        result_mock.stderr = "Process timeout"
        result = invoker._handle_infrastructure_failure(result_mock, "test-task")
        assert "timeout" in result.completion_report

        # Test unknown error
        result_mock.returncode = 2
        result_mock.stderr = "Unknown error"
        result = invoker._handle_infrastructure_failure(result_mock, "test-task")
        assert "unknown" in result.completion_report

    @pytest.mark.asyncio
    async def test_max_turns_parameter(self, test_config, sample_task, sample_repo, temp_workdir):
        """Test that max_turns parameter is passed to harness."""
        invoker = HarnessInvoker(test_config)

        with patch('controller.invoker.subprocess.run') as mock_git, \
             patch('controller.invoker.asyncio.create_subprocess_exec') as mock_subprocess:

            # Mock git clone success
            mock_git.return_value = Mock(returncode=0, stderr="", stdout="")

            # Mock harness success
            mock_process = Mock()
            mock_process.returncode = 0

            async def mock_communicate():
                return ('{"result": "test", "is_error": false}', "")

            mock_process.communicate = mock_communicate
            mock_subprocess.return_value = mock_process

            # Execute
            await invoker.invoke(sample_task, sample_repo, temp_workdir, "test prompt")

            # Verify max_turns parameter was included
            mock_subprocess.assert_called_once()
            harness_args = mock_subprocess.call_args[0]
            assert "--max-turns" in harness_args
            assert str(test_config.max_turns) in harness_args

    @pytest.mark.asyncio
    async def test_cxtx_wrapping_when_cxdb_configured(self, sample_task, sample_repo, temp_workdir):
        """Test that claude is wrapped with cxtx when VF_CXDB_URL is set."""
        config = AgentConfig(
            agent_id="test-cxtx",
            task_timeout=30,
            max_turns=10,
            sessions_dir="/tmp/test-sessions",
            cxdb_url="http://cxdb-server:9010",
        )
        invoker = HarnessInvoker(config)

        with patch('controller.invoker.subprocess.run') as mock_git, \
             patch('controller.invoker.asyncio.create_subprocess_exec') as mock_subprocess:

            mock_git.return_value = Mock(returncode=0, stderr="", stdout="")

            mock_process = Mock()
            mock_process.returncode = 0
            async def mock_communicate():
                return ('{"result": "test", "is_error": false}', "")
            mock_process.communicate = mock_communicate
            mock_subprocess.return_value = mock_process

            await invoker.invoke(sample_task, sample_repo, temp_workdir, "test prompt")

            harness_args = mock_subprocess.call_args[0]
            assert harness_args[0] == "cxtx"
            assert harness_args[1] == "--url"
            assert harness_args[2] == "http://cxdb-server:9010"
            assert harness_args[3] == "--label"
            assert harness_args[4] == "task:test-task-123"
            assert harness_args[5] == "--label"
            assert harness_args[6] == "role:executor"
            assert harness_args[7] == "claude"
            assert harness_args[8] == "--"
            assert "-p" in harness_args
            assert "--output-format" in harness_args

    @pytest.mark.asyncio
    async def test_no_cxtx_when_cxdb_not_configured(self, sample_task, sample_repo, temp_workdir):
        """Test that plain claude is used when VF_CXDB_URL is empty."""
        config = AgentConfig(
            agent_id="test-no-cxtx",
            task_timeout=30,
            max_turns=10,
            sessions_dir="/tmp/test-sessions",
            cxdb_url="",
        )
        invoker = HarnessInvoker(config)

        with patch('controller.invoker.subprocess.run') as mock_git, \
             patch('controller.invoker.asyncio.create_subprocess_exec') as mock_subprocess:

            mock_git.return_value = Mock(returncode=0, stderr="", stdout="")

            mock_process = Mock()
            mock_process.returncode = 0
            async def mock_communicate():
                return ('{"result": "test", "is_error": false}', "")
            mock_process.communicate = mock_communicate
            mock_subprocess.return_value = mock_process

            await invoker.invoke(sample_task, sample_repo, temp_workdir, "test prompt")

            harness_args = mock_subprocess.call_args[0]
            assert harness_args[0] == "claude"
            assert "cxtx" not in harness_args

    # --- Pi harness tests ---

    @pytest.mark.asyncio
    async def test_pi_command_without_cxdb(self, sample_task, sample_repo, temp_workdir):
        """Test Pi command building without cxtx wrapping."""
        config = AgentConfig(
            agent_id="test-pi",
            task_timeout=30,
            max_turns=10,
            sessions_dir="/tmp/test-sessions",
            harness="pi",
            pi_provider="anthropic",
            pi_model="claude-sonnet-4-20250514",
            agent_role="executor",
        )
        invoker = HarnessInvoker(config)

        with patch('controller.invoker.subprocess.run') as mock_git, \
             patch('controller.invoker.asyncio.create_subprocess_exec') as mock_subprocess:

            mock_git.return_value = Mock(returncode=0, stderr="", stdout="")

            mock_process = Mock()
            mock_process.returncode = 0
            pi_output = (
                '{"type":"session","id":"pi-session-1"}\n'
                '{"type":"agent_start"}\n'
                '{"type":"turn_start"}\n'
                '{"type":"turn_end","message":{"role":"assistant","content":[{"type":"text","text":"done"}]},"toolResults":[]}\n'
                '{"type":"agent_end","messages":[{"role":"assistant","content":[{"type":"text","text":"done"}],"usage":{"totalTokens":100,"cost":{"total":0.001}}}]}\n'
            )
            async def mock_communicate():
                return (pi_output, "")
            mock_process.communicate = mock_communicate
            mock_subprocess.return_value = mock_process

            result = await invoker.invoke(sample_task, sample_repo, temp_workdir, "test prompt")

            harness_args = mock_subprocess.call_args[0]
            assert harness_args[0] == "pi"
            assert "-p" in harness_args
            assert "--provider" in harness_args
            assert "anthropic" in harness_args
            assert "--model" in harness_args
            assert "--mode" in harness_args
            assert "json" in harness_args
            assert "--no-session" in harness_args
            assert "--append-system-prompt" in harness_args
            assert "cxtx" not in harness_args

            assert result.success is True
            assert result.session_id == "pi-session-1"
            assert result.completion_report == "done"
            assert result.num_turns == 1

    @pytest.mark.asyncio
    async def test_pi_command_with_cxdb(self, sample_task, sample_repo, temp_workdir):
        """Test Pi command building with cxtx wrapping."""
        config = AgentConfig(
            agent_id="test-pi-cxtx",
            task_timeout=30,
            max_turns=10,
            sessions_dir="/tmp/test-sessions",
            harness="pi",
            pi_provider="anthropic",
            pi_model="claude-sonnet-4-20250514",
            cxdb_url="http://cxdb:9010",
        )
        invoker = HarnessInvoker(config)

        with patch('controller.invoker.subprocess.run') as mock_git, \
             patch('controller.invoker.asyncio.create_subprocess_exec') as mock_subprocess:

            mock_git.return_value = Mock(returncode=0, stderr="", stdout="")
            mock_process = Mock()
            mock_process.returncode = 0
            async def mock_communicate():
                return ('{"type":"session","id":"s1"}\n{"type":"agent_end","messages":[]}\n', "")
            mock_process.communicate = mock_communicate
            mock_subprocess.return_value = mock_process

            await invoker.invoke(sample_task, sample_repo, temp_workdir, "test prompt")

            harness_args = mock_subprocess.call_args[0]
            assert harness_args[0] == "cxtx"
            assert harness_args[1] == "--url"
            assert harness_args[2] == "http://cxdb:9010"
            assert harness_args[3] == "--label"
            assert harness_args[4] == "task:test-task-123"
            # role label follows the task label (P2-1: judge-session pullability)
            assert harness_args[5] == "--label"
            assert harness_args[6] == "role:executor"
            assert harness_args[7] == "pi"
            assert harness_args[8] == "--"
            assert "-p" in harness_args

    @pytest.mark.asyncio
    async def test_cxtx_includes_role_label_executor(self, sample_task, sample_repo, temp_workdir):
        """P2-1: cxtx is labelled with the agent role so the retrospective
        judge-evaluation loop can pull judge sessions specifically. Executor."""
        config = AgentConfig(
            agent_id="test-role-exec",
            task_timeout=30,
            max_turns=10,
            sessions_dir="/tmp/test-sessions",
            agent_role="executor",
            cxdb_url="http://cxdb:9010",
        )
        invoker = HarnessInvoker(config)
        with patch('controller.invoker.subprocess.run') as mock_git, \
             patch('controller.invoker.asyncio.create_subprocess_exec') as mock_subprocess:
            mock_git.return_value = Mock(returncode=0, stderr="", stdout="")
            mock_process = Mock()
            mock_process.returncode = 0
            async def mock_communicate():
                return ('{"result": "test", "is_error": false}', "")
            mock_process.communicate = mock_communicate
            mock_subprocess.return_value = mock_process

            await invoker.invoke(sample_task, sample_repo, temp_workdir, "test prompt")
            harness_args = list(mock_subprocess.call_args[0])
            assert "role:executor" in harness_args
            ri = harness_args.index("role:executor")
            assert harness_args[ri - 1] == "--label"
            assert "task:test-task-123" in harness_args
            # labels precede the "--" separator (and thus the harness binary)
            assert harness_args.index("--") > ri

    @pytest.mark.asyncio
    async def test_cxtx_includes_role_label_judge(self, sample_task, sample_repo, temp_workdir):
        """P2-1: judge sessions carry role:judge so they are filterable."""
        config = AgentConfig(
            agent_id="test-role-judge",
            task_timeout=30,
            max_turns=10,
            sessions_dir="/tmp/test-sessions",
            agent_role="judge",
            cxdb_url="http://cxdb:9010",
        )
        invoker = HarnessInvoker(config)
        with patch('controller.invoker.subprocess.run') as mock_git, \
             patch('controller.invoker.asyncio.create_subprocess_exec') as mock_subprocess:
            mock_git.return_value = Mock(returncode=0, stderr="", stdout="")
            mock_process = Mock()
            mock_process.returncode = 0
            async def mock_communicate():
                return ('{"result": "test", "is_error": false}', "")
            mock_process.communicate = mock_communicate
            mock_subprocess.return_value = mock_process

            await invoker.invoke(sample_task, sample_repo, temp_workdir, "test prompt")
            harness_args = list(mock_subprocess.call_args[0])
            assert "role:judge" in harness_args
            assert harness_args[harness_args.index("role:judge") - 1] == "--label"

    def test_parse_pi_output_success(self, test_config):
        """Test Pi JSONL output parsing with full agent_end event."""
        config = AgentConfig(**{**test_config.__dict__, "harness": "pi"})
        invoker = HarnessInvoker(config)

        pi_output = "\n".join([
            '{"type":"session","version":3,"id":"sess-abc"}',
            '{"type":"agent_start"}',
            '{"type":"turn_start"}',
            '{"type":"turn_end","message":{"role":"assistant"},"toolResults":[]}',
            '{"type":"turn_start"}',
            '{"type":"turn_end","message":{"role":"assistant"},"toolResults":[]}',
            '{"type":"agent_end","messages":[{"role":"user","content":[{"type":"text","text":"do it"}]},{"role":"assistant","content":[{"type":"text","text":"All done"}],"usage":{"totalTokens":500,"cost":{"total":0.005}}}]}',
        ])
        result = invoker._parse_pi_output(pi_output, "task-1")
        assert result.success is True
        assert result.session_id == "sess-abc"
        assert result.completion_report == "All done"
        assert result.num_turns == 2
        assert result.cost_usd == 0.005

    def test_parse_pi_output_empty(self, test_config):
        """Test Pi output parsing with empty stdout."""
        config = AgentConfig(**{**test_config.__dict__, "harness": "pi"})
        invoker = HarnessInvoker(config)
        result = invoker._parse_pi_output("", "task-2")
        assert result.success is False
        assert "no output" in result.completion_report.lower()

    def test_parse_pi_output_malformed_lines(self, test_config):
        """Test Pi output parsing with mix of valid and invalid JSONL."""
        config = AgentConfig(**{**test_config.__dict__, "harness": "pi"})
        invoker = HarnessInvoker(config)

        pi_output = "\n".join([
            '{"type":"session","id":"sess-ok"}',
            'not valid json',
            '{"type":"turn_end","message":{}}',
            '{"type":"agent_end","messages":[{"role":"assistant","content":[{"type":"text","text":"recovered"}],"usage":{"totalTokens":50,"cost":{"total":0}}}]}',
        ])
        result = invoker._parse_pi_output(pi_output, "task-3")
        assert result.success is True
        assert result.session_id == "sess-ok"
        assert result.completion_report == "recovered"
        assert result.num_turns == 1

    def test_parse_pi_output_no_agent_end(self, test_config):
        """Test Pi output parsing when stream is cut short (no agent_end)."""
        config = AgentConfig(**{**test_config.__dict__, "harness": "pi"})
        invoker = HarnessInvoker(config)

        pi_output = "\n".join([
            '{"type":"session","id":"sess-cut"}',
            '{"type":"agent_start"}',
            '{"type":"turn_start"}',
        ])
        result = invoker._parse_pi_output(pi_output, "task-4")
        assert result.success is True
        assert result.session_id == "sess-cut"
        assert result.completion_report == "Task completed"
        assert result.num_turns == 0

    def test_parse_pi_output_with_tool_use(self, test_config):
        """Test Pi output parsing with tool execution events."""
        config = AgentConfig(**{**test_config.__dict__, "harness": "pi"})
        invoker = HarnessInvoker(config)

        pi_output = "\n".join([
            '{"type":"session","id":"sess-tools"}',
            '{"type":"agent_start"}',
            '{"type":"turn_start"}',
            '{"type":"tool_execution_start","toolCallId":"c1","toolName":"bash"}',
            '{"type":"tool_execution_end","toolCallId":"c1","isError":false}',
            '{"type":"turn_end","message":{"role":"assistant"},"toolResults":[{"callId":"c1"}]}',
            '{"type":"agent_end","messages":[{"role":"assistant","content":[{"type":"text","text":"ran tests"}],"usage":{"totalTokens":200,"cost":{"total":0}}}]}',
        ])
        result = invoker._parse_pi_output(pi_output, "task-5")
        assert result.success is True
        assert result.completion_report == "ran tests"
        assert result.num_turns == 1

    @pytest.mark.asyncio
    async def test_cxtx_with_max_turns(self, sample_task, sample_repo, temp_workdir):
        """Test that max_turns appears after -- in cxtx-wrapped command."""
        config = AgentConfig(
            agent_id="test-cxtx-turns",
            task_timeout=30,
            max_turns=25,
            sessions_dir="/tmp/test-sessions",
            cxdb_url="http://cxdb-server:9010",
        )
        invoker = HarnessInvoker(config)

        with patch('controller.invoker.subprocess.run') as mock_git, \
             patch('controller.invoker.asyncio.create_subprocess_exec') as mock_subprocess:

            mock_git.return_value = Mock(returncode=0, stderr="", stdout="")

            mock_process = Mock()
            mock_process.returncode = 0
            async def mock_communicate():
                return ('{"result": "test", "is_error": false}', "")
            mock_process.communicate = mock_communicate
            mock_subprocess.return_value = mock_process

            await invoker.invoke(sample_task, sample_repo, temp_workdir, "test prompt")

            harness_args = list(mock_subprocess.call_args[0])
            separator_idx = harness_args.index("--")
            max_turns_idx = harness_args.index("--max-turns")
            label_idx = harness_args.index("--label")
            # label must appear before -- separator
            assert label_idx < separator_idx
            assert "task:test-task-123" in harness_args[label_idx + 1]
            # max-turns must appear after the -- separator
            assert max_turns_idx > separator_idx
            assert harness_args[max_turns_idx + 1] == "25"

    # ---- D1: empty-string `result` guard (kb gotcha XsPemtnm) ----
    # Claude's harness can return {"result": ""} (e.g. max_turns hit before
    # the agent emits a final text turn). `.get("result", "Task completed")`
    # returns the empty string, not the default — that blank string is then
    # posted as the completion note via VtfWorkSource.complete(), and vtf
    # rejects empty Note.text with 400, aborting the gates-passed delivery.
    # Use `or` so falsy values (empty string, None) also fall back.

    def test_parse_claude_output_empty_result_success_falls_back(self, test_config):
        invoker = HarnessInvoker(test_config)
        stdout = json.dumps({
            "result": "",
            "is_error": False,
            "session_id": "sess-empty-ok",
            "total_cost_usd": 0.0,
            "num_turns": 47,
        })
        result = invoker._parse_claude_output(stdout, "task-empty-ok")
        assert result.success is True
        assert result.session_id == "sess-empty-ok"
        # Must NOT be the empty string — vtf rejects blank Note.text with 400
        assert result.completion_report == "Task completed"

    def test_parse_claude_output_empty_result_error_falls_back(self, test_config):
        invoker = HarnessInvoker(test_config)
        stdout = json.dumps({
            "result": "",
            "is_error": True,
            "session_id": "sess-empty-err",
            "total_cost_usd": 0.0,
            "num_turns": 1,
        })
        result = invoker._parse_claude_output(stdout, "task-empty-err")
        assert result.success is False
        assert result.completion_report == "Harness reported an error"


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), check=True,
                           capture_output=True, text=True).stdout.strip()


class TestHttpsGithubToSsh:
    """#15: pure HTTPS→SSH GitHub URL transform. See
    docs/issue-15-https-ssh-remote-DESIGN.md."""

    def test_https_with_dotgit(self):
        from controller.invoker import https_github_to_ssh
        assert https_github_to_ssh("https://github.com/test/repo.git") == "git@github.com:test/repo.git"

    def test_https_without_dotgit(self):
        from controller.invoker import https_github_to_ssh
        assert https_github_to_ssh("https://github.com/test/repo") == "git@github.com:test/repo.git"

    def test_https_trailing_slash(self):
        from controller.invoker import https_github_to_ssh
        assert https_github_to_ssh("https://github.com/vilosource/vtf-canary/") == "git@github.com:vilosource/vtf-canary.git"

    def test_already_ssh_scp_form_is_none(self):
        from controller.invoker import https_github_to_ssh
        assert https_github_to_ssh("git@github.com:test/repo.git") is None

    def test_ssh_scheme_is_none(self):
        from controller.invoker import https_github_to_ssh
        assert https_github_to_ssh("ssh://git@github.com/test/repo.git") is None

    def test_non_github_host_is_none(self):
        from controller.invoker import https_github_to_ssh
        assert https_github_to_ssh("https://gitlab.com/test/repo.git") is None

    def test_incomplete_path_is_none(self):
        from controller.invoker import https_github_to_ssh
        assert https_github_to_ssh("https://github.com/onlyowner") is None

    def test_garbage_is_none(self):
        from controller.invoker import https_github_to_ssh
        assert https_github_to_ssh("") is None
        assert https_github_to_ssh("not a url") is None


class TestNormalizeOriginForPush:
    """#15: integration — origin is rewritten to SSH iff a GitHub https
    URL AND the mounted ssh key is present; otherwise untouched."""

    def _repo_with_origin(self, tmp_path, url):
        wd = tmp_path / "wd"
        wd.mkdir()
        _git(wd, "init", "-q", ".")
        _git(wd, "remote", "add", "origin", url)
        return wd

    def test_rewrites_when_key_present(self, tmp_path, test_config):
        wd = self._repo_with_origin(tmp_path, "https://github.com/test/repo.git")
        key = tmp_path / "id_ed25519"; key.write_text("x")
        inv = HarnessInvoker(test_config)
        inv.ssh_key_path = key
        inv._normalize_origin_for_push(wd, "https://github.com/test/repo.git")
        assert _git(wd, "remote", "get-url", "origin") == "git@github.com:test/repo.git"

    def test_unchanged_when_key_absent(self, tmp_path, test_config):
        wd = self._repo_with_origin(tmp_path, "https://github.com/test/repo.git")
        inv = HarnessInvoker(test_config)
        inv.ssh_key_path = tmp_path / "missing"
        inv._normalize_origin_for_push(wd, "https://github.com/test/repo.git")
        assert _git(wd, "remote", "get-url", "origin") == "https://github.com/test/repo.git"

    def test_non_github_unchanged_even_with_key(self, tmp_path, test_config):
        wd = self._repo_with_origin(tmp_path, "https://gitlab.com/test/repo.git")
        key = tmp_path / "id_ed25519"; key.write_text("x")
        inv = HarnessInvoker(test_config)
        inv.ssh_key_path = key
        inv._normalize_origin_for_push(wd, "https://gitlab.com/test/repo.git")
        assert _git(wd, "remote", "get-url", "origin") == "https://gitlab.com/test/repo.git"


class TestCloneUrl:
    """#17/R1: clone-URL resolver — clone via the managed SSH credential
    when GitHub-HTTPS and the mounted key is present (else unchanged).
    See docs/issue-17-clone-ssh-DESIGN.md."""

    def _inv(self, test_config, key=None):
        inv = HarnessInvoker(test_config)
        inv.ssh_key_path = key if key is not None else Path("/nonexistent/key")
        return inv

    def test_github_https_with_key_becomes_ssh(self, tmp_path, test_config):
        k = tmp_path / "id_ed25519"; k.write_text("x")
        inv = self._inv(test_config, k)
        assert inv._clone_url("https://github.com/o/r.git") == "git@github.com:o/r.git"

    def test_github_https_without_key_unchanged(self, test_config):
        inv = self._inv(test_config)  # key path missing
        assert inv._clone_url("https://github.com/o/r.git") == "https://github.com/o/r.git"

    def test_non_github_unchanged_even_with_key(self, tmp_path, test_config):
        k = tmp_path / "id_ed25519"; k.write_text("x")
        inv = self._inv(test_config, k)
        assert inv._clone_url("https://gitlab.com/o/r.git") == "https://gitlab.com/o/r.git"

    def test_already_ssh_unchanged(self, tmp_path, test_config):
        k = tmp_path / "id_ed25519"; k.write_text("x")
        inv = self._inv(test_config, k)
        assert inv._clone_url("git@github.com:o/r.git") == "git@github.com:o/r.git"


class TestCloneCommandUsesResolvedUrl:
    """#17/R1: the actual `git clone` command must carry the SSH URL iff
    GitHub-HTTPS AND key present; HTTPS/unchanged otherwise."""

    def _clone_argv(self, mock_run):
        for c in mock_run.call_args_list:
            argv = c.args[0] if c.args else c.kwargs.get("args", [])
            if isinstance(argv, list) and argv[:2] == ["git", "clone"]:
                return argv
        return None

    @pytest.mark.asyncio
    async def test_clone_uses_ssh_when_key_present(self, tmp_path, test_config):
        inv = HarnessInvoker(test_config)
        k = tmp_path / "id_ed25519"; k.write_text("x"); inv.ssh_key_path = k
        repo = RepoInfo(url="https://github.com/o/r.git", branch="main")
        wd = tmp_path / "wd"
        with patch("controller.invoker.subprocess.run") as mr:
            mr.return_value = Mock(returncode=0, stderr="", stdout="")
            await inv._ensure_repo_cloned(repo, wd)
        argv = self._clone_argv(mr)
        assert argv is not None and "git@github.com:o/r.git" in argv
        assert "https://github.com/o/r.git" not in argv

    @pytest.mark.asyncio
    async def test_clone_uses_https_when_no_key(self, tmp_path, test_config):
        inv = HarnessInvoker(test_config)
        inv.ssh_key_path = tmp_path / "missing"
        repo = RepoInfo(url="https://github.com/o/r.git", branch="main")
        wd = tmp_path / "wd"
        with patch("controller.invoker.subprocess.run") as mr:
            mr.return_value = Mock(returncode=0, stderr="", stdout="")
            await inv._ensure_repo_cloned(repo, wd)
        argv = self._clone_argv(mr)
        assert argv is not None and "https://github.com/o/r.git" in argv

    @pytest.mark.asyncio
    async def test_clone_non_github_unchanged(self, tmp_path, test_config):
        inv = HarnessInvoker(test_config)
        k = tmp_path / "id_ed25519"; k.write_text("x"); inv.ssh_key_path = k
        repo = RepoInfo(url="https://gitlab.com/o/r.git", branch="main")
        wd = tmp_path / "wd"
        with patch("controller.invoker.subprocess.run") as mr:
            mr.return_value = Mock(returncode=0, stderr="", stdout="")
            await inv._ensure_repo_cloned(repo, wd)
        argv = self._clone_argv(mr)
        assert argv is not None and "https://gitlab.com/o/r.git" in argv


class TestColdStartCloneFallback:
    """WC-1.1 (vtaskforge#13): a DAG root node's server-derived
    base_ref IS the milestone integration branch — but that git ref
    does not exist on origin until the first integrate() (post-approval).
    The clone path (only) must fall back to the project default at cold
    start; any other clone failure still fails loud. The integrate
    target (get_task_repo_info.branch) is deliberately NOT changed —
    see test_wc2_controller_integration."""

    def _clone_branches(self, mock_run):
        out = []
        for c in mock_run.call_args_list:
            argv = c.args[0] if c.args else c.kwargs.get("args", [])
            if isinstance(argv, list) and argv[:2] == ["git", "clone"] \
                    and "--branch" in argv:
                out.append(argv[argv.index("--branch") + 1])
        return out

    @pytest.mark.asyncio
    async def test_falls_back_to_default_when_base_ref_branch_absent(
            self, tmp_path, test_config):
        inv = HarnessInvoker(test_config)
        inv.ssh_key_path = tmp_path / "missing"
        repo = RepoInfo(url="https://github.com/o/r.git",
                        branch="vafi/wg-ms1", fallback_branch="main")
        wd = tmp_path / "wd"
        not_found = subprocess.CalledProcessError(
            128, "git",
            stderr="fatal: Remote branch vafi/wg-ms1 not found "
                   "in upstream origin\n")

        def fake_run(cmd, **kw):
            if isinstance(cmd, list) and cmd[:2] == ["git", "clone"] \
                    and cmd[cmd.index("--branch") + 1] == "vafi/wg-ms1":
                raise not_found
            return Mock(returncode=0, stderr="", stdout="")

        with patch("controller.invoker.subprocess.run",
                   side_effect=fake_run) as mr:
            await inv._ensure_repo_cloned(repo, wd)
        assert self._clone_branches(mr) == ["vafi/wg-ms1", "main"]

    @pytest.mark.asyncio
    async def test_other_clone_error_still_fails_loud(
            self, tmp_path, test_config):
        inv = HarnessInvoker(test_config)
        inv.ssh_key_path = tmp_path / "missing"
        repo = RepoInfo(url="https://github.com/o/r.git",
                        branch="vafi/wg-ms1", fallback_branch="main")
        wd = tmp_path / "wd"
        auth_err = subprocess.CalledProcessError(
            128, "git",
            stderr="fatal: could not read Username for "
                   "'https://github.com': terminal prompts disabled\n")
        with patch("controller.invoker.subprocess.run",
                   side_effect=auth_err):
            with pytest.raises(subprocess.CalledProcessError):
                await inv._ensure_repo_cloned(repo, wd)

    @pytest.mark.asyncio
    async def test_no_fallback_when_branch_exists(
            self, tmp_path, test_config):
        inv = HarnessInvoker(test_config)
        inv.ssh_key_path = tmp_path / "missing"
        repo = RepoInfo(url="https://github.com/o/r.git",
                        branch="vafi/wg-ms1", fallback_branch="main")
        wd = tmp_path / "wd"
        with patch("controller.invoker.subprocess.run") as mr:
            mr.return_value = Mock(returncode=0, stderr="", stdout="")
            await inv._ensure_repo_cloned(repo, wd)
        assert self._clone_branches(mr) == ["vafi/wg-ms1"]

    @pytest.mark.asyncio
    async def test_no_fallback_when_fallback_equals_branch(
            self, tmp_path, test_config):
        # single-task / V16 path: branch == fallback == project default.
        # A not-found there is a genuine error, not a cold start.
        inv = HarnessInvoker(test_config)
        inv.ssh_key_path = tmp_path / "missing"
        repo = RepoInfo(url="https://github.com/o/r.git",
                        branch="main", fallback_branch="main")
        wd = tmp_path / "wd"
        nf = subprocess.CalledProcessError(
            128, "git",
            stderr="fatal: Remote branch main not found in "
                   "upstream origin\n")
        with patch("controller.invoker.subprocess.run", side_effect=nf):
            with pytest.raises(subprocess.CalledProcessError):
                await inv._ensure_repo_cloned(repo, wd)
