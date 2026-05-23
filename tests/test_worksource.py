"""Tests for WorkSource protocol and VtfWorkSource implementation."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from vtf_sdk.async_client import AsyncVtfClient
from vtf_sdk.entities import Task, Agent, Project, Review, Note
from vtf_sdk.pagination import PagedResult
from vtf_sdk.refs import ProjectRef, TaskRef, UserActor, WorkplanRef

from src.controller.types import AgentInfo, TaskInfo, RepoInfo, ReworkContext, ExecutionResult, GateResult
from src.controller.worksources.protocol import WorkSource
from src.controller.worksources.vtf import VtfWorkSource


def _make_sdk_task(**overrides):
    defaults = {
        "id": "task-001", "title": "Test Task", "description": "",
        "status": "draft", "project": ProjectRef(id="proj-1", name="Proj"),
        "spec": "test spec", "test_command": {}, "labels": [],
        "acceptance_criteria": [], "requires": [],
        "agent_model": "", "judge": False, "isolation": "",
        "retry_count": 0,
    }
    defaults.update(overrides)
    return Task.model_validate(defaults)


def _make_paged(items):
    return PagedResult(items=items, has_more=False)


class TestWorkSourceProtocol:
    def test_vtfworksource_implements_protocol(self):
        mock_client = MagicMock()
        work_source = VtfWorkSource(mock_client)
        protocol_methods = {
            'register', 'poll', 'claim', 'heartbeat', 'complete', 'fail',
            'submit', 'list_submittable', 'submit_review', 'get_repo_info',
            'get_rework_context', 'get_task_context', 'add_note',
            'agent_heartbeat', 'set_agent_offline', 'poll_reviews',
            'count_rework_attempts',
        }
        for method_name in protocol_methods:
            method = getattr(work_source, method_name, None)
            assert method is not None and callable(method), f"{method_name} missing or not callable"


class TestVtfWorkSource:

    def setup_method(self):
        self.mock_client = MagicMock(spec=AsyncVtfClient)
        # Set up async mock managers
        self.mock_client.tasks = AsyncMock()
        self.mock_client.agents = AsyncMock()
        self.mock_client.projects = AsyncMock()
        self.mock_client.reviews = AsyncMock()
        self.work_source = VtfWorkSource(self.mock_client)

    async def test_register(self):
        agent = Agent.model_validate({"id": "agent_123", "name": "test-agent", "tags": ["executor"],
                                       "status": "online", "effective_status": "online"})
        self.mock_client.agents.register.return_value = (agent, {"id": "agent_123", "token": "test_token_456"})
        # Mock transport for token update
        self.mock_client._transport = MagicMock()
        self.mock_client._transport._client = MagicMock()
        self.mock_client._transport._client.headers = {}

        result = await self.work_source.register("test-agent", ["executor", "claude"])
        assert isinstance(result, AgentInfo)
        assert result.id == "agent_123"
        assert result.token == "test_token_456"

    async def test_poll_returns_rework_first(self):
        rework_task = _make_sdk_task(id="task_rework", title="Rework Task", status="changes_requested")
        self.mock_client.tasks.list.return_value = _make_paged([rework_task])

        result = await self.work_source.poll("agent_123", ["executor"])
        assert isinstance(result, TaskInfo)
        assert result.id == "task_rework"

    async def test_poll_returns_claimable_when_no_rework(self):
        self.mock_client.tasks.list.return_value = _make_paged([])
        claimable_task = _make_sdk_task(id="task_new", title="New Task")
        self.mock_client.tasks.claimable.return_value = _make_paged([claimable_task])

        result = await self.work_source.poll("agent_123", ["executor"])
        assert isinstance(result, TaskInfo)
        assert result.id == "task_new"

    async def test_poll_returns_none_when_no_work(self):
        self.mock_client.tasks.list.return_value = _make_paged([])
        self.mock_client.tasks.claimable.return_value = _make_paged([])

        result = await self.work_source.poll("agent_123", ["executor"])
        assert result is None

    async def test_claim(self):
        claimed_task = _make_sdk_task(id="task_123", title="Test Task", status="doing")
        self.mock_client.tasks.claim.return_value = claimed_task

        result = await self.work_source.claim("task_123", "agent_123")
        assert isinstance(result, TaskInfo)
        assert result.id == "task_123"

    def test_sdk_task_to_info_synthesizes_workgraph_id_when_no_milestone(self):
        """F9 (High): a task with no milestone must still get a stable,
        namespaced synthetic workgraph_id ('task-<id>') so its events are
        emitted instead of dropped. Milestone-less tasks (one-off spikes,
        ad-hoc bugfixes) were previously fully invisible to observability."""
        task = _make_sdk_task(id="task_solo", title="Standalone")  # no milestone
        info = self.work_source._sdk_task_to_info(task)
        assert info.workgraph_id == "task-task_solo"

    def test_sdk_task_to_info_uses_milestone_id_verbatim(self):
        """A real milestone id is used unchanged (no prefix) — only the
        absent case is synthesized."""
        from vtf_sdk.refs import MilestoneRef
        task = _make_sdk_task(
            id="t1",
            milestone=MilestoneRef(id="ms_abc", name="WG", status="active"),
        )
        info = self.work_source._sdk_task_to_info(task)
        assert info.workgraph_id == "ms_abc"

    async def test_poll_reviews_calls_reviews_pending_not_tasks_list(self):
        """Regression for vtaskforge#6.

        poll_reviews must hit the dedicated /v2/reviews/pending/ endpoint
        (via reviews.pending) so non-member judge agents see fleet-wide
        work. Calling tasks.list(status='pending_completion_review')
        applies project-membership scoping and silently returns empty.
        """
        review_task = _make_sdk_task(id="task_review", title="Review me", status="pending_completion_review")
        self.mock_client.reviews.pending.return_value = _make_paged([review_task])

        result = await self.work_source.poll_reviews("agent_judge")
        assert isinstance(result, TaskInfo)
        assert result.id == "task_review"
        self.mock_client.reviews.pending.assert_awaited()
        # Make sure we did NOT fall back to the broken path
        self.mock_client.tasks.list.assert_not_awaited()

    async def test_poll_reviews_returns_none_when_empty(self):
        self.mock_client.reviews.pending.return_value = _make_paged([])
        result = await self.work_source.poll_reviews("agent_judge")
        assert result is None

    async def test_heartbeat(self):
        await self.work_source.heartbeat("task_123")
        self.mock_client.tasks.heartbeat.assert_called_once_with("task_123")

    async def test_complete_with_session_id(self):
        execution_result = ExecutionResult(
            success=True, session_id="session_abc123",
            completion_report="Task completed successfully",
            cost_usd=0.042, num_turns=5,
            gate_results=[GateResult(name="test_gate", command="pytest tests/",
                                      exit_code=0, stdout="All tests passed", passed=True)],
        )
        await self.work_source.complete("task_123", execution_result)
        assert self.mock_client.tasks.add_note.call_count == 3
        self.mock_client.tasks.complete.assert_called_once_with("task_123")

    async def test_complete_without_session_id(self):
        execution_result = ExecutionResult(
            success=True, session_id=None,
            completion_report="Done",
            cost_usd=0.01, num_turns=1, gate_results=[],
        )
        await self.work_source.complete("task_123", execution_result)
        assert self.mock_client.tasks.add_note.call_count == 2  # report + metadata, no session

    async def test_fail(self):
        await self.work_source.fail("task_123", "Some error occurred")
        self.mock_client.tasks.add_note.assert_called_once()
        self.mock_client.tasks.fail.assert_called_once_with("task_123")

    async def test_get_repo_info(self):
        project = Project.model_validate({
            "id": "proj-1", "name": "Proj", "repo_url": "git@github.com:test/repo.git",
            "default_branch": "develop", "status": "active",
        })
        self.mock_client.projects.get.return_value = project

        result = await self.work_source.get_repo_info("proj-1")
        assert isinstance(result, RepoInfo)
        assert result.url == "git@github.com:test/repo.git"
        assert result.branch == "develop"

    async def test_get_rework_context(self):
        review = Review(
            id="rev-1", task=TaskRef(id="t1", title="T", status="doing"),
            decision="changes_requested", reason="Fix the bug",
            reviewer=UserActor(type="user", id="1", username="judge"),
            reviewer_type="agent",
        )
        task = _make_sdk_task(id="task_rework", reviews=[review])
        self.mock_client.tasks.get.return_value = task
        self.mock_client.tasks.list_notes.return_value = _make_paged([
            Note(id="n1", text="vafi:session_id=sess-abc", task=TaskRef(id="t1", title="T", status="doing")),
        ])

        result = await self.work_source.get_rework_context("task_rework")
        assert isinstance(result, ReworkContext)
        assert result.judge_feedback == "Fix the bug"
        assert result.session_id == "sess-abc"

    async def test_get_rework_context_no_session_id(self):
        task = _make_sdk_task(id="task_rework", reviews=[])
        self.mock_client.tasks.get.return_value = task
        self.mock_client.tasks.list_notes.return_value = _make_paged([])

        result = await self.work_source.get_rework_context("task_rework")
        assert result.session_id is None
        assert result.judge_feedback == ""

    async def test_count_rework_attempts(self):
        reviews = [
            Review(id="r1", task=TaskRef(id="t1", title="T", status="doing"),
                   decision="changes_requested", reason="Fix", reviewer_type="agent"),
            Review(id="r2", task=TaskRef(id="t1", title="T", status="doing"),
                   decision="approved", reason="OK", reviewer_type="agent"),
            Review(id="r3", task=TaskRef(id="t1", title="T", status="doing"),
                   decision="changes_requested", reason="Fix again", reviewer_type="agent"),
        ]
        task = _make_sdk_task(reviews=reviews)
        self.mock_client.tasks.get.return_value = task

        count = await self.work_source.count_rework_attempts("task_123")
        assert count == 2

    async def test_submit(self):
        self.mock_client.tasks.submit.return_value = _make_sdk_task(status="todo")
        await self.work_source.submit("task_123")
        self.mock_client.tasks.submit.assert_called_once_with("task_123")

    async def test_list_submittable_with_dependencies(self):
        task = _make_sdk_task(
            id="task-sub", status="draft",
            requires=[TaskRef(id="dep-1", title="Dep", status="done")],
        )
        self.mock_client.tasks.list.return_value = _make_paged([task])

        result = await self.work_source.list_submittable()
        assert len(result) == 1
        assert result[0].id == "task-sub"

    async def test_list_submittable_no_dependencies(self):
        task = _make_sdk_task(id="task-nodep", status="draft")
        self.mock_client.tasks.list.return_value = _make_paged([task])

        result = await self.work_source.list_submittable()
        assert len(result) == 1

    async def test_submit_review(self):
        self.mock_client.tasks.submit_review.return_value = Review(
            id="rev-new", task=TaskRef(id="t1", title="T", status="doing"),
            decision="approved", reason="LGTM", reviewer_type="agent",
        )
        await self.work_source.submit_review("task_123", "approved", "LGTM", "reviewer-1")
        self.mock_client.tasks.submit_review.assert_called_once()

    async def test_agent_heartbeat(self):
        self.mock_client.agents.update.return_value = Agent.model_validate({
            "id": "agent_123", "name": "test", "status": "online", "effective_status": "online",
        })
        await self.work_source.agent_heartbeat("agent_123")
        self.mock_client.agents.update.assert_called_once()

    async def test_set_agent_offline(self):
        self.mock_client.agents.update_status.return_value = Agent.model_validate({
            "id": "agent_123", "name": "test", "status": "offline", "effective_status": "offline",
        })
        await self.work_source.set_agent_offline("agent_123")
        self.mock_client.agents.update_status.assert_called_once_with("agent_123", status="offline")

    # ---- D2: complete() resilience (kb gotcha XsPemtnm) ----
    # complete() posts up to three notes (completion report, session_id,
    # execution metadata) and then calls tasks.complete(). If any add_note
    # raises (e.g. vtf 400 on blank Note.text from a max_turns claude run),
    # the chain previously aborted BEFORE tasks.complete() — leaving a
    # gates-passed delivery in `doing` and the controller treated it as a
    # fatal failure (→ needs_attention). A successful delivery whose
    # gates passed must reach pending_completion_review, period.

    async def test_complete_continues_when_completion_note_fails(self):
        """First add_note (completion report) raises → metadata note + tasks.complete still run."""
        execution_result = ExecutionResult(
            success=True, session_id="sess-xyz",
            completion_report="report text",
            cost_usd=0.01, num_turns=2, gate_results=[],
        )
        # Make ONLY the completion-report note (first call) fail.
        call_count = {"n": 0}
        async def add_note_side_effect(task_id, text):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("vtf 400: text must not be blank")
        self.mock_client.tasks.add_note.side_effect = add_note_side_effect

        await self.work_source.complete("task_123", execution_result)

        # All three add_note attempts were made (none short-circuited).
        assert self.mock_client.tasks.add_note.call_count == 3
        # The terminal complete MUST still have been called.
        self.mock_client.tasks.complete.assert_called_once_with("task_123")

    async def test_complete_continues_when_session_id_note_fails(self):
        """Middle add_note (session_id) failing must not abort completion."""
        execution_result = ExecutionResult(
            success=True, session_id="sess-xyz",
            completion_report="ok",
            cost_usd=0.01, num_turns=2, gate_results=[],
        )
        call_count = {"n": 0}
        async def add_note_side_effect(task_id, text):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise Exception("vtf 500")
        self.mock_client.tasks.add_note.side_effect = add_note_side_effect

        await self.work_source.complete("task_123", execution_result)

        assert self.mock_client.tasks.add_note.call_count == 3
        self.mock_client.tasks.complete.assert_called_once_with("task_123")

    async def test_complete_continues_when_metadata_note_fails(self):
        """Last add_note (metadata) failing must not abort completion."""
        execution_result = ExecutionResult(
            success=True, session_id="sess-xyz",
            completion_report="ok",
            cost_usd=0.01, num_turns=2, gate_results=[],
        )
        call_count = {"n": 0}
        async def add_note_side_effect(task_id, text):
            call_count["n"] += 1
            if call_count["n"] == 3:
                raise Exception("vtf 500")
        self.mock_client.tasks.add_note.side_effect = add_note_side_effect

        await self.work_source.complete("task_123", execution_result)

        assert self.mock_client.tasks.add_note.call_count == 3
        self.mock_client.tasks.complete.assert_called_once_with("task_123")

    async def test_complete_continues_when_all_notes_fail(self):
        """Even if EVERY note fails, the terminal complete() must run."""
        execution_result = ExecutionResult(
            success=True, session_id="sess-xyz",
            completion_report="ok",
            cost_usd=0.01, num_turns=2, gate_results=[],
        )
        async def add_note_side_effect(task_id, text):
            raise Exception("vtf down")
        self.mock_client.tasks.add_note.side_effect = add_note_side_effect

        await self.work_source.complete("task_123", execution_result)

        assert self.mock_client.tasks.add_note.call_count == 3
        self.mock_client.tasks.complete.assert_called_once_with("task_123")

    async def test_complete_guards_blank_completion_report_text(self):
        """Belt-and-braces with D1: if completion_report somehow IS blank,
        complete() must substitute a non-empty placeholder so the note
        post doesn't 400. (D1 fixes this at source; D2 defends in depth.)"""
        execution_result = ExecutionResult(
            success=True, session_id="sess-xyz",
            completion_report="",  # the bug payload
            cost_usd=0.01, num_turns=47, gate_results=[],
        )
        captured_texts = []
        async def add_note_capture(task_id, text):
            captured_texts.append(text)
        self.mock_client.tasks.add_note.side_effect = add_note_capture

        await self.work_source.complete("task_123", execution_result)

        # First call is the completion-report note — its text must be non-empty.
        assert captured_texts[0] != "", \
            "complete() must substitute a placeholder for blank completion_report text"
        self.mock_client.tasks.complete.assert_called_once_with("task_123")
