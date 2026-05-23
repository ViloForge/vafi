"""VtfWorkSource implementation.

This module implements the WorkSource protocol using the vtf Python SDK.
It wraps AsyncVtfClient and contains vtf-specific logic like priority ordering
(rework before new work), review parsing, and session ID extraction.
"""
import logging
from typing import Any

from vtf_sdk.async_client import AsyncVtfClient
from vtf_sdk.entities import Task as SdkTask

logger = logging.getLogger(__name__)

from ..types import (
    AgentInfo,
    ExecutionResult,
    RepoInfo,
    ReworkContext,
    TaskInfo,
)


class VtfWorkSource:
    """WorkSource implementation backed by the vtf Python SDK."""

    def __init__(
        self,
        client: AsyncVtfClient,
        tags: list[str] | None = None,
        pod_name: str | None = None,
    ):
        self._client = client
        self._default_tags = tags or []
        self._pod_name = pod_name

    async def register(self, name: str, tags: list[str]) -> AgentInfo:
        """Register an agent with vtf."""
        agent, raw = await self._client.agents.register(
            name=name, tags=tags, pod_name=self._pod_name,
        )
        # Recreate the client with the returned token
        token = raw.get("token", "")
        self._client._transport._client.headers["authorization"] = f"Token {token}"
        return AgentInfo(id=agent.id, token=token)

    async def poll(self, agent_id: str, tags: list[str]) -> TaskInfo | None:
        """Poll for available work with priority ordering."""
        # Priority 1: Rework tasks (changes_requested)
        rework_result = await self._client.tasks.list(status="changes_requested")
        if rework_result.items:
            return self._sdk_task_to_info(rework_result.items[0])

        # Priority 2: Claimable work
        claimable_result = await self._client.tasks.claimable(tags=tags)
        if claimable_result.items:
            return self._sdk_task_to_info(claimable_result.items[0])

        return None

    async def poll_reviews(self, agent_id: str) -> TaskInfo | None:
        """Poll for tasks pending completion review (judge work).

        Uses the dedicated /v2/reviews/pending/ endpoint (vtaskforge#6) so
        the per-agent token sees fleet-wide review work even though the
        agent's user is not a member of every project the tasks live in.
        Generic tasks.list(status='pending_completion_review') applies
        scope_queryset_to_user_projects on the server and silently returns
        empty for non-member agents — that path is what bit production.
        """
        review_result = await self._client.reviews.pending()
        if review_result.items:
            return self._sdk_task_to_info(review_result.items[0])
        return None

    async def claim(self, task_id: str, agent_id: str) -> TaskInfo:
        """Claim a task for execution."""
        task = await self._client.tasks.claim(task_id, agent_id=agent_id)
        return self._sdk_task_to_info(task)

    async def heartbeat(self, task_id: str) -> None:
        """Send heartbeat for a claimed task."""
        await self._client.tasks.heartbeat(task_id)

    async def agent_heartbeat(self, agent_id: str) -> None:
        """Send agent-level heartbeat."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        payload: dict[str, Any] = {"last_heartbeat": now}
        if self._pod_name is not None:
            payload["pod_name"] = self._pod_name
        await self._client.agents.update(agent_id, **payload)

    async def set_agent_offline(self, agent_id: str) -> None:
        """Mark agent as offline during graceful shutdown."""
        await self._client.agents.update_status(agent_id, status="offline")

    async def complete(self, task_id: str, result: ExecutionResult) -> None:
        """Mark a task as completed with execution results.

        Note-post failures are best-effort and MUST NOT abort the terminal
        tasks.complete() — a gates-passed delivery must reach
        pending_completion_review even if audit notes can't be posted.
        Without this, a blank completion_report (claude max_turns) → vtf 400
        on add_note → exception bubbled → controller treated the whole
        completion as fatal and routed to needs_attention. kb gotcha
        XsPemtnm. D1 (invoker.py) ensures completion_report is never blank
        at the source; this method defends in depth.
        """
        report_text = result.completion_report or "Task completed"
        await self._safe_add_note(task_id, report_text)

        if result.session_id:
            await self._safe_add_note(
                task_id, f"vafi:session_id={result.session_id}",
            )

        metadata_text = (
            f"vafi:execution_metadata\n"
            f"cost_usd: {result.cost_usd}\n"
            f"num_turns: {result.num_turns}\n"
            f"gates: {len(result.gate_results)} executed"
        )
        await self._safe_add_note(task_id, metadata_text)
        await self._client.tasks.complete(task_id)

    async def _safe_add_note(self, task_id: str, text: str) -> None:
        """Best-effort note post — log + swallow exceptions so the caller's
        terminal action (e.g. tasks.complete) is not aborted by an audit-only
        side-channel failure. See `complete` for the rationale."""
        try:
            await self._client.tasks.add_note(task_id, text=text)
        except Exception as e:
            logger.warning(
                "add_note failed for task %s (continuing): %s", task_id, e,
            )

    async def fail(self, task_id: str, reason: str) -> None:
        """Mark a task as failed."""
        await self._client.tasks.add_note(task_id, text=f"Task failed: {reason}")
        await self._client.tasks.fail(task_id)

    async def get_task_context(self, task_id: str) -> dict:
        """Get full task data with reviews and notes."""
        task = await self._client.tasks.get(task_id, expand=["reviews"])
        try:
            notes_result = await self._client.tasks.list_notes(task_id)
            notes = [{"text": n.text, "actor": str(n.actor) if n.actor else ""} for n in notes_result.items]
        except Exception:
            notes = []
        return {"task": task.model_dump(mode="json"), "notes": notes}

    async def add_note(self, task_id: str, text: str, actor_id: str) -> None:
        """Add a note to a task."""
        await self._client.tasks.add_note(task_id, text=text)

    async def get_repo_info(self, project_id: str) -> RepoInfo:
        """Get repository information for a project (project-default
        branch). Retained for the single-task / no-base_ref path."""
        project = await self._client.projects.get(project_id)
        default = project.default_branch or "main"
        return RepoInfo(url=project.repo_url, branch=default,
                        fallback_branch=default)

    async def get_task_repo_info(self, task: TaskInfo) -> RepoInfo:
        """WC-2/D1 — per-task clone ref. The controller clones the
        server-derived `base_ref` (WC-1/C2: the milestone integration
        branch for a workgraph task) and never re-derives the rule.
        Empty base_ref ⇒ project default (V16 byte-identical).

        WC-1.1 (vtaskforge#13): `branch` stays the server-derived
        base_ref — it is BOTH the clone ref AND the WC-2 integrate
        target (`_poll_and_integrate` passes it to `integrate()`; for
        the root node that target MUST be the integration branch so the
        root's approval *creates* it). `fallback_branch` carries the
        project default so the clone path — and only the clone path —
        can fall back at DAG cold start, when the integration branch
        does not exist on origin yet."""
        project = await self._client.projects.get(task.project_id)
        default = project.default_branch or "main"
        branch = task.base_ref or default
        return RepoInfo(url=project.repo_url, branch=branch,
                        fallback_branch=default)

    async def list_integrations(self) -> list[TaskInfo]:
        """WC-2/D2 — workgraph tasks awaiting post-approve integration.
        WC-1 routes an approved workgraph task to `integrating` (the
        milestone merge slot is held SoR-side); the controller services
        them like any other state in its poll loop."""
        result = await self._client.tasks.list(status="integrating")
        return [self._sdk_task_to_info(t) for t in result.items]

    async def report_integration_result(
        self, task_id: str, success: bool, detail: str = ""
    ) -> None:
        """WC-2/D2 — report the merge outcome. success ⇒
        integrating→done; conflict/push-failure ⇒
        integrating→needs_attention (+ a note carrying `detail`).
        Idempotent SoR-side (re-entrant with the WC-1/C4 reaper)."""
        await self._client.tasks.integration_result(
            task_id, success=success, detail=detail,
        )

    async def get_rework_context(self, task_id: str) -> ReworkContext:
        """Get context for rework execution."""
        task = await self._client.tasks.get(task_id, expand=["reviews"])

        judge_feedback = ""
        if task.reviews:
            for review in reversed(task.reviews):
                if review.decision == "changes_requested":
                    judge_feedback = review.reason or "No feedback provided"
                    break

        session_id = None
        try:
            notes_result = await self._client.tasks.list_notes(task_id)
            for note in notes_result.items:
                if note.text.startswith("vafi:session_id="):
                    session_id = note.text.split("=", 1)[1].strip()
                    break
        except Exception:
            pass

        attempt_number = await self.count_rework_attempts(task_id)
        return ReworkContext(
            session_id=session_id,
            judge_feedback=judge_feedback,
            attempt_number=attempt_number,
        )

    async def count_rework_attempts(self, task_id: str) -> int:
        """Count the number of rework attempts for a task."""
        task = await self._client.tasks.get(task_id, expand=["reviews"])
        if not task.reviews:
            return 0
        return sum(1 for r in task.reviews if r.decision == "changes_requested")

    async def submit(self, task_id: str) -> None:
        """Submit a task from draft to todo status."""
        await self._client.tasks.submit(task_id)

    async def list_submittable(self) -> list[TaskInfo]:
        """List tasks that can be submitted (draft with completed deps)."""
        draft_result = await self._client.tasks.list(status="draft")
        submittable = []
        for task in draft_result.items:
            if self._are_dependencies_completed(task):
                submittable.append(self._sdk_task_to_info(task))
        return submittable

    async def submit_review(self, task_id: str, decision: str, reason: str, reviewer_id: str) -> None:
        """Submit a review for a completed task."""
        await self._client.tasks.submit_review(
            task_id, decision=decision, reason=reason, reviewer_type="agent",
        )

    def _are_dependencies_completed(self, task: SdkTask) -> bool:
        """Check if all task dependencies are completed."""
        if not task.requires:
            return True
        return all(dep.status == "done" for dep in task.requires)

    def _sdk_task_to_info(self, task: SdkTask) -> TaskInfo:
        """Convert SDK Task entity to internal TaskInfo."""
        project_id = task.project.id if task.project else ""
        # vfobs "workgraph" == vtaskforge milestone (verified vs the
        # vtf SDK Task entity — there is no workgraph in vtaskforge;
        # the WG vtf-imports each create one milestone per workgraph).
        # F9 (High): a milestone-less task is a legitimate run (one-off
        # spike, ad-hoc bugfix) but had workgraph_id="" ⇒ safe_emit
        # dropped every event ⇒ invisible. Synthesize a stable,
        # namespaced, self-describing id ("task-<id>") so the run is
        # observable. The "task-" prefix cannot collide with a vtaskforge
        # milestone nanoid and tells a retro reader "standalone, not a
        # milestone DAG". vfobs treats it as an opaque workgraph_id.
        workgraph_id = task.milestone.id if task.milestone else f"task-{task.id}"
        return TaskInfo(
            id=task.id,
            title=task.title,
            spec=task.spec,
            project_id=project_id,
            test_command=task.test_command,
            needs_review=task.needs_review_on_completion or False,
            assigned_to=str(task.assigned_to) if task.assigned_to else None,
            workgraph_id=workgraph_id,
            base_ref=getattr(task, "base_ref", "") or "",
        )
