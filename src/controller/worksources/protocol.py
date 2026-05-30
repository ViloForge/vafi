"""WorkSource protocol definition.

This protocol defines the interface between the controller and work systems.
The controller depends only on this protocol, allowing different work sources
(vtf, queue-based systems, manual dispatch) to be swapped in without changing
controller logic.
"""

from typing import Protocol

from ..types import (
    AgentInfo,
    ExecutionResult,
    RepoInfo,
    ReworkContext,
    TaskInfo,
)


class WorkSource(Protocol):
    """Abstract interface to a work system."""

    async def register(self, name: str, tags: list[str]) -> AgentInfo:
        """Register an agent with the work source.

        Args:
            name: Agent name for identification
            tags: Agent tags for task matching

        Returns:
            Agent information including ID and auth token
        """
        ...

    async def poll(self, agent_id: str, tags: list[str]) -> TaskInfo | None:
        """Poll for available work.

        Returns the highest priority available task, or None if no work available.
        Priority order: rework assigned to this agent, then new claimable work.

        Args:
            agent_id: ID of the polling agent
            tags: Agent tags for task matching

        Returns:
            Next task to execute, or None if no work available
        """
        ...

    async def poll_reviews(self, agent_id: str) -> TaskInfo | None:
        """Poll for tasks pending completion review (judge work).

        Args:
            agent_id: ID of the polling agent

        Returns:
            Next task to review, or None if no reviews pending
        """
        ...

    async def claim(self, task_id: str, agent_id: str) -> TaskInfo:
        """Claim a task for execution.

        Args:
            task_id: Task ID to claim
            agent_id: ID of the claiming agent

        Returns:
            Updated task information

        Raises:
            VtfConflictError: If task is already claimed by another agent
            VtfValidationError: If agent doesn't have required tags
        """
        ...

    async def heartbeat(self, task_id: str) -> None:
        """Send heartbeat for a claimed task to extend claim timeout.

        Args:
            task_id: Task ID being executed
        """
        ...

    async def agent_heartbeat(self, agent_id: str) -> None:
        """Send agent-level heartbeat to signal the agent is alive.

        Separate from task heartbeat. Runs continuously regardless of whether
        a task is being executed.

        Args:
            agent_id: Agent ID sending the heartbeat
        """
        ...

    async def set_agent_offline(self, agent_id: str) -> None:
        """Mark agent as offline during graceful shutdown.

        Args:
            agent_id: Agent ID going offline
        """
        ...

    async def complete(self, task_id: str, result: ExecutionResult) -> None:
        """Mark a task as completed with execution results.

        Stores the completion report and execution metadata, then transitions
        the task to the appropriate completion state (done or pending review).

        Args:
            task_id: Task ID being completed
            result: Execution results including report and metadata
        """
        ...

    async def fail(self, task_id: str, reason: str) -> None:
        """Mark a task as failed.

        Args:
            task_id: Task ID being failed
            reason: Failure reason for human triage
        """
        ...

    async def get_repo_info(self, project_id: str) -> RepoInfo:
        """Get repository information for a project.

        Args:
            project_id: Project ID from task

        Returns:
            Repository URL and default branch for cloning
        """
        ...

    async def get_project_slug(self, project_id: str) -> str:
        """Resolve a project's K8s-safe slug (the C.3 variables Vault-path identity).

        The task's embedded project ref is {id, name} only, so this fetches the
        full project. Called lazily, only for tasks that declare variables.
        """
        ...

    async def get_task_repo_info(self, task: TaskInfo) -> RepoInfo:
        """WC-2/D1: per-task clone ref (server-derived base_ref).

        Args:
            task: The task (carries base_ref from WC-1/C2)

        Returns:
            Repository URL and the branch to clone (base_ref, or
            project default when base_ref is empty — V16).
        """
        ...

    async def list_integrations(self) -> list[TaskInfo]:
        """WC-2/D2: workgraph tasks in 'integrating' awaiting the
        deterministic post-approve merge."""
        ...

    async def report_integration_result(
        self, task_id: str, success: bool, detail: str = ""
    ) -> None:
        """WC-2/D2: report the integration outcome —
        success ⇒ done; conflict ⇒ needs_attention + note."""
        ...

    async def get_rework_context(self, task_id: str) -> ReworkContext:
        """Get context for rework execution.

        Args:
            task_id: Task ID being reworked

        Returns:
            Rework context including judge feedback and attempt count
        """
        ...

    async def count_rework_attempts(self, task_id: str) -> int:
        """Count the number of rework attempts for a task.

        Args:
            task_id: Task ID to check

        Returns:
            Number of times the task has been rejected for changes
        """
        ...

    async def get_task_context(self, task_id: str) -> dict:
        """Get full task data with reviews for context building.

        Args:
            task_id: Task ID

        Returns:
            Dict with 'task' (with reviews) and 'notes' keys
        """
        ...

    async def add_note(self, task_id: str, text: str, actor_id: str) -> None:
        """Add a note to a task.

        Args:
            task_id: Task ID
            text: Note content
            actor_id: ID of the actor adding the note
        """
        ...

    async def submit(self, task_id: str) -> None:
        """Submit a task from draft to todo status (supervisor operation).

        Args:
            task_id: Task ID to submit
        """
        ...

    async def list_submittable(self) -> list[TaskInfo]:
        """List tasks that can be submitted (supervisor operation).

        Returns tasks in draft status where all dependencies are completed.

        Returns:
            List of submittable tasks
        """
        ...

    async def submit_review(self, task_id: str, decision: str, reason: str, reviewer_id: str) -> None:
        """Submit a review for a completed task (judge operation).

        Args:
            task_id: Task ID being reviewed
            decision: Review decision ("approved" or "changes_requested")
            reason: Review reasoning/feedback
            reviewer_id: ID of the reviewer
        """
        ...