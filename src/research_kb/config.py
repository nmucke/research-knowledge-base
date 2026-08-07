"""Typed application configuration loaded from environment variables."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, DirectoryPath, TypeAdapter, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from research_kb.models import TAG_NAMESPACES

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LibraryType = Literal["user", "group"]
UnknownTagPolicy = Literal["error", "warning"]
HTTP_URL_ADAPTER = TypeAdapter(AnyHttpUrl)
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})
ZOTERO_WEB_API_URL = "https://api.zotero.org"


class Settings(BaseSettings):
    """Runtime settings with paths resolved relative to the vault root."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    research_vault_path: DirectoryPath = Path(".")
    research_obsidian_dir: Path = Path("vault")
    zotero_local_api: str = "http://localhost:23119/api"
    better_bibtex_rpc: str = "http://localhost:23119/better-bibtex/json-rpc"
    zotero_library_type: LibraryType = "user"
    zotero_library_id: int = 0
    research_log_level: LogLevel = "INFO"
    unknown_tag_policy: UnknownTagPolicy = "error"
    zotero_web_api_key: str | None = None
    zotero_web_library_id: int | None = None
    allowed_tag_namespaces: Annotated[tuple[str, ...], NoDecode] = TAG_NAMESPACES

    @field_validator("zotero_local_api", "better_bibtex_rpc", mode="before")
    @classmethod
    def validate_service_url(cls, value: object) -> str:
        """Validate HTTP endpoints while exposing strings compatible with httpx."""
        url = HTTP_URL_ADAPTER.validate_python(value)
        if url.host not in LOOPBACK_HOSTS:
            raise ValueError("local service URLs must use a loopback host")
        return str(url).rstrip("/")

    @field_validator("research_obsidian_dir", mode="before")
    @classmethod
    def validate_obsidian_dir(cls, value: object) -> object:
        """Reject an empty or upward-traversing Obsidian vault location."""
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("the Obsidian vault directory must not be empty")
        directory = Path(str(value))
        if not directory.is_absolute() and ".." in directory.parts:
            raise ValueError("the Obsidian vault directory must not traverse above the project")
        return directory

    @field_validator("research_log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        """Accept conventional case-insensitive log-level values."""
        return value.upper() if isinstance(value, str) else value

    @field_validator("zotero_web_api_key", mode="before")
    @classmethod
    def normalize_optional_web_api_key(cls, value: object) -> object:
        """Treat an empty local override as an unconfigured optional secret."""
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("zotero_web_library_id", mode="before")
    @classmethod
    def normalize_optional_web_library_id(cls, value: object) -> object:
        """Treat an empty local override as an unconfigured optional identifier."""
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("allowed_tag_namespaces", mode="before")
    @classmethod
    def validate_allowed_tag_namespaces(cls, value: object) -> tuple[str, ...] | object:
        """Accept an ordered comma-separated local override without widening policy."""
        if isinstance(value, str):
            value = tuple(namespace.strip() for namespace in value.split(",") if namespace.strip())
        if not isinstance(value, (tuple, list)) or not value:
            raise ValueError("allowed tag namespaces must be a non-empty ordered collection")
        namespaces = tuple(str(namespace) for namespace in value)
        if len(set(namespaces)) != len(namespaces):
            raise ValueError("allowed tag namespaces must not contain duplicates")
        unknown = set(namespaces).difference(TAG_NAMESPACES)
        if unknown:
            raise ValueError(
                "allowed tag namespaces must be canonical namespaces; unknown: "
                f"{', '.join(sorted(unknown))}"
            )
        return namespaces

    @model_validator(mode="after")
    def web_write_credentials_must_be_complete(self) -> "Settings":
        """Require the Web API key and library ID as an inseparable fallback pair."""
        configured = (self.zotero_web_api_key is not None, self.zotero_web_library_id is not None)
        if configured[0] != configured[1]:
            raise ValueError(
                "ZOTERO_WEB_API_KEY and ZOTERO_WEB_LIBRARY_ID must be configured together"
            )
        return self

    @property
    def web_write_configured(self) -> bool:
        """Whether the explicit Zotero Web API write fallback is configured."""
        return self.zotero_web_api_key is not None and self.zotero_web_library_id is not None

    @property
    def vault_path(self) -> Path:
        """Project root holding generated state, tooling, and the Obsidian vault."""
        return Path(self.research_vault_path).resolve()

    @property
    def obsidian_vault_path(self) -> Path:
        """Root of the Obsidian vault; the only directory opened in Obsidian."""
        directory = Path(self.research_obsidian_dir)
        return directory.resolve() if directory.is_absolute() else self.vault_path / directory

    @property
    def papers_dir(self) -> Path:
        return self.obsidian_vault_path / "Literature" / "Papers"

    @property
    def templates_dir(self) -> Path:
        return self.obsidian_vault_path / "System" / "Templates"

    @property
    def tag_registry_path(self) -> Path:
        return self.obsidian_vault_path / "System" / "tag-registry.md"

    @property
    def reading_profile_path(self) -> Path:
        return self.obsidian_vault_path / "System" / "reading-profile.md"

    @property
    def research_dir(self) -> Path:
        return self.vault_path / ".research"

    @property
    def credentials_path(self) -> Path:
        """Path for locally stored Zotero write credentials."""
        return self.research_dir / "credentials.json"

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
