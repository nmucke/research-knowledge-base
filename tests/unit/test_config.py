"""Tests for typed step-1 configuration."""

from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from research_kb.config import Settings


def test_vault_paths_are_resolved_from_root(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, research_vault_path=tmp_path)

    assert settings.vault_path == tmp_path.resolve()
    assert settings.papers_dir == tmp_path / "vault" / "Literature" / "Papers"
    assert settings.tag_registry_path == tmp_path / "vault" / "System" / "tag-registry.md"
    assert settings.log_dir == tmp_path / ".research" / "logs"
    assert settings.credentials_path == tmp_path / ".research" / "credentials.json"


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


def test_unknown_tag_policy_defaults_to_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("UNKNOWN_TAG_POLICY", raising=False)

    settings = Settings(_env_file=None, research_vault_path=tmp_path)

    assert settings.unknown_tag_policy == "error"


def test_unknown_tag_policy_loads_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNKNOWN_TAG_POLICY", "warning")

    settings = Settings(_env_file=None, research_vault_path=tmp_path)

    assert settings.unknown_tag_policy == "warning"


def test_invalid_unknown_tag_policy_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            research_vault_path=tmp_path,
            unknown_tag_policy="ignore",
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


def test_non_loopback_local_service_url_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="loopback host"):
        Settings(
            _env_file=None,
            research_vault_path=tmp_path,
            zotero_local_api="https://example.com/api",
        )


def test_allowed_tag_namespaces_use_the_canonical_default(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, research_vault_path=tmp_path)

    assert settings.allowed_tag_namespaces == (
        "domain",
        "method",
        "task",
        "property",
        "model",
        "data",
    )


def test_allowed_tag_namespaces_accept_a_comma_separated_subset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALLOWED_TAG_NAMESPACES", "domain, method")

    settings = Settings(_env_file=None, research_vault_path=tmp_path)

    assert settings.allowed_tag_namespaces == ("domain", "method")


@pytest.mark.parametrize("value", [(), ("domain", "domain"), ("unknown",)])
def test_invalid_allowed_tag_namespaces_are_rejected(
    tmp_path: Path, value: tuple[str, ...]
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, research_vault_path=tmp_path, allowed_tag_namespaces=value)


def test_web_write_credentials_are_optional_but_must_be_configured_together(
    tmp_path: Path,
) -> None:
    empty = Settings(
        _env_file=None,
        research_vault_path=tmp_path,
        zotero_web_api_key="",
        zotero_web_library_id="",
    )
    assert not empty.web_write_configured

    configured = Settings(
        _env_file=None,
        research_vault_path=tmp_path,
        zotero_web_api_key=" private ",
        zotero_web_library_id=123,
    )
    assert configured.web_write_configured
    assert configured.zotero_web_api_key == "private"

    with pytest.raises(ValidationError, match="must be configured together"):
        Settings(
            _env_file=None,
            research_vault_path=tmp_path,
            zotero_web_api_key="private",
        )
