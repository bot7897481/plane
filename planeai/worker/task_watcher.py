"""
PlaneAI — Task Watcher
Polls every Plane project for issues assigned to the AI Agent,
then hands them to the AgentOrchestrator to implement.

Runs as an arq background worker (same pattern as Parseek's worker.py).
"""

import asyncio
import json
import structlog
from pathlib import Path
from typing import Any

import redis.asyncio as aioredis

from config import get_settings
from plane_client import PlaneClient
from plane_client.models import PlaneIssue
from agent.orchestrator import AgentOrchestrator

logger = structlog.get_logger(__name__)
settings = get_settings()

# ─────────────────────────────────────────────────────────────────────────────
# Project Config Loader
# ─────────────────────────────────────────────────────────────────────────────

def load_project_configs() -> dict[str, dict]:
    """
    Load project configurations from environment variable + any .planeai.yml files.

    Returns a dict keyed by Plane project name (lowercase):
    {
      "parseek": {
        "name": "Parseek",
        "local_path": "/Users/you/parseek",
        "tech_stack": "Python/FastAPI + React 19 + PostgreSQL",
        "test_command": ["pytest", "-v", "--tb=short"],
        "test_path": "tests/",
      },
      "vazier": { ... }
    }
    """
    import yaml

    path_map = settings.get_project_path_map()
    configs: dict[str, dict] = {}

    for name, local_path in path_map.items():
        project_path = Path(local_path)
        config = {
            "name": name.title(),
            "local_path": local_path,
            "tech_stack": "Python/FastAPI",
            "test_command": ["pytest", "-v", "--tb=short"],
            "test_path": "",
        }

        # Load .planeai.yml from the project if present
        planeai_yml = project_path / ".planeai.yml"
        if planeai_yml.exists():
            try:
                with open(planeai_yml) as f:
                    overrides = yaml.safe_load(f) or {}
                config.update(overrides)
                logger.info("Loaded .planeai.yml", project=name, path=str(planeai_yml))
            except Exception as e:
                logger.warning("Failed to load .planeai.yml", project=name, error=str(e))

        configs[name.lower()] = config

    return configs


def match_project_config(
    plane_project_name: str, configs: dict[str, dict]
) -> dict | None:
    """Match a Plane project name to a local project config (fuzzy)."""
    name_lower = plane_project_name.lower().strip()
    # Exact match
    if name_lower in configs:
        return configs[name_lower]
    # Partial match
    for key, config in configs.items():
        if key in name_lower or name_lower in key:
            return config
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Run Tracker (prevents double-processing)
# ─────────────────────────────────────────────────────────────────────────────

class RunTracker:
    """
    Uses Redis to track which issues are currently being worked on.
    Prevents the watcher from picking up the same issue twice.
    """

    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self._redis: aioredis.Redis | None = None

    async def connect(self):
        self._redis = await aioredis.from_url(self.redis_url, decode_responses=True)

    async def close(self):
        if self._redis:
            await self._redis.aclose()

    def _key(self, issue_id: str) -> str:
        return f"planeai:running:{issue_id}"

    async def is_running(self, issue_id: str) -> bool:
        return bool(await self._redis.exists(self._key(issue_id)))

    async def mark_running(self, issue_id: str, ttl: int = 3600):
        """Lock issue for up to 1 hour (in case agent crashes)."""
        await self._redis.setex(self._key(issue_id), ttl, "1")

    async def mark_done(self, issue_id: str):
        await self._redis.delete(self._key(issue_id))

    async def publish_progress(self, issue_id: str, message: str):
        await self._redis.publish(f"planeai:progress:{issue_id}", message)


# ─────────────────────────────────────────────────────────────────────────────
# Task Watcher
# ─────────────────────────────────────────────────────────────────────────────

class TaskWatcher:
    """
    The main polling loop.

    Every PLANEAI_POLL_INTERVAL seconds:
    1. List all Plane projects
    2. For each project, get issues assigned to the AI agent user
    3. For each unprocessed issue, spin up an AgentOrchestrator run
    """

    def __init__(self):
        self.running = False
        self.tracker = RunTracker(settings.redis_url)
        self._active_runs: dict[str, asyncio.Task] = {}

    async def start(self):
        self.running = True
        await self.tracker.connect()
        logger.info(
            "TaskWatcher started",
            poll_interval=settings.planeai_poll_interval,
            workspace=settings.plane_workspace_slug,
        )

        while self.running:
            try:
                await self._poll_once()
            except Exception as e:
                logger.error("Poll error", error=str(e))

            await asyncio.sleep(settings.planeai_poll_interval)

    async def stop(self):
        self.running = False
        await self.tracker.close()
        # Wait for active runs to finish
        if self._active_runs:
            logger.info("Waiting for active runs to finish", count=len(self._active_runs))
            await asyncio.gather(*self._active_runs.values(), return_exceptions=True)

    async def _poll_once(self):
        project_configs = load_project_configs()

        async with PlaneClient(
            base_url=settings.plane_base_url,
            api_token=settings.plane_api_token,
            workspace_slug=settings.plane_workspace_slug,
        ) as client:
            try:
                projects = await client.list_projects()
            except Exception as e:
                logger.error("Failed to list Plane projects", error=str(e))
                return

            for project in projects:
                await self._check_project(client, project, project_configs)

    async def _check_project(self, client: PlaneClient, project: Any, configs: dict):
        project_config = match_project_config(project.name, configs)
        if not project_config:
            logger.debug("No local config for project", project=project.name)
            return

        try:
            issues = await client.get_ai_assigned_issues(
                project_id=project.id,
                agent_user_id=settings.planeai_agent_user_id,
            )
        except Exception as e:
            logger.error("Failed to get issues", project=project.name, error=str(e))
            return

        if not issues:
            return

        logger.info(
            "Found tasks for AI agent",
            project=project.name,
            count=len(issues),
        )

        for issue in issues:
            issue.project_name = project.name
            issue.project_local_path = project_config.get("local_path")

            if await self.tracker.is_running(issue.id):
                logger.debug("Issue already in progress", issue_id=issue.id)
                continue

            # Launch agent run as background task
            task = asyncio.create_task(
                self._run_agent(client, issue, project_config),
                name=f"agent-{issue.id}",
            )
            self._active_runs[issue.id] = task
            task.add_done_callback(lambda t, iid=issue.id: self._active_runs.pop(iid, None))

            # Only process one issue per project per poll to avoid overload
            break

    async def _run_agent(
        self,
        client: PlaneClient,
        issue: PlaneIssue,
        project_config: dict,
    ):
        """Run the agent for a single issue."""
        await self.tracker.mark_running(issue.id)

        try:
            # Mark in-progress in Plane
            await client.mark_issue_in_progress(issue.project, issue.id)
            logger.info("Starting agent run", issue=issue.display_id, title=issue.name)

            # Build progress callback (publishes to Redis for dashboard)
            async def progress(msg: str):
                await self.tracker.publish_progress(issue.id, msg)
                logger.info("Agent progress", issue=issue.display_id, msg=msg)

            orchestrator = AgentOrchestrator(plane_client=client)
            run = await orchestrator.run(
                issue=issue,
                project_config=project_config,
                progress_callback=lambda m: asyncio.create_task(progress(m)),
            )

            logger.info(
                "Agent run complete",
                issue=issue.display_id,
                status=run.status,
                files_changed=run.files_changed,
            )

        except Exception as e:
            logger.error("Agent run failed", issue_id=issue.id, error=str(e))
            try:
                await client.add_comment(
                    issue.project,
                    issue.id,
                    f"<p>❌ <strong>PlaneAI Error:</strong> {e}</p>",
                )
            except Exception:
                pass
        finally:
            await self.tracker.mark_done(issue.id)
