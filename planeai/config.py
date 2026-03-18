"""
PlaneAI — Configuration
Mirrors Parseek's config.py pattern (pydantic-settings).
"""

from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Plane ─────────────────────────────────
    plane_base_url: str = "http://localhost:8000"
    plane_api_token: str = Field(..., description="Plane API token")
    plane_workspace_slug: str = Field(..., description="Your Plane workspace slug")

    # ── Anthropic ─────────────────────────────
    anthropic_api_key: str = Field(..., description="Anthropic API key")
    anthropic_implementation_model: str = "claude-opus-4-6"
    anthropic_fast_model: str = "claude-haiku-4-5-20251001"

    # ── Service ───────────────────────────────
    planeai_host: str = "0.0.0.0"
    planeai_port: int = 9000
    planeai_poll_interval: int = 30  # seconds
    planeai_agent_user_id: str = Field(..., description="Plane user ID of the AI agent")
    planeai_secret_key: str = "change-me"

    # ── Redis ─────────────────────────────────
    redis_url: str = "redis://localhost:6379/1"

    # ── Projects ──────────────────────────────
    projects_root: str = "/projects"
    # Raw string: "parseek:/path/to/parseek,vazier:/path/to/vazier"
    project_paths: str = ""

    # ── Safety ────────────────────────────────
    max_lines_per_task: int = 500
    shell_timeout: int = 120
    require_approval: bool = False
    ai_branch_prefix: str = "planeai/"

    def get_project_path_map(self) -> dict[str, str]:
        """Parse 'name:/path,name2:/path2' into {name: path}."""
        result = {}
        if not self.project_paths:
            return result
        for entry in self.project_paths.split(","):
            entry = entry.strip()
            if ":" in entry:
                name, path = entry.split(":", 1)
                result[name.strip()] = path.strip()
        return result


@lru_cache
def get_settings() -> Settings:
    return Settings()
