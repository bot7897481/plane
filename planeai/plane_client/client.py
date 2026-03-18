"""
PlaneAI — Plane REST API Client
Wraps every Plane endpoint PlaneAI needs.
All calls are async (httpx.AsyncClient).
"""

import structlog
import httpx
from typing import Any

from plane_client.models import (
    PlaneIssue,
    PlaneProject,
    PlaneCycle,
    PlaneComment,
    PlaneState,
    IssueUpdatePayload,
    CommentCreatePayload,
)

logger = structlog.get_logger(__name__)


class PlaneClient:
    """
    Async client for Plane's REST API.

    Usage:
        async with PlaneClient(base_url, api_token, workspace_slug) as client:
            issues = await client.get_ai_assigned_issues(project_id)
    """

    def __init__(self, base_url: str, api_token: str, workspace_slug: str):
        self.base_url = base_url.rstrip("/")
        self.workspace_slug = workspace_slug
        self._headers = {
            "X-API-Key": api_token,
            "Content-Type": "application/json",
        }
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._headers,
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()

    def _workspace_url(self, path: str) -> str:
        return f"/api/v1/workspaces/{self.workspace_slug}/{path}"

    async def _get(self, path: str, params: dict | None = None) -> Any:
        assert self._client, "Use async with PlaneClient(...) as client"
        resp = await self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    async def _post(self, path: str, data: dict) -> Any:
        assert self._client
        resp = await self._client.post(path, json=data)
        resp.raise_for_status()
        return resp.json()

    async def _patch(self, path: str, data: dict) -> Any:
        assert self._client
        resp = await self._client.patch(path, json=data)
        resp.raise_for_status()
        return resp.json()

    # ── Projects ─────────────────────────────────────────────────────────────

    async def list_projects(self) -> list[PlaneProject]:
        data = await self._get(self._workspace_url("projects/"))
        results = data.get("results", data) if isinstance(data, dict) else data
        return [PlaneProject(**p) for p in results]

    async def get_project(self, project_id: str) -> PlaneProject:
        data = await self._get(self._workspace_url(f"projects/{project_id}/"))
        return PlaneProject(**data)

    # ── States ────────────────────────────────────────────────────────────────

    async def list_states(self, project_id: str) -> list[PlaneState]:
        data = await self._get(self._workspace_url(f"projects/{project_id}/states/"))
        results = data.get("results", data) if isinstance(data, dict) else data
        return [PlaneState(**s) for s in results]

    async def get_state_by_group(
        self, project_id: str, group: str
    ) -> PlaneState | None:
        """Get the first state matching a group name (e.g. 'started', 'completed')."""
        states = await self.list_states(project_id)
        for state in states:
            if state.group == group:
                return state
        return None

    # ── Issues ────────────────────────────────────────────────────────────────

    async def list_issues(
        self,
        project_id: str,
        assignee_id: str | None = None,
        state_group: str | None = None,
        priority: str | None = None,
    ) -> list[PlaneIssue]:
        """List issues with optional filters."""
        params: dict = {}
        if assignee_id:
            params["assignees"] = assignee_id
        if state_group:
            params["state__group"] = state_group
        if priority:
            params["priority"] = priority

        data = await self._get(
            self._workspace_url(f"projects/{project_id}/issues/"),
            params=params,
        )
        results = data.get("results", data) if isinstance(data, dict) else data
        return [PlaneIssue(**i) for i in results]

    async def get_ai_assigned_issues(
        self, project_id: str, agent_user_id: str
    ) -> list[PlaneIssue]:
        """
        Get all unstarted issues assigned to the AI agent user,
        ordered by priority (urgent → high → medium → low → none).
        """
        issues = await self.list_issues(
            project_id=project_id,
            assignee_id=agent_user_id,
            state_group="unstarted",
        )
        # Also check backlog group
        backlog_issues = await self.list_issues(
            project_id=project_id,
            assignee_id=agent_user_id,
            state_group="backlog",
        )
        all_issues = issues + backlog_issues

        priority_order = {"urgent": 0, "high": 1, "medium": 2, "low": 3, "none": 4}
        all_issues.sort(key=lambda i: priority_order.get(i.priority, 99))
        return all_issues

    async def get_issue(self, project_id: str, issue_id: str) -> PlaneIssue:
        data = await self._get(
            self._workspace_url(f"projects/{project_id}/issues/{issue_id}/")
        )
        return PlaneIssue(**data)

    async def update_issue(
        self, project_id: str, issue_id: str, payload: IssueUpdatePayload
    ) -> PlaneIssue:
        data = await self._patch(
            self._workspace_url(f"projects/{project_id}/issues/{issue_id}/"),
            data=payload.model_dump(exclude_none=True),
        )
        return PlaneIssue(**data)

    async def mark_issue_in_progress(
        self, project_id: str, issue_id: str
    ) -> PlaneIssue:
        state = await self.get_state_by_group(project_id, "started")
        if not state:
            logger.warning("No 'started' state found", project_id=project_id)
            return await self.get_issue(project_id, issue_id)
        return await self.update_issue(
            project_id, issue_id, IssueUpdatePayload(state=state.id)
        )

    async def mark_issue_done(
        self, project_id: str, issue_id: str
    ) -> PlaneIssue:
        state = await self.get_state_by_group(project_id, "completed")
        if not state:
            logger.warning("No 'completed' state found", project_id=project_id)
            return await self.get_issue(project_id, issue_id)
        return await self.update_issue(
            project_id, issue_id, IssueUpdatePayload(state=state.id)
        )

    # ── Comments ──────────────────────────────────────────────────────────────

    async def add_comment(
        self, project_id: str, issue_id: str, comment_html: str
    ) -> PlaneComment:
        """Add a comment to an issue (used for implementation notes + bug reports)."""
        data = await self._post(
            self._workspace_url(f"projects/{project_id}/issues/{issue_id}/comments/"),
            data=CommentCreatePayload(comment_html=comment_html).model_dump(),
        )
        return PlaneComment(**data)

    # ── Create Issues (Bug Reporting) ─────────────────────────────────────────

    async def create_bug_issue(
        self,
        project_id: str,
        title: str,
        description_html: str,
        parent_issue_id: str | None = None,
        priority: str = "high",
    ) -> PlaneIssue:
        """
        Create a new Bug issue in Plane.
        Called when tests fail after implementation.
        """
        # Get the 'unstarted' state to create issue in
        state = await self.get_state_by_group(project_id, "unstarted")

        payload: dict = {
            "name": title,
            "description_html": description_html,
            "priority": priority,
        }
        if state:
            payload["state"] = state.id
        if parent_issue_id:
            payload["parent"] = parent_issue_id

        data = await self._post(
            self._workspace_url(f"projects/{project_id}/issues/"),
            data=payload,
        )
        logger.info("Created bug issue", issue_id=data.get("id"), title=title)
        return PlaneIssue(**data)

    # ── Cycles (Sprints) ──────────────────────────────────────────────────────

    async def list_cycles(self, project_id: str) -> list[PlaneCycle]:
        data = await self._get(self._workspace_url(f"projects/{project_id}/cycles/"))
        results = data.get("results", data) if isinstance(data, dict) else data
        return [PlaneCycle(**c) for c in results]

    async def get_active_cycle(self, project_id: str) -> PlaneCycle | None:
        cycles = await self.list_cycles(project_id)
        for cycle in cycles:
            if cycle.status == "current":
                return cycle
        return None

    async def get_cycle_issues(
        self, project_id: str, cycle_id: str
    ) -> list[PlaneIssue]:
        data = await self._get(
            self._workspace_url(
                f"projects/{project_id}/cycles/{cycle_id}/cycle-issues/"
            )
        )
        results = data.get("results", data) if isinstance(data, dict) else data
        issues = []
        for item in results:
            issue_data = item.get("issue_detail", item)
            if issue_data:
                issues.append(PlaneIssue(**issue_data))
        return issues
