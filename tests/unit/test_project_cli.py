"""CLI-level tests for the projects commands."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

import research_kb.cli as cli
from research_kb.markdown_store import MarkdownStore, block_content
from research_kb.models import PaperNote

runner = CliRunner()
PROJECT = """---
schema_version: 1
type: project
project_id: turbulence-priors
title: Turbulence priors
status: active
started: 2026-08-31
target:
tags: ["domain/weather"]
---

# Turbulence priors

## Related papers

<!-- BEGIN MANAGED:PROJECT_PAPERS -->

No papers are linked to this project.

<!-- END MANAGED:PROJECT_PAPERS -->

## Notes
"""


def _vault(tmp_path: Path, **updates: object) -> Path:
    projects = tmp_path / "vault" / "Projects"
    projects.mkdir(parents=True)
    (projects / "turbulence-priors.md").write_text(PROJECT, encoding="utf-8")
    store = MarkdownStore(tmp_path / "vault" / "Literature" / "Papers")
    note = PaperNote(zotero_key="ABCD1234", citekey="doeUseful2026", title="Useful paper")
    return store.create(note.model_copy(update=updates))


def _invoke(tmp_path: Path, *args: str) -> object:
    return runner.invoke(cli.app, list(args), env={"RESEARCH_VAULT_PATH": str(tmp_path)})


def test_projects_list_reports_each_project_and_its_link_count(tmp_path: Path) -> None:
    _vault(tmp_path, projects=("turbulence-priors",))

    result = _invoke(tmp_path, "projects", "list")

    assert result.exit_code == 0
    assert "turbulence-priors  [active]  1 paper(s)  Turbulence priors" in result.stdout


def test_projects_list_reports_an_empty_vault(tmp_path: Path) -> None:
    (tmp_path / "vault" / "Literature" / "Papers").mkdir(parents=True)

    result = _invoke(tmp_path, "projects", "list")

    assert result.exit_code == 0
    assert "No project notes exist yet." in result.stdout


def test_projects_index_writes_the_derived_blocks(tmp_path: Path) -> None:
    paper = _vault(tmp_path, projects=("turbulence-priors",))

    result = _invoke(tmp_path, "projects", "index")

    assert result.exit_code == 0
    assert "updated 2 note(s)." in result.stdout
    assert block_content(paper, "PROJECTS") == "- [[turbulence-priors]]"


def test_projects_index_dry_run_reports_without_writing(tmp_path: Path) -> None:
    paper = _vault(tmp_path, projects=("turbulence-priors",))

    result = _invoke(tmp_path, "projects", "index", "--dry-run")

    assert result.exit_code == 0
    assert "would update 2 note(s)." in result.stdout
    assert block_content(paper, "PROJECTS") is None


def test_projects_index_warns_about_an_unknown_project(tmp_path: Path) -> None:
    _vault(tmp_path, projects=("missing-project",))

    result = _invoke(tmp_path, "projects", "index")

    assert result.exit_code == 0
    assert "references unknown project 'missing-project'" in result.output


def test_projects_candidates_ranks_unlinked_papers(tmp_path: Path) -> None:
    _vault(tmp_path, tags=("domain/weather",), ai_relevance=4)

    result = _invoke(tmp_path, "projects", "candidates", "turbulence-priors")

    assert result.exit_code == 0
    assert "doeUseful2026  (relevance 4; domain/weather)  Useful paper" in result.stdout


def test_projects_candidates_reject_an_unknown_project(tmp_path: Path) -> None:
    _vault(tmp_path)

    result = _invoke(tmp_path, "projects", "candidates", "missing-project")

    assert result.exit_code == 1
    assert "No project note exists" in result.output
