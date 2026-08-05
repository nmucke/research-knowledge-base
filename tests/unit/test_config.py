"""Tests for typed step-1 configuration."""

from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from research_kb.config import Settings


def test_vault_paths_are_resolved_from_root(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, research_vault_path=tmp_path)

    assert settings.vault_path == tmp_path.resolve()
    assert settings.papers_dir == tmp_path / "Literature" / "Papers"
    assert settings.tag_registry_path == tmp_path / "System" / "tag-registry.md"
    assert settings.log_dir == tmp_path / ".research" / "logs"


def test_log_level_is_case_insensitive(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        research_vault_path=tmp_path,
        research_log_level="debug",
    )

    assert settings.research_log_level == "DEBUG"


def test_invalid_library_type_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            research_vault_path=tmp_path,
            zotero_library_type="shared",
        )


def test_service_urls_are_validated_httpx_compatible_strings(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, research_vault_path=tmp_path)

    assert isinstance(settings.zotero_local_api, str)
    assert isinstance(settings.better_bibtex_rpc, str)
    assert httpx.URL(settings.zotero_local_api).path == "/api"
    assert httpx.URL(settings.better_bibtex_rpc).path == "/better-bibtex/json-rpc"


def test_invalid_service_url_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            research_vault_path=tmp_path,
            zotero_local_api="not-a-url",
        )
