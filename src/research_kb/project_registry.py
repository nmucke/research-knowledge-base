"""Reader for the vault's project notes."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from research_kb.exceptions import MarkdownParseError, ValidationError
from research_kb.markdown_store import read_frontmatter
from research_kb.models import ProjectNote

__all__ = ("parse_project_note", "load_projects")


def parse_project_note(path: Path) -> ProjectNote:
    """Validate one project note without modifying it."""
    try:
        metadata, _body = read_frontmatter(path)
    except (MarkdownParseError, OSError, ValueError) as error:
        raise ValidationError(f"{path}: unreadable project note: {error}") from error
    try:
        project = ProjectNote.model_validate(metadata)
    except PydanticValidationError as error:
        raise ValidationError(f"{path}: invalid project frontmatter: {error}") from error
    if path.stem != project.project_id:
        raise ValidationError(
            f"{path}: filename {path.stem!r} does not match project_id {project.project_id!r}."
        )
    return project


def load_projects(projects_dir: Path) -> tuple[ProjectNote, ...]:
    """Load every project note in identifier order, or none when the folder is absent."""
    if not projects_dir.is_dir():
        return ()
    return tuple(
        sorted(
            (parse_project_note(path) for path in projects_dir.glob("*.md")),
            key=lambda project: project.project_id,
        )
    )
