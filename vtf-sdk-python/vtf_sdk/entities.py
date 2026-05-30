"""Typed entity models for the vtf v2 API.

All entities inherit from VtfModel (frozen, forward-compatible).
Construction from API response dicts uses model_validate().
"""
from datetime import datetime, timedelta

from .base import VtfModel
from .refs import (
    ActorRef,
    InternalLinkRef,
    LinkRef,
    MilestoneRef,
    ProjectRef,
    TaskRef,
    WorkplanRef,
)


# --- Permissions types ---

class TaskPermissions(VtfModel):
    can_edit: bool
    can_delete: bool
    available_actions: list[str]


class ProjectPermissions(VtfModel):
    can_edit: bool
    can_delete: bool
    can_archive: bool
    can_manage_members: bool


class WorkplanPermissions(VtfModel):
    can_edit: bool
    can_delete: bool
    can_archive: bool
    can_complete: bool


class MilestonePermissions(VtfModel):
    can_edit: bool
    can_delete: bool
    can_activate: bool
    can_complete: bool


# --- Entity types ---

class Task(VtfModel):
    id: str
    title: str
    description: str
    status: str
    project: ProjectRef
    workplan: WorkplanRef | None = None
    milestone: MilestoneRef | None = None
    labels: list[str] = []
    acceptance_criteria: list[str] = []
    needs_review_before_start: bool | None = None
    needs_review_on_completion: bool | None = None
    review_return_to: str | None = None
    requires: list[TaskRef] = []
    assigned_to: ActorRef | None = None
    claimed_by: ActorRef | None = None
    claimed_at: datetime | None = None
    claim_timeout: str | None = None  # ISO 8601 duration string
    claim_expires_at: datetime | None = None
    created_by: ActorRef | None = None
    spec: str = ""
    agent_model: str = ""
    variables: list[dict] = []   # declared variables: spec (C.3); materialized at spawn
    test_command: dict = {}
    judge: bool = False
    isolation: str = ""
    retry_count: int = 0
    execution_summary: dict | None = None
    permissions: TaskPermissions | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    # Expandable collections
    links: list["Link"] | None = None
    reviews: list["Review"] | None = None
    events: list["TaskEvent"] | None = None
    traces: list[dict] | None = None

    def __str__(self) -> str:
        return self.title


class Project(VtfModel):
    id: str
    name: str
    slug: str = ""          # K8s-safe immutable identity (vtaskforge C.2); Vault-path segment
    description: str = ""
    status: str = ""
    repo_url: str = ""
    default_branch: str = ""
    tags: list[str] = []
    owner: ActorRef | None = None
    created_by: ActorRef | None = None
    permissions: ProjectPermissions | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __str__(self) -> str:
        return self.name


class Workplan(VtfModel):
    id: str
    name: str
    description: str = ""
    status: str = ""
    project: ProjectRef | None = None
    owner: ActorRef | None = None
    tags: list[str] = []
    target_date: datetime | None = None
    default_needs_review_before_start: bool = False
    default_needs_review_on_completion: bool = False
    created_by: ActorRef | None = None
    permissions: WorkplanPermissions | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __str__(self) -> str:
        return self.name


class Milestone(VtfModel):
    id: str
    name: str
    description: str = ""
    status: str = ""
    order: int = 0
    workplan: WorkplanRef | None = None
    default_needs_review_before_start: bool | None = None
    default_needs_review_on_completion: bool | None = None
    created_by: ActorRef | None = None
    permissions: MilestonePermissions | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __str__(self) -> str:
        return self.name


class Agent(VtfModel):
    id: str
    name: str
    tags: list[str] = []
    status: str = ""
    effective_status: str = ""
    last_heartbeat: datetime | None = None
    pod_name: str | None = None
    registered_at: datetime | None = None
    current_task: TaskRef | None = None
    tasks_completed: int = 0
    tasks_failed: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __str__(self) -> str:
        return self.name


class Review(VtfModel):
    id: str
    task: TaskRef
    decision: str
    reason: str = ""
    reviewer: ActorRef | None = None
    reviewer_type: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Note(VtfModel):
    id: str
    task: TaskRef | None = None
    text: str
    actor: ActorRef | None = None
    created_at: datetime | None = None


class Link(VtfModel):
    id: str
    source: InternalLinkRef
    target: LinkRef
    link_type: str
    metadata: dict | None = None
    created_by: ActorRef | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TaskEvent(VtfModel):
    id: str
    task: TaskRef
    event_type: str
    data: dict = {}
    trigger_source: str = ""
    actor: ActorRef | None = None
    timestamp: datetime | None = None
