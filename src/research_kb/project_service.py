"""Derivation of project/paper links from paper-note frontmatter.

Paper frontmatter is the single source of truth.  Both the paper note's
``PROJECTS`` block and the project note's ``PROJECT_PAPERS`` block are generated
from it, so the two sides can never disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from research_kb.config import Settings
from research_kb.exceptions import ResearchKBError
from research_kb.markdown_store import (
    MarkdownStore,
    block_content,
    ensure_block,
    replace_block,
)
from research_kb.models import PaperNote, ProjectNote
from research_kb.project_registry import load_projects

NO_PAPERS = "No papers are linked to this project."
NO_PROJECTS = "No projects are linked to this paper."


@dataclass(frozen=True)
class ProjectIndexReport:
    """Which notes the index rewrote, which references point nowhere, what it skipped."""

    changed: tuple[Path, ...] = ()
    unknown: tuple[tuple[str, str], ...] = ()
    skipped: tuple[Path, ...] = ()


@dataclass(frozen=True)
class ProjectCandidate:
    """One unlinked paper scored against a project's controlled tags."""

    citekey: str
    title: str
    shared_tags: tuple[str, ...]
    ai_relevance: int | None


class ProjectService:
    """Regenerate the derived project links and propose new ones."""

    def __init__(self, settings: Settings, markdown_store: MarkdownStore) -> None:
        self.settings = settings
        self.markdown_store = markdown_store

    def projects(self) -> tuple[ProjectNote, ...]:
        """Every project note in the vault, in identifier order."""
        return load_projects(self.settings.projects_dir)

    def papers(self) -> tuple[tuple[Path, PaperNote], ...]:
        """Every readable paper note in filename order."""
        return self._read_papers()[0]

    def _read_papers(self) -> tuple[tuple[tuple[Path, PaperNote], ...], tuple[Path, ...]]:
        """Every readable paper note, and the notes that could not be parsed.

        A note that cannot be parsed is skipped rather than blocking the index.
        Its links vanish from the derived blocks, so the caller reports it;
        ``research validate`` explains what is actually wrong with it.
        """
        found: list[tuple[Path, PaperNote]] = []
        skipped: list[Path] = []
        for path in sorted(self.markdown_store.papers_dir.glob("*.md")):
            try:
                found.append((path, self.markdown_store.parse(path).note))
            except (ResearchKBError, ValueError, OSError):
                skipped.append(path)
        return tuple(found), tuple(skipped)

    def index(self, *, dry_run: bool = False) -> ProjectIndexReport:
        """Rewrite every derived block that no longer matches the paper frontmatter."""
        projects = self.projects()
        papers, skipped = self._read_papers()
        known = {project.project_id: project for project in projects}
        changed: list[Path] = []
        unknown: list[tuple[str, str]] = []

        for project in projects:
            linked = [note for _path, note in papers if project.project_id in note.projects]
            path = self.settings.projects_dir / f"{project.project_id}.md"
            if not dry_run:
                ensure_block(path, "PROJECT_PAPERS", "Related papers", "Notes")
            if replace_block(path, "PROJECT_PAPERS", _render_papers(linked), dry_run=dry_run):
                changed.append(path)

        for path, note in papers:
            unknown.extend(
                (note.citekey, project_id)
                for project_id in note.projects
                if project_id not in known
            )
            has_block = block_content(path, "PROJECTS") is not None
            if not note.projects and not has_block:
                continue
            if not has_block:
                if dry_run:
                    changed.append(path)
                    continue
                ensure_block(path, "PROJECTS", "Projects", "Zotero annotations")
            content = _render_projects(note.projects)
            if replace_block(path, "PROJECTS", content, dry_run=dry_run):
                changed.append(path)

        return ProjectIndexReport(changed=tuple(changed), unknown=tuple(unknown), skipped=skipped)

    def candidates(self, project_id: str, limit: int = 20) -> tuple[ProjectCandidate, ...]:
        """Rank unlinked papers by controlled-tag overlap for an agent to judge."""
        projects = {project.project_id: project for project in self.projects()}
        if project_id not in projects:
            raise ValueError(f"No project note exists for {project_id!r}.")
        project_tags = set(projects[project_id].tags)
        scored = [
            ProjectCandidate(
                citekey=note.citekey,
                title=note.title,
                shared_tags=tuple(sorted(project_tags.intersection(note.tags))),
                ai_relevance=note.ai_relevance,
            )
            for _path, note in self.papers()
            if project_id not in note.projects
        ]
        scored.sort(
            key=lambda item: (
                -len(item.shared_tags),
                -(item.ai_relevance or 0),
                item.citekey,
            )
        )
        return tuple(scored[:limit])


def _render_papers(notes: list[PaperNote]) -> str:
    if not notes:
        return NO_PAPERS
    return "\n".join(f"- [[{note.citekey}]]" for note in sorted(notes, key=_citekey))


def _render_projects(project_ids: tuple[str, ...]) -> str:
    if not project_ids:
        return NO_PROJECTS
    return "\n".join(f"- [[{project_id}]]" for project_id in sorted(project_ids))


def _citekey(note: PaperNote) -> str:
    return note.citekey
