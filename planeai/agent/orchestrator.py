"""
PlaneAI — Agent Orchestrator
The core agentic loop. Takes a Plane issue, runs Claude with tools,
and keeps looping until the task is done or a stop condition is hit.

Pattern mirrors Plan B's agent_service.py design exactly.
"""

import json
import structlog
from typing import AsyncIterator, Callable

import anthropic

from config import get_settings
from plane_client import PlaneClient
from plane_client.models import PlaneIssue
from agent.tools import CODING_AGENT_TOOLS, CODING_AGENT_SYSTEM_PROMPT
from agent.tool_executor import ToolExecutor

logger = structlog.get_logger(__name__)

settings = get_settings()


class AgentRun:
    """Tracks the state of a single agent run."""

    def __init__(self, issue: PlaneIssue, project_config: dict):
        self.issue = issue
        self.project_config = project_config
        self.messages: list[dict] = []
        self.tool_calls: list[dict] = []
        self.files_changed: list[str] = []
        self.status: str = "running"  # running | completed | failed | needs_clarification
        self.summary: str = ""
        self.error: str = ""
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0


class AgentOrchestrator:
    """
    The main agentic loop.

    For each Plane issue:
    1. Build the system prompt with task context
    2. Start the Claude conversation
    3. Execute any tool calls Claude makes
    4. Loop until Claude calls mark_task_complete, create_bug_report,
       or ask_for_clarification — or until max_iterations is reached
    """

    MAX_ITERATIONS = 30  # safety cap — prevents infinite loops

    def __init__(self, plane_client: PlaneClient):
        self.plane_client = plane_client
        self.anthropic = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    def _build_system_prompt(
        self, issue: PlaneIssue, project_config: dict
    ) -> str:
        return CODING_AGENT_SYSTEM_PROMPT.format(
            project_name=project_config.get("name", "Unknown Project"),
            tech_stack=project_config.get("tech_stack", "Python/FastAPI"),
            project_root=project_config.get("local_path", "/project"),
            test_command=" ".join(project_config.get("test_command", ["pytest"])),
            task_id=issue.display_id,
            priority=issue.priority,
            task_title=issue.name,
            task_description=issue.short_description(),
        )

    def _build_initial_user_message(self, issue: PlaneIssue) -> str:
        return (
            f"Please implement the following task:\n\n"
            f"**{issue.display_id} — {issue.name}**\n\n"
            f"{issue.short_description()}\n\n"
            f"Start by exploring the codebase to understand the existing patterns, "
            f"then implement the solution. Run the tests when you're done."
        )

    async def run(
        self,
        issue: PlaneIssue,
        project_config: dict,
        progress_callback: Callable[[str], None] | None = None,
    ) -> AgentRun:
        """
        Run the full agent loop for a single Plane issue.
        Returns an AgentRun with final status and summary.
        """
        run = AgentRun(issue=issue, project_config=project_config)
        project_root = project_config.get("local_path", "")

        executor = ToolExecutor(
            project_root=project_root,
            plane_client=self.plane_client,
            issue=issue,
            shell_timeout=settings.shell_timeout,
            test_command=project_config.get("test_command", ["pytest", "-v", "--tb=short"]),
        )

        system_prompt = self._build_system_prompt(issue, project_config)
        run.messages = [
            {"role": "user", "content": self._build_initial_user_message(issue)}
        ]

        logger.info(
            "Agent run started",
            issue_id=issue.id,
            issue_title=issue.name,
            project=project_config.get("name"),
        )

        if progress_callback:
            progress_callback(f"🤖 Starting implementation of {issue.display_id}: {issue.name}")

        iteration = 0
        terminal_tools = {"mark_task_complete", "create_bug_report", "ask_for_clarification"}

        while iteration < self.MAX_ITERATIONS:
            iteration += 1
            logger.debug("Agent iteration", iteration=iteration)

            try:
                response = await self.anthropic.messages.create(
                    model=settings.anthropic_implementation_model,
                    max_tokens=8192,
                    system=system_prompt,
                    tools=CODING_AGENT_TOOLS,
                    messages=run.messages,
                )
            except anthropic.APIError as e:
                run.status = "failed"
                run.error = f"Anthropic API error: {e}"
                logger.error("API error", error=str(e))
                break

            # Track token usage
            run.total_input_tokens += response.usage.input_tokens
            run.total_output_tokens += response.usage.output_tokens

            # Collect the assistant's response blocks
            assistant_content = []
            tool_use_blocks = []

            for block in response.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                    if progress_callback and block.text.strip():
                        # Stream just the first 200 chars of Claude's thinking
                        preview = block.text.strip()[:200]
                        progress_callback(f"💭 {preview}{'...' if len(block.text) > 200 else ''}")

                elif block.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })
                    tool_use_blocks.append(block)

            # Add assistant message to history
            run.messages.append({"role": "assistant", "content": assistant_content})

            # If Claude finished without calling a tool, we're done (shouldn't happen much)
            if response.stop_reason == "end_turn" and not tool_use_blocks:
                logger.warning("Claude stopped without completing task", iteration=iteration)
                run.status = "failed"
                run.error = "Agent stopped without marking task complete"
                break

            # Process all tool calls
            tool_results = []
            hit_terminal = False

            for tool_block in tool_use_blocks:
                tool_name = tool_block.name
                tool_input = tool_block.input

                if progress_callback:
                    progress_callback(f"🔧 Using tool: {tool_name}")

                logger.info("Tool call", tool=tool_name, input_keys=list(tool_input.keys()))

                # Track file changes
                if tool_name == "write_file" and "path" in tool_input:
                    run.files_changed.append(tool_input["path"])

                # Execute the tool
                result = await executor.execute(tool_name, tool_input)

                # Store in run history
                run.tool_calls.append({
                    "tool": tool_name,
                    "input": tool_input,
                    "result": result,
                })

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_block.id,
                    "content": json.dumps(result),
                })

                # Check for terminal tools
                if tool_name in terminal_tools:
                    hit_terminal = True
                    if tool_name == "mark_task_complete":
                        run.status = "completed"
                        run.summary = tool_input.get("summary", "")
                        if progress_callback:
                            progress_callback(f"✅ Task {issue.display_id} marked complete!")
                    elif tool_name == "create_bug_report":
                        run.status = "bug_reported"
                        if progress_callback:
                            progress_callback(f"🐛 Bug report created in Plane")
                    elif tool_name == "ask_for_clarification":
                        run.status = "needs_clarification"
                        if progress_callback:
                            progress_callback(f"🤔 Paused — waiting for human clarification")

            # Add tool results back to conversation
            if tool_results:
                run.messages.append({"role": "user", "content": tool_results})

            # Exit loop if a terminal tool was called
            if hit_terminal:
                break

        else:
            # Hit max iterations
            run.status = "failed"
            run.error = f"Hit max iterations ({self.MAX_ITERATIONS}) without completing"
            logger.error("Max iterations hit", issue_id=issue.id)

            # Post a failure comment to Plane
            try:
                await self.plane_client.add_comment(
                    issue.project,
                    issue.id,
                    f"<p>⚠️ <strong>PlaneAI hit max iterations ({self.MAX_ITERATIONS})</strong> without completing this task. "
                    f"Human review required.</p>",
                )
            except Exception:
                pass

        logger.info(
            "Agent run finished",
            issue_id=issue.id,
            status=run.status,
            iterations=iteration,
            input_tokens=run.total_input_tokens,
            output_tokens=run.total_output_tokens,
            files_changed=run.files_changed,
        )

        return run
