"""
PlaneAI — FastAPI Application
Serves the dashboard API and starts the task watcher on startup.
"""

import asyncio
import structlog
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from config import get_settings
from plane_client import PlaneClient
from worker.task_watcher import TaskWatcher, load_project_configs

logger = structlog.get_logger(__name__)
settings = get_settings()

# ─────────────────────────────────────────────────────────────────────────────
# App Lifecycle
# ─────────────────────────────────────────────────────────────────────────────

watcher = TaskWatcher()
watcher_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global watcher_task
    logger.info("PlaneAI starting", workspace=settings.plane_workspace_slug)
    watcher_task = asyncio.create_task(watcher.start(), name="task-watcher")
    yield
    logger.info("PlaneAI shutting down")
    await watcher.stop()
    if watcher_task:
        watcher_task.cancel()


app = FastAPI(
    title="PlaneAI Agent",
    description="AI agent that reads Plane tasks, implements code, and marks issues complete.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────────────────────────────
# Dashboard Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the PlaneAI monitoring dashboard."""
    return HTMLResponse(content=DASHBOARD_HTML)


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "workspace": settings.plane_workspace_slug,
        "plane_url": settings.plane_base_url,
        "active_runs": list(watcher._active_runs.keys()),
    }


@app.get("/api/projects")
async def list_projects():
    """List all Plane projects and their local config status."""
    configs = load_project_configs()
    async with PlaneClient(
        base_url=settings.plane_base_url,
        api_token=settings.plane_api_token,
        workspace_slug=settings.plane_workspace_slug,
    ) as client:
        try:
            projects = await client.list_projects()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Plane API error: {e}")

    result = []
    for p in projects:
        config_key = p.name.lower()
        has_config = any(
            k in config_key or config_key in k for k in configs
        )
        result.append({
            "id": p.id,
            "name": p.name,
            "identifier": p.identifier,
            "has_local_config": has_config,
        })
    return result


@app.get("/api/projects/{project_id}/pending-tasks")
async def get_pending_tasks(project_id: str):
    """List tasks currently assigned to the AI agent for a project."""
    async with PlaneClient(
        base_url=settings.plane_base_url,
        api_token=settings.plane_api_token,
        workspace_slug=settings.plane_workspace_slug,
    ) as client:
        try:
            issues = await client.get_ai_assigned_issues(
                project_id=project_id,
                agent_user_id=settings.planeai_agent_user_id,
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Plane API error: {e}")

    return [
        {
            "id": i.id,
            "sequence_id": i.sequence_id,
            "title": i.name,
            "priority": i.priority,
            "is_running": i.id in watcher._active_runs,
        }
        for i in issues
    ]


@app.get("/api/status")
async def get_status():
    """Overall PlaneAI status — active runs, configs, poll interval."""
    configs = load_project_configs()
    return {
        "active_runs": [
            {"issue_id": iid, "task_name": t.get_name()}
            for iid, t in watcher._active_runs.items()
        ],
        "configured_projects": list(configs.keys()),
        "poll_interval_seconds": settings.planeai_poll_interval,
        "agent_model": settings.anthropic_implementation_model,
    }


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket — Live progress stream
# ─────────────────────────────────────────────────────────────────────────────

@app.websocket("/ws/progress/{issue_id}")
async def progress_stream(websocket: WebSocket, issue_id: str):
    """
    Stream real-time agent progress for a specific issue.
    Subscribes to Redis pub/sub channel planeai:progress:{issue_id}.
    """
    await websocket.accept()
    redis = await aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = redis.pubsub()
    await pubsub.subscribe(f"planeai:progress:{issue_id}")

    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                await websocket.send_text(message["data"])
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe()
        await redis.aclose()


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard HTML (single-file, no build step needed)
# ─────────────────────────────────────────────────────────────────────────────

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>PlaneAI Dashboard</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family: 'Segoe UI', sans-serif; background:#0d0d14; color:#e2e2f0; min-height:100vh; }
  .header { background:#111118; border-bottom:1px solid #1e1e2e; padding:16px 32px; display:flex; align-items:center; gap:12px; }
  .header h1 { font-size:20px; font-weight:700; color:#fff; }
  .badge { background:#6c63ff22; color:#6c63ff; border:1px solid #6c63ff44; font-size:11px; padding:3px 10px; border-radius:20px; font-weight:600; }
  .main { padding:32px; max-width:1200px; margin:0 auto; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(280px,1fr)); gap:20px; margin-bottom:32px; }
  .card { background:#111118; border:1px solid #1e1e2e; border-radius:12px; padding:24px; }
  .card h2 { font-size:13px; color:#888; text-transform:uppercase; letter-spacing:1px; margin-bottom:16px; }
  .stat { font-size:36px; font-weight:800; color:#fff; }
  .stat-sub { font-size:13px; color:#666; margin-top:4px; }
  .green { color:#43d9ad; }
  .purple { color:#6c63ff; }
  .orange { color:#d97706; }
  .log { background:#0a0a0f; border:1px solid #1e1e2e; border-radius:8px; padding:16px; font-family:monospace; font-size:12px; height:300px; overflow-y:auto; }
  .log-entry { margin-bottom:6px; color:#aaa; }
  .log-entry .time { color:#555; margin-right:8px; }
  .log-entry.info { color:#43d9ad; }
  .log-entry.tool { color:#6c63ff; }
  .log-entry.done { color:#f7c948; }
  .task-list { display:flex; flex-direction:column; gap:10px; }
  .task-item { background:#16161f; border:1px solid #1e1e2e; border-radius:8px; padding:12px 16px; display:flex; align-items:center; gap:12px; }
  .task-item .priority { font-size:10px; font-weight:700; padding:2px 8px; border-radius:4px; }
  .p-urgent { background:#ff6b6b22; color:#ff6b6b; }
  .p-high { background:#d9770622; color:#d97706; }
  .p-medium { background:#6c63ff22; color:#6c63ff; }
  .p-low { background:#43d9ad22; color:#43d9ad; }
  .task-item .running { margin-left:auto; font-size:11px; color:#43d9ad; }
  .pulse { animation: pulse 1.5s infinite; }
  @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.4; } }
  button { background:#6c63ff; color:#fff; border:none; padding:8px 16px; border-radius:6px; font-size:13px; cursor:pointer; }
  button:hover { background:#5b52ee; }
  select { background:#16161f; color:#e2e2f0; border:1px solid #1e1e2e; padding:6px 10px; border-radius:6px; font-size:13px; }
</style>
</head>
<body>
<div class="header">
  <h1>🤖 PlaneAI</h1>
  <span class="badge">Agent Dashboard</span>
  <span id="status-dot" style="margin-left:auto; font-size:12px; color:#43d9ad">● Connected</span>
</div>

<div class="main">
  <div class="grid">
    <div class="card">
      <h2>Active Runs</h2>
      <div class="stat green" id="active-count">0</div>
      <div class="stat-sub">Issues being implemented now</div>
    </div>
    <div class="card">
      <h2>Configured Projects</h2>
      <div class="stat purple" id="project-count">—</div>
      <div class="stat-sub" id="project-names">Loading...</div>
    </div>
    <div class="card">
      <h2>Poll Interval</h2>
      <div class="stat orange" id="poll-interval">—</div>
      <div class="stat-sub">seconds between Plane API polls</div>
    </div>
  </div>

  <div class="grid">
    <div class="card" style="grid-column: 1 / -1">
      <h2>Pending Tasks (assigned to AI Agent)
        <select id="project-select" style="margin-left:12px" onchange="loadTasks()">
          <option value="">Select project...</option>
        </select>
        <button onclick="loadTasks()" style="margin-left:8px">Refresh</button>
      </h2>
      <div class="task-list" id="task-list">
        <p style="color:#555; font-size:13px">Select a project to view pending tasks</p>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>Live Agent Log
      <span id="log-issue-label" style="margin-left:8px; color:#6c63ff; font-size:11px"></span>
    </h2>
    <div class="log" id="log"></div>
  </div>
</div>

<script>
  let currentWs = null;

  async function loadStatus() {
    const r = await fetch('/api/status');
    const data = await r.json();
    document.getElementById('active-count').textContent = data.active_runs.length;
    document.getElementById('project-count').textContent = data.configured_projects.length;
    document.getElementById('project-names').textContent = data.configured_projects.join(', ') || 'None configured';
    document.getElementById('poll-interval').textContent = data.poll_interval_seconds;
  }

  async function loadProjects() {
    const r = await fetch('/api/projects');
    const projects = await r.json();
    const sel = document.getElementById('project-select');
    sel.innerHTML = '<option value="">Select project...</option>';
    for (const p of projects) {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = p.name + (p.has_local_config ? ' ✓' : ' (no local config)');
      sel.appendChild(opt);
    }
  }

  async function loadTasks() {
    const projectId = document.getElementById('project-select').value;
    if (!projectId) return;
    const r = await fetch('/api/projects/' + projectId + '/pending-tasks');
    const tasks = await r.json();
    const list = document.getElementById('task-list');
    if (!tasks.length) {
      list.innerHTML = '<p style="color:#555; font-size:13px">No pending AI tasks for this project</p>';
      return;
    }
    list.innerHTML = tasks.map(t => `
      <div class="task-item" onclick="watchIssue('${t.id}', '#${t.sequence_id}')">
        <span class="priority p-${t.priority}">${t.priority}</span>
        <span>#${t.sequence_id} ${t.title}</span>
        ${t.is_running ? '<span class="running pulse">● Running</span>' : ''}
      </div>
    `).join('');
  }

  function watchIssue(issueId, label) {
    if (currentWs) currentWs.close();
    document.getElementById('log-issue-label').textContent = label;
    const log = document.getElementById('log');
    log.innerHTML = '';
    const ws = new WebSocket('ws://' + location.host + '/ws/progress/' + issueId);
    ws.onmessage = (e) => {
      const entry = document.createElement('div');
      entry.className = 'log-entry' + (e.data.includes('✅') ? ' done' : e.data.includes('🔧') ? ' tool' : ' info');
      const time = new Date().toLocaleTimeString();
      entry.innerHTML = '<span class="time">' + time + '</span>' + e.data;
      log.appendChild(entry);
      log.scrollTop = log.scrollHeight;
    };
    currentWs = ws;
  }

  loadStatus();
  loadProjects();
  setInterval(loadStatus, 10000);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.planeai_host,
        port=settings.planeai_port,
        reload=False,
        log_level="info",
    )
