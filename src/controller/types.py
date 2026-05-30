"""Data types for vafi controller.

These types define the interface between the controller and work sources.
They are shared across the controller, WorkSource protocol, and VtfWorkSource
implementation.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentInfo:
    """Information about a registered agent."""
    id: str
    token: str


@dataclass
class RepoInfo:
    """Repository information for task execution."""
    url: str          # git clone URL
    branch: str       # ref to clone / the WC-2 integrate target
    # WC-1.1 (vtaskforge#13): the project default branch. The clone
    # path falls back to this when `branch` (a milestone integration
    # branch) does not yet exist on origin — the DAG cold start, where
    # the root node's integration branch has not been created by its
    # own integrate() yet. Defaulted so existing RepoInfo(url=, branch=)
    # constructions don't regress; "" ⇒ no fallback (V16 single-task).
    fallback_branch: str = ""


@dataclass
class TaskInfo:
    """Task information from the work source."""
    id: str
    title: str
    spec: str         # YAML spec content
    project_id: str
    test_command: dict[str, Any]
    needs_review: bool
    assigned_to: str | None
    # vfobs observability dimension. Defaulted (NOT required) so the
    # existing TaskInfo(...) constructions in vafi tests don't
    # regress (verifier V16 / WG2 D-T0-1 lesson). vtaskforge has no
    # "workgraph" — its unit is the milestone; the vtf worksource
    # maps task.milestone.id here. Empty ⇒ emission hooks skip +
    # log once (degrade, never crash).
    workgraph_id: str = ""
    # WC-1/C2 server-derived base_ref (milestone integration branch, or
    # project default for single-task work). Defaulted so existing
    # TaskInfo(...) constructions don't regress (V16). Empty ⇒ fall back
    # to the project default branch (today's clone behaviour).
    base_ref: str = ""
    # C.3 declared variables: spec (vtaskforge Task.variables). Defaulted so
    # existing TaskInfo(...) constructions don't regress (V16). Empty ⇒ the
    # materializer no-ops and the spawn is byte-identical to today.
    variables: list[dict] = field(default_factory=list)


@dataclass
class ReworkContext:
    """Context for rework execution."""
    session_id: str | None     # from previous execution, for --resume
    judge_feedback: str        # latest review with changes_requested
    attempt_number: int        # how many times rejected so far


@dataclass
class GateResult:
    """Result of a verification gate."""
    name: str
    command: str
    exit_code: int
    stdout: str
    passed: bool


@dataclass
class ExecutionResult:
    """Result of task execution."""
    success: bool              # all gates passed
    session_id: str | None     # harness session ID for future rework
    completion_report: str     # harness result text (opaque to controller)
    cost_usd: float
    num_turns: int
    gate_results: list[GateResult]
    # vafi#39: set when the harness reported success=False but the gates
    # passed (delivery is real on origin). The controller surfaces this
    # as a task note before accepting the delivery, so adjudicators can
    # see the exit-code anomaly even though the work was accepted. None
    # for the ordinary harness-OK or honest-failure paths.
    harness_anomaly: str | None = None