"""CLI-level tests for PDF extraction and review-context rendering."""

import re
from pathlib import Path
from typing import ClassVar, Self

import pymupdf
import pytest
from typer.testing import CliRunner

import research_kb.cli as cli
from research_kb.markdown_store import MarkdownStore
from research_kb.models import PaperNote

runner = CliRunner()


class CLIPathResolver:
    path: ClassVar[Path]
    closed: ClassVar[int] = 0

    def __init__(self, base_url: str) -> None:
        del base_url

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        type(self).closed += 1

    def resolve_attachment_path(
        self, attachment_key: str, *, library_type: str, library_id: int
    ) -> Path:
        assert (attachment_key, library_type, library_id) == ("PDFX5678", "user", 0)
        return type(self).path


def _vault(tmp_path: Path) -> None:
    (tmp_path / "System").mkdir()
    (tmp_path / "System" / "reading-profile.md").write_text("profile", encoding="utf-8")
    (tmp_path / "System" / "tag-registry.md").write_text("tags", encoding="utf-8")
    store = MarkdownStore(tmp_path / "Literature" / "Papers")
    store.create(
        PaperNote(
            zotero_key="ABCD1234",
            citekey="doeUseful2026",
            title="Useful paper",
            pdf_attachment_key="PDFX5678",
        )
    )
    pdf = pymupdf.open()
    pdf.new_page().insert_text(
        (72, 72),
        "\n".join(f"A readable synthetic paper line {line}." for line in range(30)),
    )
    CLIPathResolver.path = tmp_path / "paper.pdf"
    pdf.save(CLIPathResolver.path)
    pdf.close()
    CLIPathResolver.closed = 0


def test_extract_command_reports_quality_and_force_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _vault(tmp_path)
    monkeypatch.setattr(cli, "ZoteroClient", CLIPathResolver)
    environment = {"RESEARCH_VAULT_PATH": str(tmp_path)}

    first = runner.invoke(cli.app, ["extract", "doeUseful2026"], env=environment)
    forced = runner.invoke(
        cli.app, ["extract", "doeUseful2026", "--force"], env=environment
    )

    assert first.exit_code == forced.exit_code == 0
    assert first.stdout.startswith("EXTRACTED doeUseful2026 -> ")
    assert "Status: complete" in first.stdout
    assert "Pages: 1" in first.stdout
    assert "Characters per page: 1:" in first.stdout
    assert "Empty pages: none" in first.stdout
    assert "Total characters:" in first.stdout
    assert "Low-text fraction:" in first.stdout
    assert forced.stdout.startswith("EXTRACTED doeUseful2026 -> ")
    assert CLIPathResolver.closed == 2


def test_review_context_auto_extracts_and_prints_spec_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _vault(tmp_path)
    monkeypatch.setattr(cli, "ZoteroClient", CLIPathResolver)

    result = runner.invoke(
        cli.app,
        ["review-context", "doeUseful2026"],
        env={"RESEARCH_VAULT_PATH": str(tmp_path)},
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "Paper note:",
        "Literature/Papers/doeUseful2026.md",
        "",
        "Extracted paper:",
        ".research/paper-text/doeUseful2026.md",
        "",
        "Reading profile:",
        "System/reading-profile.md",
        "",
        "Tag registry:",
        "System/tag-registry.md",
    ]
    assert (tmp_path / ".research" / "paper-text" / "doeUseful2026.md").exists()
    assert (
        tmp_path / ".research" / "review-snapshots" / "doeUseful2026.json"
    ).exists()


def test_extract_domain_error_uses_standard_exit_convention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "ZoteroClient", CLIPathResolver)

    result = runner.invoke(
        cli.app,
        ["extract", "missing2026"],
        env={"RESEARCH_VAULT_PATH": str(tmp_path)},
    )

    assert result.exit_code == 1
    assert result.stderr.startswith("Error: Paper note for 'missing2026' was not found")


def test_extract_help_lists_force_and_review_context_is_registered() -> None:
    extract_help = runner.invoke(cli.app, ["extract", "--help"], color=False)
    root_help = runner.invoke(cli.app, ["--help"], color=False)

    assert extract_help.exit_code == root_help.exit_code == 0
    extract_output = re.sub(r"\x1b\[[0-9;]*m", "", extract_help.output)
    root_output = re.sub(r"\x1b\[[0-9;]*m", "", root_help.output)
    assert "--force" in extract_output
    assert "review-context" in root_output


def test_review_context_paths_are_relative_to_the_repository_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """From a checkout, every reported path must be openable as printed."""
    vault_dir = tmp_path / "Vault"
    vault_dir.mkdir()
    _vault(vault_dir)
    monkeypatch.setattr(cli, "ZoteroClient", CLIPathResolver)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        cli.app,
        ["review-context", "doeUseful2026"],
        env={
            "RESEARCH_VAULT_PATH": str(vault_dir),
            "RESEARCH_STATE_PATH": str(tmp_path / ".research"),
        },
    )

    assert result.exit_code == 0
    for reported in (
        "Vault/Literature/Papers/doeUseful2026.md",
        ".research/paper-text/doeUseful2026.md",
        "Vault/System/reading-profile.md",
        "Vault/System/tag-registry.md",
    ):
        assert reported in result.stdout.splitlines()
        assert (tmp_path / reported).is_file()
