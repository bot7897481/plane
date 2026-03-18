# PlaneAI — Quickstart Guide

Get PlaneAI running alongside your Plane instance in 4 steps.

---

## Prerequisites

- Plane is already running (locally or hosted)
- Python 3.12+
- Redis (can share Plane's Redis or run a new one)
- An Anthropic API key
- Your Parseek and/or Vazier repos cloned locally

---

## Step 1 — Configure PlaneAI

```bash
cd planeai/
cp .env.example .env
```

Edit `.env`:
```env
PLANE_BASE_URL=http://localhost:8000      # Your Plane URL
PLANE_API_TOKEN=<from Plane Settings → API Tokens>
PLANE_WORKSPACE_SLUG=<your-workspace>

ANTHROPIC_API_KEY=sk-ant-...

# The Plane user ID of a user you created called "AI Agent"
PLANEAI_AGENT_USER_ID=<uuid>

# Point to your actual project directories
PROJECT_PATHS=parseek:/Users/you/parseek,vazier:/Users/you/vazier-ai
```

---

## Step 2 — Add .planeai.yml to Each Project

Copy the right config into each project's root:
```bash
# For Parseek:
cp planeai/config/parseek.planeai.yml /path/to/parseek/.planeai.yml

# For Vazier:
cp planeai/config/vazier.planeai.yml /path/to/vazier-ai/.planeai.yml
```

---

## Step 3 — Create the AI Agent User in Plane

1. Open Plane → Workspace Settings → Members
2. Invite a new member with email: `ai-agent@your-domain.com`
3. Copy that user's UUID from the Plane database or API
4. Set it as `PLANEAI_AGENT_USER_ID` in your `.env`

---

## Step 4 — Start PlaneAI

```bash
cd planeai/
pip install -r requirements.txt
python main.py
```

Or with Docker:
```bash
docker-compose up -d
```

Open the dashboard: **http://localhost:9000**

---

## How to Give PlaneAI a Task

1. Open Plane → your Parseek or Vazier project
2. Create or find a task (issue)
3. **Assign it to "AI Agent"** (the user you created in Step 3)
4. PlaneAI will pick it up within `PLANEAI_POLL_INTERVAL` seconds (default: 30s)
5. Watch it live at http://localhost:9000

PlaneAI will:
- Mark the task "In Progress"
- Read the codebase, implement the code, run tests
- Mark "Done" + post implementation notes — or create a Bug issue if tests fail

---

## Recommended First Tasks to Assign

Based on your Plan B, assign these in order:

| Priority | Task | Plan B Reference |
|---|---|---|
| 1 | Create `app/models/agent.py` with AgentSession + AgentMessage models | Task 1.1 |
| 2 | Create `app/services/agent_service.py` — core agent loop | Task 1.2 |
| 3 | Create `app/api/agent.py` — WebSocket + REST endpoints | Task 1.3 |
| 4 | Create agent chat frontend page | Task 1.4 |

Copy the "Claude Code Prompt" from each PLAN_B task directly into the Plane issue description. PlaneAI will use it as implementation instructions.

---

## Monitoring

- **Dashboard:** http://localhost:9000
- **Health check:** http://localhost:9000/api/health
- **Active runs:** http://localhost:9000/api/status
- **Live progress:** WebSocket at ws://localhost:9000/ws/progress/{issue_id}
