"""Typed application configuration loaded from environment variables."""

from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, DirectoryPath, TypeAdapter, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LibraryType = Literal["user", "group"]
UnknownTagPolicy = Literal["error", "warning"]
HTTP_URL_ADAPTER = TypeAdapter(AnyHttpUrl)
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})


class Settings(BaseSettings):
    """Runtime settings with paths resolved relative to the vault root."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    research_vault_path: DirectoryPath = Path(".")
    zotero_local_api: str = "http://localhost:23119/api"
    better_bibtex_rpc: str = "http://localhost:23119/better-bibtex/json-rpc"
    zotero_library_type: LibraryType = "user"
    zotero_library_id: int = 0
    research_log_level: LogLevel = "INFO"
    unknown_tag_policy: UnknownTagPolicy = "error"
    zotero_web_api_key: str | None = None
    zotero_web_library_id: int | None = None

    @field_validator("zotero_local_api", "better_bibtex_rpc", mode="before")
    @classmethod
    def validate_service_url(cls, value: object) -> str:
        """Validate HTTP endpoints while exposing strings compatible with httpx."""
        url = HTTP_URL_ADAPTER.validate_python(value)
        if url.host not in LOOPBACK_HOSTS:
            raise ValueError("local service URLs must use a loopback host")
        return str(url).rstrip("/")

    @field_validator("research_log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        """Accept conventional case-insensitive log-level values."""
        return value.upper() if isinstance(value, str) else value

    @property
    def vault_path(self) -> Path:
        return Path(self.research_vault_path).resolve()

    @property
    def papers_dir(self) -> Path:
        return self.vault_path / "Literature" / "Papers"

    @property
    def templates_dir(self) -> Path:
        return self.vault_path / "System" / "Templates"

    @property
    def tag_registry_path(self) -> Path:
        return self.vault_path / "System" / "tag-registry.md"

    @property
    def reading_profile_path(self) -> Path:
        return self.vault_path / "System" / "reading-profile.md"

    @property
    def research_dir(self) -> Path:
        return self.vault_path / ".research"

    @property
    def log_dir(self) -> Path:
        return self.research_dir / "logs"

    @property
    def paper_text_dir(self) -> Path:
        """Directory containing page-aware, regenerable PDF text caches."""
        return self.research_dir / "paper-text"

    @property
    def review_snapshot_dir(self) -> Path:
        """Directory containing one-shot protected-state review snapshots."""
        return self.research_dir / "review-snapshots"
