"""Tests for project-note parsing and the derived project links."""

from __future__ import annotations

from pathlib import Path

import pytest

from research_kb.config import Settings
from research_kb.exceptions import ValidationError
from research_kb.markdown_store import MarkdownStore, block_content
from research_kb.models import PaperNote
from research_kb.project_registry import load_projects, parse_project_note
from research_kb.project_service import NO_PAPERS, NO_PROJECTS, ProjectService

PROJECT = """---
schema_version: 1
type: project
project_id: {project_id}
title: {title}
status: {status}
started: 2026-08-31
target:
tags: {tags}
---

# {title}

## Description

A project.

## Related papers

<!-- BEGIN MANAGED:PROJECT_PAPERS -->

No papers are linked to this project.

<!-- END MANAGED:PROJECT_PAPERS -->

## Notes
"""


def _settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, research_vault_path=tmp_path)


def _project(
    tmp_path: Path,
    project_id: str = "turbulence-priors",
    *,
    title: str = "Turbulence priors",
    status: str = "active",
    tags: str = "[]",
    filename: str | None = None,
) -> Path:
    directory = tmp_path / "vault" / "Projects"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{filename or project_id}.md"
    path.write_text(
        PROJECT.format(project_id=project_id, title=title, status=status, tags=tags),
        encoding="utf-8",
    )
    return path


def _paper(store: MarkdownStore, citekey: str, **updates: object) -> Path:
    note = PaperNote(zotero_key="ABCD1234", citekey=citekey, title=f"Paper {citekey}")
    path = store.create(note.model_copy(update=updates))
    return path


def _service(tmp_path: Path) -> ProjectService:
    settings = _settings(tmp_path)
    return ProjectService(settings, MarkdownStore(settings.papers_dir))


def test_parse_project_note_reads_frontmatter(tmp_path: Path) -> None:
    path = _project(tmp_path, tags='["domain/fluid-dynamics"]')

    project = parse_project_note(path)

    assert project.project_id == "turbulence-priors"
    assert project.status == "active"
    assert project.tags == ("domain/fluid-dynamics",)


def test_parse_project_note_rejects_a_filename_that_does_not_match(tmp_path: Path) -> None:
    path = _project(tmp_path, filename="renamed")

    with pytest.raises(ValidationError, match="does not match project_id"):
        parse_project_note(path)


def test_parse_project_note_rejects_a_non_slug_identifier(tmp_path: Path) -> None:
    path = _project(tmp_path, project_id="Not A Slug", filename="Not A Slug")

    with pytest.raises(ValidationError, match="invalid project frontmatter"):
        parse_project_note(path)


def test_load_projects_is_empty_without_a_projects_folder(tmp_path: Path) -> None:
    assert load_projects(tmp_path / "vault" / "Projects") == ()


def test_index_writes_both_derived_blocks_from_paper_frontmatter(tmp_path: Path) -> None:
    _project(tmp_path)
    service = _service(tmp_path)
    paper = _paper(
        service.markdown_store,
        "doeUseful2026",
        projects=("turbulence-priors",),
        year=2026,
        ai_recommendation="read",
    )

    report = service.index()

    project = tmp_path / "vault" / "Projects" / "turbulence-priors.md"
    assert block_content(project, "PROJECT_PAPERS") == (
        "- [[doeUseful2026|Paper doeUseful2026]] — 2026 · unread · read"
    )
    assert block_content(paper, "PROJECTS") == "- [[turbulence-priors|Turbulence priors]] — active"
    assert set(report.changed) == {project, paper}
    assert report.unknown == ()


def test_index_is_idempotent_and_dry_run_never_writes(tmp_path: Path) -> None:
    _project(tmp_path)
    service = _service(tmp_path)
    paper = _paper(service.markdown_store, "doeUseful2026", projects=("turbulence-priors",))
    service.index()
    before = paper.read_text(encoding="utf-8")

    assert service.index().changed == ()
    assert service.index(dry_run=True).changed == ()
    assert paper.read_text(encoding="utf-8") == before


def test_dry_run_reports_a_missing_block_without_inserting_it(tmp_path: Path) -> None:
    _project(tmp_path)
    service = _service(tmp_path)
    paper = _paper(service.markdown_store, "doeUseful2026", projects=("turbulence-priors",))

    report = service.index(dry_run=True)

    assert paper in report.changed
    assert block_content(paper, "PROJECTS") is None


def test_unlinking_a_paper_leaves_a_placeholder_block(tmp_path: Path) -> None:
    _project(tmp_path)
    service = _service(tmp_path)
    paper = _paper(service.markdown_store, "doeUseful2026", projects=("turbulence-priors",))
    service.index()
    text = paper.read_text(encoding="utf-8")
    paper.write_text(
        text.replace("projects:\n- turbulence-priors", "projects: []"), encoding="utf-8"
    )

    service.index()

    assert block_content(paper, "PROJECTS") == NO_PROJECTS
    project = tmp_path / "vault" / "Projects" / "turbulence-priors.md"
    assert block_content(project, "PROJECT_PAPERS") == NO_PAPERS


def test_index_reports_a_reference_to_a_project_that_does_not_exist(tmp_path: Path) -> None:
    _project(tmp_path)
    service = _service(tmp_path)
    _paper(service.markdown_store, "doeUseful2026", projects=("missing-project",))

    report = service.index()

    assert report.unknown == (("doeUseful2026", "missing-project"),)


def test_index_skips_a_note_it_cannot_parse(tmp_path: Path) -> None:
    _project(tmp_path)
    service = _service(tmp_path)
    service.markdown_store.papers_dir.mkdir(parents=True, exist_ok=True)
    (service.markdown_store.papers_dir / "broken.md").write_text("not a note", encoding="utf-8")

    assert service.index().changed == ()


def test_candidates_rank_unlinked_papers_by_shared_tags_then_relevance(tmp_path: Path) -> None:
    _project(tmp_path, tags='["domain/weather", "method/diffusion-models"]')
    service = _service(tmp_path)
    _paper(
        service.markdown_store,
        "bothTags2026",
        tags=("domain/weather", "method/diffusion-models"),
    )
    _paper(service.markdown_store, "oneTag2026", tags=("domain/weather",), ai_relevance=5)
    _paper(service.markdown_store, "noTags2026")
    _paper(service.markdown_store, "linked2026", projects=("turbulence-priors",))

    candidates = service.candidates("turbulence-priors")

    assert [candidate.citekey for candidate in candidates] == [
        "bothTags2026",
        "oneTag2026",
        "noTags2026",
    ]
    assert candidates[0].shared_tags == ("domain/weather", "method/diffusion-models")


def test_candidates_reject_an_unknown_project(tmp_path: Path) -> None:
    _project(tmp_path)

    with pytest.raises(ValueError, match="No project note exists"):
        _service(tmp_path).candidates("missing-project")
