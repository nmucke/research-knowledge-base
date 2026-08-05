"""Tests for the CLI contract."""

from pathlib import Path
from types import TracebackType
from typing import Self

import pytest
from typer.testing import CliRunner

import research_kb.cli as cli
from research_kb.better_bibtex import BetterBibTeXClient
from research_kb.exceptions import BetterBibTeXUnavailableError, ZoteroUnavailableError
from research_kb.zotero_client import ZoteroClient, ZoteroServerInfo

app = cli.app

runner = CliRunner()


def test_help_starts_successfully() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Manage the local Zotero-Obsidian literature workflow" in result.stdout
    assert "doctor" in result.stdout


def test_version_starts_successfully() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.startswith("research ")


def test_doctor_reports_success_and_optional_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create_required_vault_paths(tmp_path)
    (tmp_path / "references.bib").write_text("% bibliography\n", encoding="utf-8")
    monkeypatch.setattr(cli, "ZoteroClient", SuccessfulZoteroClient)
    monkeypatch.setattr(cli, "BetterBibTeXClient", SuccessfulBetterBibTeXClient)

    result = runner.invoke(
        app,
        ["doctor"],
        env={"RESEARCH_VAULT_PATH": str(tmp_path)},
    )

    assert result.exit_code == 0
    output = " ".join(result.output.split())
    assert "PASS Zotero: Zotero ready: API 3, server server-1, schema 1." in output
    assert "PASS Better BibTeX: Better BibTeX is ready." in output
    assert "WARN Write authorization" in output

    log_text = (tmp_path / ".research" / "logs" / "research.log").read_text(
        encoding="utf-8"
    )
    assert "command_start command=doctor" in log_text
    assert "doctor_check name=write_authorization status=warn" in log_text


def test_doctor_reports_all_service_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create_required_vault_paths(tmp_path)
    monkeypatch.setattr(cli, "ZoteroClient", FailingZoteroClient)
    monkeypatch.setattr(cli, "BetterBibTeXClient", FailingBetterBibTeXClient)

    result = runner.invoke(
        app,
        ["doctor"],
        env={"RESEARCH_VAULT_PATH": str(tmp_path)},
    )

    assert result.exit_code == 1
    output = " ".join(result.output.split())
    assert "FAIL Zotero" in output
    assert "FAIL Better BibTeX" in output
    assert "WARN references.bib" in output
    assert "PASS Vault paths" in output


class SuccessfulZoteroClient(ZoteroClient):
    def __init__(self, base_url: str) -> None:
        del base_url

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def discover(self) -> ZoteroServerInfo:
        return ZoteroServerInfo(3, "server-1", 1)


class SuccessfulBetterBibTeXClient(BetterBibTeXClient):
    def __init__(self, rpc_url: str) -> None:
        del rpc_url

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def check_ready(self) -> None:
        return None


class FailingZoteroClient(SuccessfulZoteroClient):
    def discover(self) -> ZoteroServerInfo:
        raise ZoteroUnavailableError


class FailingBetterBibTeXClient(SuccessfulBetterBibTeXClient):
    def check_ready(self) -> None:
        raise BetterBibTeXUnavailableError


def _create_required_vault_paths(vault_path: Path) -> None:
    (vault_path / "Literature" / "Papers").mkdir(parents=True)
    (vault_path / "System" / "Templates").mkdir(parents=True)
    (vault_path / "System" / "reading-profile.md").write_text("profile\n", encoding="utf-8")
    (vault_path / "System" / "tag-registry.md").write_text("tags\n", encoding="utf-8")
