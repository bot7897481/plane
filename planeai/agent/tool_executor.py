"""
PlaneAI — Tool Executor
Handles the actual execution of every tool Claude can call.
Each method maps 1:1 to a tool in agent/tools.py.
"""

import os
import re
import subprocess
import structlog
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from plane_client import PlaneClient
    from plane_client.models import PlaneIssue

logger = structlog.get_logger(__name__)

# Characters that look like a path traversal attempt
_TRAVERSAL_RE = re.compile(r"\.\./|~\/|^/")


class ToolExecutionError(Exception):
    pass


class ToolExecutor:
    """
    Executes the tools Claude requests.

    project_root: absolute path to the coding project (e.g. /Users/you/parseek)
    plane_client: authenticated PlaneClient instance
    issue: the current PlaneIssue being worked on
    shell_timeout: max seconds for any shell command
    """

    def __init__(
        self,
        project_root: str,
        plane_client: "PlaneClient",
        issue: "PlaneIssue",
        shell_timeout: int = 120,
        test_command: list[str] | None = None,
    ):
        self.root = Path(project_root).resolve()
        self.plane_client = plane_client
        self.issue = issue
        self.shell_timeout = shell_timeout
        self.test_command = test_command or ["pytest", "-v", "--tb=short"]

    # ── Path Safety ───────────────────────────────────────────────────────────

    def _safe_path(self, relative_path: str) -> Path:
        """
        Resolve a relative path and ensure it stays inside project_root.
        Raises ToolExecutionError on path traversal attempts.
        """
        if _TRAVERSAL_RE.search(relative_path):
            raise ToolExecutionError(f"Path traversal detected: {relative_path!r}")
        resolved = (self.root / relative_path).resolve()
        if not str(resolved).startswith(str(self.root)):
            raise ToolExecutionError(
                f"Path {resolved} is outside project root {self.root}"
            )
        return resolved

    # ── File Tools ────────────────────────────────────────────────────────────

    def read_file(self, path: str) -> dict:
        try:
            full_path = self._safe_path(path)
            if not full_path.exists():
                return {"success": False, "error": f"File not found: {path}"}
            content = full_path.read_text(encoding="utf-8")
            lines = content.splitlines()
            if len(lines) > 600:
                # Truncate very large files and warn Claude
                content = "\n".join(lines[:600])
                content += f"\n\n... [truncated — {len(lines)} total lines] ..."
            return {"success": True, "path": path, "content": content, "lines": len(lines)}
        except ToolExecutionError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": f"Read error: {e}"}

    def write_file(self, path: str, content: str) -> dict:
        try:
            full_path = self._safe_path(path)
            full_path.parent.mkdir(parents=True, exist_ok=True)
            existed = full_path.exists()
            full_path.write_text(content, encoding="utf-8")
            lines = len(content.splitlines())
            action = "updated" if existed else "created"
            logger.info("File written", path=path, lines=lines, action=action)
            return {"success": True, "path": path, "action": action, "lines": lines}
        except ToolExecutionError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": f"Write error: {e}"}

    def list_directory(self, path: str, recursive: bool = False) -> dict:
        try:
            full_path = self._safe_path(path)
            if not full_path.is_dir():
                return {"success": False, "error": f"Not a directory: {path}"}

            if recursive:
                entries = []
                for p in sorted(full_path.rglob("*")):
                    if any(part.startswith(".") for part in p.parts):
                        continue  # skip hidden dirs like .git, __pycache__
                    if "__pycache__" in str(p):
                        continue
                    rel = p.relative_to(self.root)
                    entries.append(
                        {"path": str(rel), "type": "dir" if p.is_dir() else "file"}
                    )
            else:
                entries = []
                for p in sorted(full_path.iterdir()):
                    rel = p.relative_to(self.root)
                    entries.append(
                        {"path": str(rel), "type": "dir" if p.is_dir() else "file"}
                    )

            return {"success": True, "path": path, "entries": entries[:200]}
        except ToolExecutionError as e:
            return {"success": False, "error": str(e)}

    def search_code(self, pattern: str, file_pattern: str = "") -> dict:
        try:
            cmd = ["grep", "-rn", "--include", file_pattern or "*.py", pattern, str(self.root)]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30
            )
            matches = result.stdout.strip().splitlines()[:50]  # cap results
            # Make paths relative to project root
            clean = []
            for line in matches:
                if str(self.root) in line:
                    line = line.replace(str(self.root) + "/", "")
                clean.append(line)
            return {
                "success": True,
                "pattern": pattern,
                "matches": clean,
                "count": len(clean),
                "truncated": len(matches) >= 50,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Search timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Shell Tools ───────────────────────────────────────────────────────────

    def run_command(self, command: list[str], timeout: int | None = None) -> dict:
        """
        Run a shell command safely.
        - command must be a list (no shell=True)
        - cwd is always project_root
        - blocked commands: rm, drop, delete, truncate (destructive)
        """
        BLOCKED = {"rm", "rmdir", "drop", "truncate", "format", "mkfs"}
        if command and command[0].lower() in BLOCKED:
            return {
                "success": False,
                "error": f"Command '{command[0]}' is blocked for safety.",
            }

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout or self.shell_timeout,
                cwd=str(self.root),
            )
            output = result.stdout + result.stderr
            logger.info(
                "Command executed",
                command=command,
                returncode=result.returncode,
            )
            return {
                "success": result.returncode == 0,
                "returncode": result.returncode,
                "output": output[:3000],  # cap output
                "command": " ".join(command),
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"Command timed out after {timeout}s"}
        except FileNotFoundError:
            return {"success": False, "error": f"Command not found: {command[0]}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def run_tests(
        self, test_path: str = "", extra_args: list[str] | None = None
    ) -> dict:
        cmd = list(self.test_command)
        if test_path:
            cmd.append(test_path)
        if extra_args:
            cmd.extend(extra_args)

        logger.info("Running tests", command=cmd)
        result = self.run_command(cmd, timeout=self.shell_timeout)

        # Parse pytest output for summary
        output = result.get("output", "")
        passed = failed = errors = 0
        for line in output.splitlines():
            if " passed" in line:
                import re
                m = re.search(r"(\d+) passed", line)
                if m:
                    passed = int(m.group(1))
            if " failed" in line:
                m = re.search(r"(\d+) failed", line)
                if m:
                    failed = int(m.group(1))
            if " error" in line:
                m = re.search(r"(\d+) error", line)
                if m:
                    errors = int(m.group(1))

        return {
            **result,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "all_passed": result.get("success", False) and failed == 0 and errors == 0,
        }

    # ── Git Tools ─────────────────────────────────────────────────────────────

    def git_status(self) -> dict:
        return self.run_command(["git", "status", "--short"])

    def git_diff(self, path: str = "") -> dict:
        cmd = ["git", "diff"]
        if path:
            cmd.append(path)
        return self.run_command(cmd)

    # ── Plane Reporting ───────────────────────────────────────────────────────

    async def mark_task_complete(self, summary: str) -> dict:
        issue = self.issue
        project_id = issue.project
        issue_id = issue.id

        comment_html = f"""
<h3>✅ PlaneAI Implementation Complete</h3>
<p>{summary.replace(chr(10), '<br>')}</p>
<hr>
<p><em>Implemented autonomously by PlaneAI using Claude {self.plane_client._headers.get('X-Agent', 'claude-opus-4-6')}</em></p>
""".strip()

        try:
            await self.plane_client.add_comment(project_id, issue_id, comment_html)
            await self.plane_client.mark_issue_done(project_id, issue_id)
            logger.info("Task marked complete", issue_id=issue_id)
            return {"success": True, "message": "Task marked as Done in Plane"}
        except Exception as e:
            logger.error("Failed to mark task complete", error=str(e))
            return {"success": False, "error": str(e)}

    async def create_bug_report(
        self, title: str, description: str, priority: str = "high"
    ) -> dict:
        issue = self.issue
        project_id = issue.project

        description_html = f"""
<h3>🐛 Bug Discovered During Implementation of {issue.display_id}</h3>
<pre>{description}</pre>
<hr>
<p><strong>Discovered by:</strong> PlaneAI while implementing task {issue.display_id}: {issue.name}</p>
""".strip()

        try:
            bug = await self.plane_client.create_bug_issue(
                project_id=project_id,
                title=f"[AI] {title}",
                description_html=description_html,
                parent_issue_id=issue.id,
                priority=priority,
            )
            # Also comment on the original issue
            await self.plane_client.add_comment(
                project_id,
                issue.id,
                f"<p>⚠️ PlaneAI discovered a bug during implementation and created issue {bug.display_id}: <strong>{title}</strong></p>",
            )
            return {
                "success": True,
                "bug_issue_id": bug.id,
                "bug_sequence_id": bug.sequence_id,
                "message": f"Bug issue #{bug.sequence_id} created in Plane",
            }
        except Exception as e:
            logger.error("Failed to create bug report", error=str(e))
            return {"success": False, "error": str(e)}

    async def ask_for_clarification(self, question: str) -> dict:
        issue = self.issue
        comment_html = f"""
<h3>🤔 PlaneAI Needs Clarification</h3>
<p><strong>Question:</strong> {question}</p>
<p><em>Implementation paused. Please reply to this comment and re-assign the task to the AI Agent to resume.</em></p>
""".strip()
        try:
            await self.plane_client.add_comment(issue.project, issue.id, comment_html)
            # Move back to unstarted so it's not stuck in progress
            state = await self.plane_client.get_state_by_group(issue.project, "unstarted")
            if state:
                from plane_client.models import IssueUpdatePayload
                await self.plane_client.update_issue(
                    issue.project, issue.id, IssueUpdatePayload(state=state.id)
                )
            return {"success": True, "message": "Clarification posted to Plane, task paused"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Dispatcher ────────────────────────────────────────────────────────────

    async def execute(self, tool_name: str, tool_input: dict) -> dict:
        """Route a tool call from Claude to the correct method."""
        sync_tools = {
            "read_file": lambda: self.read_file(**tool_input),
            "write_file": lambda: self.write_file(**tool_input),
            "list_directory": lambda: self.list_directory(**tool_input),
            "search_code": lambda: self.search_code(**tool_input),
            "run_command": lambda: self.run_command(**tool_input),
            "run_tests": lambda: self.run_tests(**tool_input),
            "git_status": lambda: self.git_status(),
            "git_diff": lambda: self.git_diff(**tool_input),
        }
        async_tools = {
            "mark_task_complete": self.mark_task_complete,
            "create_bug_report": self.create_bug_report,
            "ask_for_clarification": self.ask_for_clarification,
        }

        if tool_name in sync_tools:
            return sync_tools[tool_name]()
        elif tool_name in async_tools:
            return await async_tools[tool_name](**tool_input)
        else:
            return {"success": False, "error": f"Unknown tool: {tool_name}"}
