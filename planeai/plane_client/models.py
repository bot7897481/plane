"""
PlaneAI — Plane API Data Models
Pydantic models matching Plane's REST API response shapes.
"""

from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field


class PlaneUser(BaseModel):
    id: str
    email: str
    display_name: str
    avatar: str | None = None


class PlaneState(BaseModel):
    id: str
    name: str
    color: str
    group: str  # "backlog" | "unstarted" | "started" | "completed" | "cancelled"


class PlanePriority:
    URGENT = "urgent"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


class PlaneLabel(BaseModel):
    id: str
    name: str
    color: str


class PlaneIssue(BaseModel):
    """A single issue (task, bug, story, epic) in Plane."""
    id: str
    sequence_id: int
    name: str
    description_html: str | None = None
    description_stripped: str | None = None
    priority: str = "none"
    state: str  # state ID
    state_detail: PlaneState | None = None
    assignees: list[str] = []  # user IDs
    label_ids: list[str] = []
    estimate_point: int | None = None
    start_date: str | None = None
    target_date: str | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    created_by: str
    project: str  # project ID
    parent: str | None = None
    # Extra metadata PlaneAI adds during processing
    project_name: str | None = None
    project_local_path: str | None = None

    @property
    def display_id(self) -> str:
        return f"#{self.sequence_id}"

    @property
    def is_assigned_to_ai(self) -> bool:
        """Check populated by PlaneClient after comparing with agent user id."""
        return getattr(self, "_is_ai_assigned", False)

    def short_description(self) -> str:
        """Clean text description for Claude's context."""
        if self.description_stripped:
            return self.description_stripped[:2000]
        return self.name


class PlaneComment(BaseModel):
    id: str
    comment_html: str
    comment_stripped: str | None = None
    actor: str  # user ID
    created_at: datetime


class PlaneProject(BaseModel):
    id: str
    name: str
    identifier: str  # e.g. "VSR", "PSK"
    description: str | None = None
    network: int = 0
    created_at: datetime
    updated_at: datetime


class PlaneCycle(BaseModel):
    """A sprint / cycle in Plane."""
    id: str
    name: str
    status: str  # "current" | "upcoming" | "completed" | "draft"
    start_date: str | None = None
    end_date: str | None = None
    project: str


class IssueUpdatePayload(BaseModel):
    """Payload for PATCH /issues/{id}/"""
    state: str | None = None
    priority: str | None = None
    assignees: list[str] | None = None


class CommentCreatePayload(BaseModel):
    """Payload for POST /issues/{id}/comments/"""
    comment_html: str
