"""One-time, user-directed personalization of a research workspace."""

from __future__ import annotations

import json
import re
import tempfile
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from research_kb.catalog_operations import ProjectCreateRequest, _render_project
from research_kb.config import Settings
from research_kb.models import TAG_NAMESPACES, DomainModel, ProjectNote, TagRegistryEntry
from research_kb.project_registry import parse_project_note
from research_kb.research_operations import _render_registry_additions
from research_kb.tag_registry import parse_tag_registry
from research_kb.vault_transaction import PlannedFileChange, VaultTransaction


class ReadingProfile(DomainModel):
    primary_interests: str = Field(min_length=1)
    valuable_papers: str = ""
    lower_priority_papers: str = ""
    recommendation_policy: str = ""

    @field_validator("primary_interests")
    @classmethod
    def nonempty_interests(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("primary_interests must not be blank")
        return value.strip()


class SetupLibrary(DomainModel):
    """Local Zotero library selection; personal libraries require no account lookup."""

    library_type: Literal["user", "group"] = Field(
        default="user", description="Use user for the local personal library, group for a group."
    )
    library_id: int = Field(
        default=0,
        ge=0,
        description=(
            "For a new personal-library setup, omit this field or use 0: the local API "
            "selects the current user. Do not ask for a numeric Zotero user ID or an API key. "
            "For a group library, supply its positive Zotero group ID. "
            "Preserve any existing workspace library configuration."
        ),
    )

    @model_validator(mode="after")
    def group_requires_id(self) -> SetupLibrary:
        if self.library_type == "group" and self.library_id == 0:
            raise ValueError("a group library requires its positive Zotero group ID")
        return self


class SetupRequest(DomainModel):
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    reading_profile: ReadingProfile | None = None
    tags: tuple[TagRegistryEntry, ...] = ()
    projects: tuple[ProjectCreateRequest, ...] = ()
    library: SetupLibrary | None = None

    @model_validator(mode="after")
    def validate_choices(self) -> SetupRequest:
        if len({tag.name for tag in self.tags}) != len(self.tags):
            raise ValueError("setup tags must be unique")
        for tag in self.tags:
            if (
                re.fullmatch(r"[a-z]+/[a-z0-9]+(?:-[a-z0-9]+)*", tag.name) is None
                or tag.name.split("/", 1)[0] not in TAG_NAMESPACES
                or not tag.definition.strip()
            ):
                raise ValueError("each tag needs a supported namespace/name and a definition")
        if len({project.project_id for project in self.projects}) != len(self.projects):
            raise ValueError("setup project identifiers must be unique")
        for project in self.projects:
            if not project.title.strip() or not project.description.strip() or not project.goals:
                raise ValueError("projects need a title, description, and at least one goal")
            if any(
                not item.strip() for item in (*project.goals, *project.scope_in, *project.scope_out)
            ):
                raise ValueError("project goals and scope entries must not be blank")
        return self


class SetupService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = settings.workspace_path
        self.marker = settings.state_dir / "setup.json"
        manifest = self._read(self.root / ".research-workspace.json")
        if manifest is None or json.loads(manifest).get("workspace_type") != "research-kb":
            raise ValueError("Run research workspace init PATH before personalizing a workspace.")
        self.transactions = VaultTransaction(self.root, settings.recovery_dir)

    def _read(self, path: Path) -> str | None:
        path.relative_to(self.root)
        for component in (path, *path.parents):
            if component.is_symlink():
                raise ValueError("Setup paths must not traverse symlinks.")
            if component == self.root:
                break
        return path.read_text(encoding="utf-8") if path.exists() else None

    def _snapshot(self) -> dict[Path, str | None]:
        paths = [
            self.settings.reading_profile_path,
            self.settings.tag_registry_path,
            self.root / ".env",
            self.marker,
            *sorted(self.settings.projects_dir.glob("*.md")),
        ]
        return {path: self._read(path) for path in paths}

    def _revision(self, snapshot: dict[Path, str | None]) -> str:
        payload = {str(path.relative_to(self.root)): value for path, value in snapshot.items()}
        return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def _request_hash(request: SetupRequest) -> str:
        return sha256(request.model_dump_json().encode()).hexdigest()

    @staticmethod
    def _starter_profile() -> str:
        return files("research_kb").joinpath("assets/vault/System/reading-profile.md").read_text()

    def status(self) -> dict[str, object]:
        snapshot = self._snapshot()
        profile = snapshot[self.settings.reading_profile_path]
        registry = parse_tag_registry(self.settings.tag_registry_path)
        projects = [
            parse_project_note(path)
            for path in snapshot
            if path.parent == self.settings.projects_dir
        ]
        return {
            "completed": snapshot[self.marker] is not None,
            "revision": self._revision(snapshot),
            "reading_profile": {
                "customized": profile is not None and profile != self._starter_profile(),
                "text": profile,
            },
            "tags": [entry.model_dump(mode="json") for entry in registry.entries],
            "projects": [{"project_id": item.project_id, "title": item.title} for item in projects],
            "library": {
                "library_type": self.settings.zotero_library_type,
                "library_id": self.settings.zotero_library_id,
            },
            "papers": sum(1 for _ in self.settings.papers_dir.glob("*.md")),
        }

    def _plan(self, request: SetupRequest) -> tuple[PlannedFileChange, ...]:
        before = self._snapshot()
        if before[self.marker] is not None:
            marker = json.loads(before[self.marker] or "{}")
            if marker.get("request_sha256") == self._request_hash(request):
                return ()
            raise ValueError("Initial setup is already complete. Use normal curation workflows.")
        if self._revision(before) != request.expected_revision:
            raise ValueError("Setup inputs changed. Inspect status and preview the revised plan.")
        after = dict(before)
        if request.reading_profile is not None:
            profile_path = self.settings.reading_profile_path
            if before[profile_path] not in (None, self._starter_profile()):
                raise ValueError(
                    "The reading profile is already customized; omit it to preserve it."
                )
            profile = request.reading_profile
            sections = (
                ("Primary interests", profile.primary_interests),
                ("Especially valuable papers", profile.valuable_papers),
                ("Lower-priority papers", profile.lower_priority_papers),
                ("Recommendation policy", profile.recommendation_policy),
            )
            after[profile_path] = "# Reading profile\n" + "".join(
                f"\n## {title}\n\n{content.strip() or 'No preference stated.'}\n"
                for title, content in sections
            )
        registry_path = self.settings.tag_registry_path
        registry = parse_tag_registry(registry_path)
        known = {tag.name: tag.definition for tag in registry.entries}
        registry_text = before[registry_path] or "# Tag registry\n"
        additions: list[tuple[str, str]] = []
        for tag in request.tags:
            if tag.name.split("/", 1)[0] not in self.settings.allowed_tag_namespaces:
                raise ValueError(f"Tag namespace is disabled: {tag.name}")
            if tag.name in known and known[tag.name] != tag.definition.strip():
                raise ValueError(f"Setup cannot redefine existing tag {tag.name}.")
            if tag.name not in known:
                additions.append((tag.name, tag.definition.strip()))
                known[tag.name] = tag.definition.strip()
        registry_text = _render_registry_additions(registry_text, additions)
        after[registry_path] = registry_text
        with tempfile.TemporaryDirectory(prefix="research-setup-check-") as directory:
            validation_path = Path(directory) / "registry.md"
            validation_path.write_text(registry_text, encoding="utf-8")
            parsed = parse_tag_registry(validation_path)
            if set(parsed.names) != set(known):
                raise ValueError("Tag definitions must not introduce additional registry headings.")
            for project in request.projects:
                path = self.settings.projects_dir / f"{project.project_id}.md"
                if self._read(path) is not None:
                    raise ValueError(f"Setup cannot overwrite project {project.project_id}.")
                if set(project.tags) - set(known):
                    raise ValueError(f"Project {project.project_id} uses unknown tags.")
                note = ProjectNote(
                    project_id=project.project_id,
                    title=project.title,
                    started=project.started,
                    target=project.target,
                    tags=project.tags,
                )
                rendered = _render_project(
                    note, project.description, project.goals, project.scope_in, project.scope_out
                )
                candidate = Path(directory) / f"{project.project_id}.md"
                candidate.write_text(rendered, encoding="utf-8")
                parse_project_note(candidate)
                before[path] = None
                after[path] = rendered
        if request.library is not None:
            path = self.root / ".env"
            matches = (
                request.library.library_type == self.settings.zotero_library_type
                and request.library.library_id == self.settings.zotero_library_id
            )
            if before[path] is not None and not matches:
                raise ValueError("Existing .env is preserved; change its library settings locally.")
            if before[path] is None:
                after[path] = (
                    f"ZOTERO_LIBRARY_TYPE={request.library.library_type}\n"
                    f"ZOTERO_LIBRARY_ID={request.library.library_id}\n"
                )
        after[self.marker] = (
            json.dumps(
                {
                    "schema_version": 1,
                    "request_sha256": self._request_hash(request),
                },
                indent=2,
            )
            + "\n"
        )
        return tuple(PlannedFileChange(path, before[path], value) for path, value in after.items())

    def preview(self, request: SetupRequest) -> dict[str, object]:
        changes = self._plan(request)
        return {
            "revision": request.expected_revision,
            "already_applied": not changes,
            "files": self.transactions.preview(changes),
        }

    def apply(self, request: SetupRequest) -> dict[str, object]:
        changes = self._plan(request)
        if not changes:
            return {"completed": True, "already_applied": True}

        def unchanged(_changes: tuple[PlannedFileChange, ...]) -> None:
            if self._revision(self._snapshot()) != request.expected_revision:
                raise ValueError("Setup inputs changed; nothing was applied.")

        receipt = self.transactions.apply(
            changes, validate=unchanged, metadata={"kind": "workspace-setup"}
        )
        return {"completed": True, "operation_id": receipt.operation_id, "files": receipt.files}
