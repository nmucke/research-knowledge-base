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
    """Which notes the index rewrote, and which references point nowhere."""

    changed: tuple[Path, ...] = ()
    unknown: tuple[tuple[str, str], ...] = ()

    @property
    def stale(self) -> bool:
        return bool(self.changed)


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
        """Every readable paper note in filename order.

        Notes that cannot be parsed are skipped rather than blocking the index;
        ``research validate`` is the command that reports them.
        """
        found: list[tuple[Path, PaperNote]] = []
        for path in sorted(self.markdown_store.papers_dir.glob("*.md")):
            try:
                found.append((path, self.markdown_store.parse(path).note))
            except (ResearchKBError, ValueError, OSError):
                continue
        return tuple(found)

    def index(self, *, dry_run: bool = False) -> ProjectIndexReport:
        """Rewrite every derived block that no longer matches the paper frontmatter."""
        projects = self.projects()
        papers = self.papers()
        known = {project.project_id: project for project in projects}
        changed: list[Path] = []
        unknown: list[tuple[str, str]] = []

        for project in projects:
            linked = [note for _path, note in papers if project.project_id in note.projects]
            path = self.settings.projects_dir / f"{project.project_id}.md"
            if not dry_run:
                ensure_block(path, "PROJECT_PAPERS", "Related papers", "Notes")
            if self._write(path, "PROJECT_PAPERS", _render_papers(linked), dry_run=dry_run):
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
                ensure_block(path, "PROJECTS", "Projects", "AI review")
            content = _render_projects(note.projects, known)
            if self._write(path, "PROJECTS", content, dry_run=dry_run):
                changed.append(path)

        return ProjectIndexReport(changed=tuple(changed), unknown=tuple(unknown))

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

    @staticmethod
    def _write(path: Path, block_name: str, content: str, *, dry_run: bool) -> bool:
        if dry_run:
            return block_content(path, block_name) != content
        return replace_block(path, block_name, content)


def _render_papers(notes: list[PaperNote]) -> str:
    if not notes:
        return NO_PAPERS
    ordered = sorted(notes, key=lambda note: (-(note.year or 0), note.citekey))
    return "\n".join(
        f"- [[{note.citekey}|{note.title}]] — {note.year or 'n.d.'} · "
        f"{note.human_read_status} · {note.ai_recommendation or 'not reviewed'}"
        for note in ordered
    )


def _render_projects(project_ids: tuple[str, ...], known: dict[str, ProjectNote]) -> str:
    if not project_ids:
        return NO_PROJECTS
    lines = []
    for project_id in sorted(project_ids):
        project = known.get(project_id)
        if project is None:
            lines.append(f"- [[{project_id}]] — unknown project")
        else:
            lines.append(f"- [[{project_id}|{project.title}]] — {project.status}")
    return "\n".join(lines)
