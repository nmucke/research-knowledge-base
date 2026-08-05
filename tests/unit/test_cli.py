"""Tests for the CLI contract."""

from pathlib import Path
from typing import ClassVar, Self

import pytest
from typer.testing import CliRunner

import research_kb.cli as cli
from research_kb.better_bibtex import BetterBibTeXClient
from research_kb.exceptions import (
    BetterBibTeXUnavailableError,
    CitationKeyMissingError,
    ZoteroUnavailableError,
)
from research_kb.zotero_client import ZoteroClient, ZoteroServerInfo

app = cli.app

runner = CliRunner()


def test_help_starts_successfully() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Manage the local Zotero-Obsidian literature workflow" in result.stdout
    assert "doctor" in result.stdout
    assert "show" in result.stdout


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


def test_show_prints_item_metadata_and_uses_configured_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ShowZoteroClient.reset()
    ShowBetterBibTeXClient.reset()
    monkeypatch.setattr(cli, "ZoteroClient", ShowZoteroClient)
    monkeypatch.setattr(cli, "BetterBibTeXClient", ShowBetterBibTeXClient)

    result = runner.invoke(
        app,
        ["show", "--zotero-key", "ABCD1234"],
        env={
            "RESEARCH_VAULT_PATH": str(tmp_path),
            "ZOTERO_LIBRARY_TYPE": "group",
            "ZOTERO_LIBRARY_ID": "42",
        },
    )

    assert result.exit_code == 0
    assert result.output.splitlines() == [
        "Title: A [useful] paper",
        "Zotero key: ABCD1234",
        "Citation key: Doe2026Useful",
        "Item type: journalArticle",
        "Version: 7",
        "Authors: Jane Doe, John Smith",
        "Date: 2026-01-15",
        "Publication: Journal of Useful Results",
        "DOI: -",
        "URL: -",
    ]
    assert ShowZoteroClient.get_item_calls == [("ABCD1234", "group", 42)]
    assert ShowBetterBibTeXClient.get_citation_key_calls == [("ABCD1234", 42)]
    assert ShowZoteroClient.closed is True
    assert ShowBetterBibTeXClient.closed is True


def test_show_stops_before_better_bibtex_when_item_read_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ShowZoteroClient.reset()
    ShowBetterBibTeXClient.reset()
    ShowZoteroClient.get_item_error = ZoteroUnavailableError("Start Zotero and try again.")
    monkeypatch.setattr(cli, "ZoteroClient", ShowZoteroClient)
    monkeypatch.setattr(cli, "BetterBibTeXClient", ShowBetterBibTeXClient)

    result = runner.invoke(
        app,
        ["show", "--zotero-key", "ABCD1234"],
        env={"RESEARCH_VAULT_PATH": str(tmp_path)},
    )

    assert result.exit_code == 1
    assert result.stderr.splitlines() == ["Error: Start Zotero and try again."]
    assert ShowBetterBibTeXClient.get_citation_key_calls == []
    assert ShowZoteroClient.closed is True
    assert ShowBetterBibTeXClient.closed is True


def test_show_reports_citation_key_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ShowZoteroClient.reset()
    ShowBetterBibTeXClient.reset()
    ShowBetterBibTeXClient.get_citation_key_error = CitationKeyMissingError(
        "Generate a citation key in Better BibTeX and try again."
    )
    monkeypatch.setattr(cli, "ZoteroClient", ShowZoteroClient)
    monkeypatch.setattr(cli, "BetterBibTeXClient", ShowBetterBibTeXClient)

    result = runner.invoke(
        app,
        ["show", "--zotero-key", "ABCD1234"],
        env={"RESEARCH_VAULT_PATH": str(tmp_path)},
    )

    assert result.exit_code == 1
    assert result.stderr.splitlines() == [
        "Error: Generate a citation key in Better BibTeX and try again."
    ]
    assert ShowBetterBibTeXClient.get_citation_key_calls == [("ABCD1234", 0)]
    assert ShowZoteroClient.closed is True
    assert ShowBetterBibTeXClient.closed is True


def test_show_reports_a_malformed_item_key_as_a_user_error(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["show", "--zotero-key", "not/a/key"],
        env={"RESEARCH_VAULT_PATH": str(tmp_path)},
    )

    assert result.exit_code == 1
    assert result.stderr.splitlines() == [
        "Error: Zotero item keys must contain exactly 8 uppercase letters or digits."
    ]


class SuccessfulZoteroClient(ZoteroClient):
    def __init__(self, base_url: str) -> None:
        del base_url

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def discover(self) -> ZoteroServerInfo:
        return ZoteroServerInfo(3, "server-1", 1)


class SuccessfulBetterBibTeXClient(BetterBibTeXClient):
    def __init__(self, rpc_url: str) -> None:
        del rpc_url

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def check_ready(self) -> None:
        return None


class FailingZoteroClient(SuccessfulZoteroClient):
    def discover(self) -> ZoteroServerInfo:
        raise ZoteroUnavailableError


class FailingBetterBibTeXClient(SuccessfulBetterBibTeXClient):
    def check_ready(self) -> None:
        raise BetterBibTeXUnavailableError


class ShowCreator:
    def __init__(self, display_name: str) -> None:
        self.display_name = display_name


class ShowItem:
    key = "ABCD1234"
    version = 7
    item_type = "journalArticle"
    title = "A [useful] paper"
    authors = (ShowCreator("Jane Doe"), ShowCreator("John Smith"))
    date = "2026-01-15"
    publication = "Journal of Useful Results"
    doi = None
    url = None


class ShowZoteroClient:
    get_item_calls: ClassVar[list[tuple[str, str, int]]] = []
    get_item_error: ClassVar[ZoteroUnavailableError | None] = None
    closed: ClassVar[bool] = False

    def __init__(self, base_url: str) -> None:
        del base_url

    @classmethod
    def reset(cls) -> None:
        cls.get_item_calls = []
        cls.get_item_error = None
        cls.closed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        type(self).closed = True

    def get_item(self, item_key: str, *, library_type: str, library_id: int) -> ShowItem:
        type(self).get_item_calls.append((item_key, library_type, library_id))
        error = type(self).get_item_error
        if error is not None:
            raise error
        return ShowItem()


class ShowBetterBibTeXClient:
    get_citation_key_calls: ClassVar[list[tuple[str, int]]] = []
    get_citation_key_error: ClassVar[CitationKeyMissingError | None] = None
    closed: ClassVar[bool] = False

    def __init__(self, rpc_url: str) -> None:
        del rpc_url

    @classmethod
    def reset(cls) -> None:
        cls.get_citation_key_calls = []
        cls.get_citation_key_error = None
        cls.closed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        type(self).closed = True

    def get_citation_key(self, item_key: str, *, library_id: int) -> str:
        type(self).get_citation_key_calls.append((item_key, library_id))
        error = type(self).get_citation_key_error
        if error is not None:
            raise error
        return "Doe2026Useful"


def _create_required_vault_paths(vault_path: Path) -> None:
    (vault_path / "Literature" / "Papers").mkdir(parents=True)
    (vault_path / "System" / "Templates").mkdir(parents=True)
    (vault_path / "System" / "reading-profile.md").write_text("profile\n", encoding="utf-8")
    (vault_path / "System" / "tag-registry.md").write_text("tags\n", encoding="utf-8")
