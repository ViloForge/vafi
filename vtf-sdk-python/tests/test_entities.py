"""Step 2: Entity type tests — Pydantic models parse v2 API responses."""
import pytest

# Sample v2 API response dicts for testing
V2_TASK = {
    "id": "tsk-abc-123",
    "title": "Add auth endpoint",
    "description": "Implement token validation",
    "status": "doing",
    "project": {"id": "p1", "name": "Auth System"},
    "workplan": {"id": "wp1", "name": "Platform Hardening"},
    "milestone": {"id": "ms1", "name": "Phase 1 Core", "status": "active"},
    "labels": ["backend", "security"],
    "acceptance_criteria": ["Token validated", "Tests pass"],
    "needs_review_before_start": False,
    "needs_review_on_completion": True,
    "review_return_to": None,
    "requires": [{"id": "tsk-dep", "title": "Create user model", "status": "done"}],
    "assigned_to": {"type": "agent", "id": "agt-001", "name": "executor-1", "pod_name": "pod-abc"},
    "claimed_by": {"type": "agent", "id": "agt-001", "name": "executor-1", "pod_name": "pod-abc"},
    "claimed_at": "2026-04-03T10:00:00Z",
    "claim_timeout": "PT30M",
    "claim_expires_at": "2026-04-03T10:30:00Z",
    "created_by": {"type": "user", "id": "42", "username": "jdoe"},
    "spec": "...",
    "agent_model": "sonnet",
    "test_command": {"unit": "pytest tests/"},
    "judge": True,
    "isolation": "worktree",
    "retry_count": 0,
    "execution_summary": None,
    "created_at": "2026-04-03T09:00:00Z",
    "updated_at": "2026-04-03T10:00:00Z",
    "permissions": {
        "can_edit": True,
        "can_delete": False,
        "available_actions": ["complete", "fail", "block"],
    },
}

V2_PROJECT = {
    "id": "p1", "name": "Auth System", "description": "Auth service",
    "slug": "auth-system",
    "status": "active", "repo_url": "https://github.com/x/y",
    "default_branch": "main", "tags": ["backend"],
    "owner": {"type": "user", "id": "42", "username": "jdoe"},
    "created_by": {"type": "user", "id": "42", "username": "jdoe"},
    "created_at": "2026-03-01T00:00:00Z", "updated_at": "2026-04-03T10:00:00Z",
    "permissions": {"can_edit": True, "can_delete": False, "can_archive": True, "can_manage_members": True},
}

V2_WORKPLAN = {
    "id": "wp1", "name": "Platform Hardening", "description": "Security",
    "status": "active", "project": {"id": "p1", "name": "Auth System"},
    "owner": {"type": "user", "id": "42", "username": "jdoe"},
    "tags": [], "target_date": None,
    "default_needs_review_before_start": False,
    "default_needs_review_on_completion": True,
    "created_by": {"type": "user", "id": "42", "username": "jdoe"},
    "created_at": "2026-03-15T00:00:00Z", "updated_at": "2026-04-03T00:00:00Z",
    "permissions": {"can_edit": True, "can_delete": False, "can_archive": True, "can_complete": True},
}

V2_MILESTONE = {
    "id": "ms1", "name": "Phase 1 Core", "description": "Core paths",
    "status": "active", "order": 1,
    "workplan": {"id": "wp1", "name": "Platform Hardening"},
    "default_needs_review_before_start": None,
    "default_needs_review_on_completion": None,
    "created_by": {"type": "user", "id": "42", "username": "jdoe"},
    "created_at": "2026-03-15T00:00:00Z", "updated_at": "2026-04-03T00:00:00Z",
    "permissions": {"can_edit": True, "can_delete": False, "can_activate": True, "can_complete": False},
}

V2_AGENT = {
    "id": "agt-001", "name": "executor-1", "tags": ["executor"],
    "status": "online", "effective_status": "online",
    "last_heartbeat": "2026-04-03T10:29:00Z", "pod_name": "pod-abc",
    "registered_at": "2026-04-01T00:00:00Z",
    "current_task": {"id": "tsk-abc", "title": "Add auth", "status": "doing"},
    "tasks_completed": 14, "tasks_failed": 1,
    "created_at": "2026-04-01T00:00:00Z", "updated_at": "2026-04-03T10:29:00Z",
}

V2_REVIEW = {
    "id": "rev-1", "task": {"id": "tsk-abc", "title": "Add auth", "status": "doing"},
    "decision": "approved", "reason": "LGTM",
    "reviewer": {"type": "user", "id": "42", "username": "jdoe"},
    "reviewer_type": "human",
    "created_at": "2026-04-03T11:00:00Z", "updated_at": "2026-04-03T11:00:00Z",
}

V2_NOTE = {
    "id": "note-1", "task": {"id": "tsk-abc", "title": "Add auth", "status": "doing"},
    "text": "All tests passing", "actor": {"type": "agent", "id": "agt-001", "name": "executor-1"},
    "created_at": "2026-04-03T11:30:00Z",
}

V2_LINK = {
    "id": "lnk-1",
    "source": {"type": "task", "id": "tsk-abc", "title": "Add auth", "status": "doing"},
    "target": {"type": "jira", "id": "PROJ-123", "label": "PROJ-123"},
    "link_type": "jira", "metadata": None,
    "created_by": {"type": "user", "id": "42", "username": "jdoe"},
    "created_at": "2026-04-03T09:00:00Z", "updated_at": "2026-04-03T09:00:00Z",
}

V2_EVENT = {
    "id": "evt-1", "task": {"id": "tsk-abc", "title": "Add auth", "status": "doing"},
    "event_type": "claimed", "data": {"agent_id": "agt-001"},
    "trigger_source": "claim", "actor": {"type": "agent", "id": "agt-001", "name": "executor-1"},
    "timestamp": "2026-04-03T10:00:00Z",
}


class TestTaskEntity:

    def test_task_model_validate(self):
        """DoD #1"""
        from vtf_sdk.entities import Task
        task = Task.model_validate(V2_TASK)
        assert task.id == "tsk-abc-123"
        assert task.title == "Add auth endpoint"

    def test_task_project_is_ref(self):
        """DoD #2"""
        from vtf_sdk.entities import Task
        from vtf_sdk.refs import ProjectRef
        task = Task.model_validate(V2_TASK)
        assert isinstance(task.project, ProjectRef)
        assert task.project.name == "Auth System"

    def test_task_claimed_by_actor_ref(self):
        """DoD #3"""
        from vtf_sdk.entities import Task
        from vtf_sdk.refs import AgentActor
        task = Task.model_validate(V2_TASK)
        assert isinstance(task.claimed_by, AgentActor)
        assert task.claimed_by.pod_name == "pod-abc"

    def test_task_null_fields(self):
        """DoD #4"""
        from vtf_sdk.entities import Task
        null_task = {**V2_TASK, "workplan": None, "milestone": None, "claimed_by": None}
        task = Task.model_validate(null_task)
        assert task.workplan is None
        assert task.milestone is None
        assert task.claimed_by is None

    def test_task_permissions(self):
        """DoD #5"""
        from vtf_sdk.entities import Task
        task = Task.model_validate(V2_TASK)
        assert task.permissions.available_actions == ["complete", "fail", "block"]

    def test_task_str(self):
        """DoD #6"""
        from vtf_sdk.entities import Task
        task = Task.model_validate(V2_TASK)
        assert str(task) == "Add auth endpoint"


class TestOtherEntities:

    def test_project_model_validate(self):
        """DoD #7"""
        from vtf_sdk.entities import Project
        from vtf_sdk.refs import UserActor
        proj = Project.model_validate(V2_PROJECT)
        assert isinstance(proj.owner, UserActor)
        assert proj.owner.username == "jdoe"

    def test_project_exposes_slug(self):
        """C.3 Slice 5: the K8s-safe Vault-path identity (vtaskforge#C.2)."""
        from vtf_sdk.entities import Project
        proj = Project.model_validate(V2_PROJECT)
        assert proj.slug == "auth-system"

    def test_project_slug_defaults_empty_when_absent(self):
        """Forward-compat: an older API response without slug parses fine."""
        from vtf_sdk.entities import Project
        payload = {k: v for k, v in V2_PROJECT.items() if k != "slug"}
        assert Project.model_validate(payload).slug == ""

    def test_workplan_model_validate(self):
        """DoD #8"""
        from vtf_sdk.entities import Workplan
        from vtf_sdk.refs import ProjectRef
        wp = Workplan.model_validate(V2_WORKPLAN)
        assert isinstance(wp.project, ProjectRef)

    def test_milestone_model_validate(self):
        """DoD #9"""
        from vtf_sdk.entities import Milestone
        from vtf_sdk.refs import WorkplanRef
        ms = Milestone.model_validate(V2_MILESTONE)
        assert isinstance(ms.workplan, WorkplanRef)

    def test_agent_model_validate(self):
        """DoD #10"""
        from vtf_sdk.entities import Agent
        from vtf_sdk.refs import TaskRef
        agent = Agent.model_validate(V2_AGENT)
        assert isinstance(agent.current_task, TaskRef)
        assert agent.current_task.title == "Add auth"

    def test_review_model_validate(self):
        """DoD #11"""
        from vtf_sdk.entities import Review
        rev = Review.model_validate(V2_REVIEW)
        assert rev.reviewer.username == "jdoe"
        assert rev.task.id == "tsk-abc"

    def test_note_model_validate(self):
        """DoD #12"""
        from vtf_sdk.entities import Note
        note = Note.model_validate(V2_NOTE)
        assert note.actor.name == "executor-1"

    def test_link_model_validate(self):
        """DoD #13"""
        from vtf_sdk.entities import Link
        link = Link.model_validate(V2_LINK)
        assert link.source.title == "Add auth"
        assert link.target.label == "PROJ-123"

    def test_task_event_model_validate(self):
        """DoD #14"""
        from vtf_sdk.entities import TaskEvent
        evt = TaskEvent.model_validate(V2_EVENT)
        assert evt.actor.name == "executor-1"
        assert evt.trigger_source == "claim"

    def test_paged_result(self):
        """DoD #15"""
        from vtf_sdk.entities import Task
        from vtf_sdk.pagination import PagedResult
        data = {"items": [V2_TASK], "has_more": False, "next_cursor": None, "previous_cursor": None}
        result = PagedResult[Task].model_validate(data)
        assert len(result.items) == 1
        assert isinstance(result.items[0], Task)

    def test_forward_compat(self):
        """DoD #16: Extra unknown fields are ignored."""
        from vtf_sdk.entities import Task
        extended = {**V2_TASK, "future_field": "v3 stuff", "another": 42}
        task = Task.model_validate(extended)
        assert task.id == "tsk-abc-123"
