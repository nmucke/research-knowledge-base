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
    SyncError,
    ZoteroUnavailableError,
)
from research_kb.sync_service import SyncAction, SyncItemResult, SyncReport
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


def test_sync_help_lists_supported_options() -> None:
    result = runner.invoke(app, ["sync", "--help"])

    assert result.exit_code == 0
    assert "--dry-run" in result.stdout
    assert "--full" in result.stdout
    assert "--item" in result.stdout
    assert "--citekey" in result.stdout


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (
            ["--item", "ABCD1234", "--citekey", "doe2026"],
            "Error: --item and --citekey cannot be used together.",
        ),
        (
            ["--full", "--item", "ABCD1234"],
            "Error: --full cannot be used with --item or --citekey.",
        ),
    ],
)
def test_sync_rejects_incompatible_flags_before_opening_clients(
    tmp_path: Path,
    arguments: list[str],
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "ZoteroClient", _UnexpectedSyncClient)
    monkeypatch.setattr(cli, "BetterBibTeXClient", _UnexpectedSyncClient)

    result = runner.invoke(app, ["sync", *arguments], env={"RESEARCH_VAULT_PATH": str(tmp_path)})

    assert result.exit_code == 1
    assert result.stderr.splitlines() == [expected]


def test_sync_passes_flags_to_service_and_renders_dry_run_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _SyncServiceFake.reset(
        SyncReport(
            mode="full",
            dry_run=True,
            previous_version=None,
            library_version=12,
            state_updated=False,
            fallback_reason="incremental-read-failed",
            items=(
                SyncItemResult(SyncAction.RENAMED, "BBBB2222", "new", Path("new.md"), "old"),
                SyncItemResult(SyncAction.CREATED, "AAAA1111", "first", Path("first.md")),
            ),
        )
    )
    _SyncClient.reset()
    monkeypatch.setattr(cli, "ZoteroClient", _SyncClient)
    monkeypatch.setattr(cli, "BetterBibTeXClient", _SyncClient)
    monkeypatch.setattr(cli, "SyncService", _SyncServiceFake)

    result = runner.invoke(
        app,
        ["sync", "--dry-run", "--citekey", "first"],
        env={"RESEARCH_VAULT_PATH": str(tmp_path)},
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "DRY-RUN CREATE AAAA1111 first",
        "DRY-RUN RENAME BBBB2222 old -> new",
        (
            "Summary: mode=full, count=2, version=12, "
            "incremental-fallback=yes (incremental-read-failed)"
        ),
    ]
    assert _SyncServiceFake.run_calls == [(True, False, None, "first")]
    assert _SyncClient.closed == 2
    log_text = (tmp_path / ".research" / "logs" / "research.log").read_text(encoding="utf-8")
    assert "sync_item action=created zotero_key=AAAA1111 citekey=first path=first.md" in log_text
    assert "sync_item action=renamed zotero_key=BBBB2222 citekey=new path=new.md" in log_text
    assert (
        "sync_complete mode=full dry_run=True previous_version=None "
        "library_version=12 state_updated=False"
    ) in log_text


def test_sync_reports_targeted_cursor_as_not_applicable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _SyncServiceFake.reset(
        SyncReport(
            mode="targeted",
            dry_run=False,
            previous_version=None,
            library_version=None,
            state_updated=False,
            items=(),
        )
    )
    monkeypatch.setattr(cli, "ZoteroClient", _SyncClient)
    monkeypatch.setattr(cli, "BetterBibTeXClient", _SyncClient)
    monkeypatch.setattr(cli, "SyncService", _SyncServiceFake)

    result = runner.invoke(
        app,
        ["sync", "--item", "ABCD1234"],
        env={"RESEARCH_VAULT_PATH": str(tmp_path)},
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "Summary: mode=targeted, count=0, version=n/a, incremental-fallback=no"
    ]


def test_sync_reports_domain_error_and_closes_clients(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _SyncServiceFake.reset(SyncError("Zotero is unavailable."))
    _SyncClient.reset()
    monkeypatch.setattr(cli, "ZoteroClient", _SyncClient)
    monkeypatch.setattr(cli, "BetterBibTeXClient", _SyncClient)
    monkeypatch.setattr(cli, "SyncService", _SyncServiceFake)

    result = runner.invoke(app, ["sync"], env={"RESEARCH_VAULT_PATH": str(tmp_path)})

    assert result.exit_code == 1
    assert result.stderr.splitlines() == ["Error: Zotero is unavailable."]
    assert _SyncClient.closed == 2


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


class _UnexpectedSyncClient:
    def __init__(self, _: str) -> None:
        raise AssertionError("sync opened a client before validating flags")


class _SyncClient:
    closed: ClassVar[int] = 0

    def __init__(self, _: str) -> None:
        pass

    @classmethod
    def reset(cls) -> None:
        cls.closed = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        type(self).closed += 1


class _SyncServiceFake:
    report: ClassVar[SyncReport | SyncError]
    run_calls: ClassVar[list[tuple[bool, bool, str | None, str | None]]] = []

    def __init__(self, *_: object) -> None:
        pass

    @classmethod
    def reset(cls, report: SyncReport | SyncError) -> None:
        cls.report = report
        cls.run_calls = []

    def run(
        self,
        *,
        dry_run: bool = False,
        full: bool = False,
        item_key: str | None = None,
        citekey: str | None = None,
    ) -> SyncReport:
        type(self).run_calls.append((dry_run, full, item_key, citekey))
        report = type(self).report
        if isinstance(report, SyncError):
            raise report
        return report


def _create_required_vault_paths(vault_path: Path) -> None:
    (vault_path / "Literature" / "Papers").mkdir(parents=True)
    (vault_path / "System" / "Templates").mkdir(parents=True)
    (vault_path / "System" / "reading-profile.md").write_text("profile\n", encoding="utf-8")
    (vault_path / "System" / "tag-registry.md").write_text("tags\n", encoding="utf-8")
