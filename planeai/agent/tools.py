"""
PlaneAI — Claude Agent Tool Definitions
All the tools Claude uses to read plans, implement code, run tests, and report back.

These are Anthropic tool_use format definitions — passed directly to the Claude API.
The actual execution lives in agent/tool_executor.py.
"""

# ─────────────────────────────────────────────────────────────────────────────
# Tool Schemas (passed to Claude's API)
# ─────────────────────────────────────────────────────────────────────────────

CODING_AGENT_TOOLS = [
    {
        "name": "read_file",
        "description": (
            "Read the contents of a file in the project directory. "
            "Use this to understand existing code patterns before writing new code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path from project root (e.g. 'app/models/document.py')",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Create or overwrite a file in the project directory. "
            "Use this to write new code or update existing files. "
            "Always read the file first if it already exists."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path from project root",
                },
                "content": {
                    "type": "string",
                    "description": "The complete file content to write",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_directory",
        "description": "List the files and folders in a directory. Use this to explore the project structure.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to directory (use '.' for project root)",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "If true, list all files recursively",
                    "default": False,
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_code",
        "description": (
            "Search for a pattern (text or regex) across all files in the project. "
            "Use this to find existing patterns, imports, class definitions, or function signatures "
            "before writing new code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Text or regex pattern to search for",
                },
                "file_pattern": {
                    "type": "string",
                    "description": "Glob to filter files (e.g. '*.py', 'app/models/*.py'). Default: all files.",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "run_command",
        "description": (
            "Execute a shell command in the project directory. "
            "Use for: installing dependencies, running linters, database migrations. "
            "NEVER use for: deleting files, dropping databases, or anything destructive. "
            "Always use list form (no shell=True)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Command as a list (e.g. ['pip', 'install', 'httpx'])",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Max seconds to wait. Default: 60",
                    "default": 60,
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "run_tests",
        "description": (
            "Run the project's test suite. Returns pass/fail status, number of tests, "
            "and any failure output. Always run this after implementing code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "test_path": {
                    "type": "string",
                    "description": "Specific test file or directory to run (leave empty to run all tests)",
                },
                "extra_args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Extra args passed to the test runner (e.g. ['-v', '-k', 'test_agent'])",
                },
            },
            "required": [],
        },
    },
    {
        "name": "git_status",
        "description": "Show which files have been changed, added, or deleted since the last commit.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "git_diff",
        "description": "Show the diff of all current changes. Use before committing to verify your changes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Specific file path to diff (optional)",
                }
            },
            "required": [],
        },
    },
    {
        "name": "mark_task_complete",
        "description": (
            "Mark the current Plane task as Done and post an implementation summary comment. "
            "Only call this after running tests and confirming they pass."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Implementation summary to post as a Plane comment (markdown supported). Include: what was built, files changed, test results.",
                },
            },
            "required": ["summary"],
        },
    },
    {
        "name": "create_bug_report",
        "description": (
            "Create a new Bug issue in Plane. Use when: tests fail and cannot be fixed, "
            "an unexpected edge case is discovered, or a dependency issue is found."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short bug title (e.g. 'agent_service.py: KeyError on empty tool_result')",
                },
                "description": {
                    "type": "string",
                    "description": "Full bug report in markdown: steps to reproduce, error output, expected vs actual behavior",
                },
                "priority": {
                    "type": "string",
                    "enum": ["urgent", "high", "medium", "low"],
                    "description": "Bug priority",
                },
            },
            "required": ["title", "description", "priority"],
        },
    },
    {
        "name": "ask_for_clarification",
        "description": (
            "Post a comment on the Plane task asking a human for clarification, "
            "then pause the implementation. Use when the task description is too ambiguous to proceed safely."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The specific question to ask (be precise — what exactly is unclear?)",
                },
            },
            "required": ["question"],
        },
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# System Prompt for the Coding Agent
# ─────────────────────────────────────────────────────────────────────────────

CODING_AGENT_SYSTEM_PROMPT = """
You are PlaneAI, an autonomous coding agent embedded in the {project_name} project.

## Your Job
You receive a task from Plane (the project management tool) and your job is to:
1. Read and fully understand the task
2. Explore the existing codebase to understand patterns and conventions
3. Implement the code following those exact patterns
4. Run the test suite to verify your implementation works
5. Mark the task as complete with a clear implementation summary
6. Create bug reports for any issues you discover that are out of scope

## Project Context
- **Project:** {project_name}
- **Tech stack:** {tech_stack}
- **Project root:** {project_root}
- **Test command:** {test_command}
- **Task ID:** {task_id} | **Priority:** {priority}

## Task to Implement
**Title:** {task_title}

**Description:**
{task_description}

## Rules You Must Follow

### Code Quality
- ALWAYS read existing files first before writing new ones
- Follow the exact same coding patterns as the rest of the codebase
- Add docstrings to all new functions and classes
- Never leave TODO comments — implement the thing or create a bug report
- Filter by user_id in every database query (multi-tenant safety)

### Testing
- Run tests BEFORE and AFTER your changes
- If tests were already failing before you started, note it in your summary
- Fix any tests your changes break before marking complete
- If you cannot fix failing tests after 3 attempts, create a bug report instead

### Safety
- NEVER run destructive commands (DROP TABLE, rm -rf, etc.)
- NEVER commit API keys, tokens, or passwords
- NEVER modify files outside the project directory
- Use list-form commands only (no shell=True, no string commands)

### Communication
- If a task is too vague to implement safely, use ask_for_clarification
- Post your implementation summary in plain language a non-technical person can read
- List every file you changed in the summary

Start by reading the task description carefully, then explore the codebase, then implement.
""".strip()
