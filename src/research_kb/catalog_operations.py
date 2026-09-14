"""Proposal and trusted-approval operations for projects and controlled tags."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from research_kb.config import Settings
from research_kb.exceptions import ValidationError
from research_kb.markdown_store import MarkdownStore, read_frontmatter
from research_kb.models import PROJECT_ID, PaperNote, ProjectNote
from research_kb.project_registry import parse_project_note
from research_kb.review_snapshot import ReviewSnapshotStore
from research_kb.tag_registry import parse_tag_registry
from research_kb.validation_service import ValidationService
from research_kb.vault_transaction import (
    FileDiff,
    PlannedFileChange,
    TransactionResult,
    VaultTransaction,
)

CatalogOperation = Literal[
    "project-create", "project-update", "project-archive", "tag-rename", "tag-merge"
]
_ID = re.compile(r"[a-f0-9]{32}\Z")


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("rationale", check_fields=False)
    @classmethod
    def rationale_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("rationale must not be blank")
        return value.strip()


class ProjectCreateRequest(CatalogModel):
    operation: Literal["project-create"] = "project-create"
    project_id: str
    title: str
    description: str
    goals: tuple[str, ...]
    scope_in: tuple[str, ...]
    scope_out: tuple[str, ...]
    tags: tuple[str, ...] = ()
    started: date | None = None
    target: date | None = None
    rationale: str

    @field_validator("project_id")
    @classmethod
    def valid_project_id(cls, value: str) -> str:
        if PROJECT_ID.fullmatch(value) is None:
            raise ValueError("project_id must be lowercase kebab-case")
        return value


class ProjectUpdateRequest(CatalogModel):
    operation: Literal["project-update"] = "project-update"
    project_id: str
    title: str | None = None
    description: str | None = None
    goals: tuple[str, ...] | None = None
    scope_in: tuple[str, ...] | None = None
    scope_out: tuple[str, ...] | None = None
    tags: tuple[str, ...] | None = None
    status: Literal["active", "paused", "done"] | None = None
    target: date | None = None
    set_target: bool = False
    rationale: str
    reassessment: str | None = None

    @field_validator("project_id")
    @classmethod
    def valid_project_id(cls, value: str) -> str:
        if PROJECT_ID.fullmatch(value) is None:
            raise ValueError("project_id must be lowercase kebab-case")
        return value

    @model_validator(mode="after")
    def has_update(self) -> ProjectUpdateRequest:
        fields = (
            self.title,
            self.description,
            self.goals,
            self.scope_in,
            self.scope_out,
            self.tags,
            self.status,
        )
        if all(value is None for value in fields) and not self.set_target:
            raise ValueError("project update must supply at least one field")
        return self


class ProjectArchiveRequest(CatalogModel):
    operation: Literal["project-archive"] = "project-archive"
    project_id: str
    rationale: str
    reassessment: str

    @field_validator("project_id")
    @classmethod
    def valid_project_id(cls, value: str) -> str:
        if PROJECT_ID.fullmatch(value) is None:
            raise ValueError("project_id must be lowercase kebab-case")
        return value


class TagRenameRequest(CatalogModel):
    operation: Literal["tag-rename"] = "tag-rename"
    old_tag: str
    new_tag: str
    definition: str | None = None
    rationale: str


class TagMergeRequest(CatalogModel):
    operation: Literal["tag-merge"] = "tag-merge"
    source_tags: tuple[str, ...]
    target_tag: str
    rationale: str

    @field_validator("source_tags")
    @classmethod
    def sources_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(value) != len(set(value)):
            raise ValueError("source_tags must be nonempty and unique")
        return value


CatalogRequest = Annotated[
    ProjectCreateRequest
    | ProjectUpdateRequest
    | ProjectArchiveRequest
    | TagRenameRequest
    | TagMergeRequest,
    Field(discriminator="operation"),
]
CATALOG_REQUEST_ADAPTER: TypeAdapter[CatalogRequest] = TypeAdapter(CatalogRequest)


class CatalogProposal(CatalogModel):
    proposal_id: str
    status: Literal["pending", "applied", "rejected", "superseded"] = "pending"
    request: CatalogRequest
    expected_revisions: dict[str, str | None]
    created_at: datetime
    operation_id: str | None = None


class CatalogPreview(CatalogModel):
    proposal_id: str
    operation: CatalogOperation
    files: tuple[FileDiff, ...]


class CatalogOperations:
    """Agent-safe catalog proposal creation and read-only preview."""

    def __init__(self, settings: Settings, store: MarkdownStore | None = None) -> None:
        self.settings = settings
        self.store = store or MarkdownStore(settings.papers_dir)
        self._proposals = settings.state_dir / "operations" / "catalog-proposals"
        _safe_directory(self._proposals, settings.workspace_path, create=True)
        self.transactions = VaultTransaction(settings.workspace_path, settings.recovery_dir)

    def propose(self, request: CatalogRequest) -> CatalogProposal:
        changes = self._plan(request)
        self.transactions.preview(changes)
        self._validate_changes(changes)
        proposal = CatalogProposal(
            proposal_id=uuid4().hex,
            request=request,
            expected_revisions={
                str(change.path.relative_to(self.settings.workspace_path)): (
                    _revision(change.before) if change.before is not None else None
                )
                for change in changes
            },
            created_at=datetime.now().astimezone(),
        )
        proposal_path = self._proposals / f"{proposal.proposal_id}.json"
        proposal_text = (
            json.dumps(proposal.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        )
        mirror_path = (
            self.settings.obsidian_vault_path / "System/Proposals" / (f"{proposal.proposal_id}.md")
        )
        self.transactions.apply(
            (
                PlannedFileChange(proposal_path, None, proposal_text),
                PlannedFileChange(mirror_path, None, _render_proposal_mirror(proposal)),
            )
        )
        return proposal

    def preview(self, proposal_id: str) -> CatalogPreview:
        proposal = self._load(proposal_id)
        changes = self._plan(proposal.request)
        self._check_revisions(proposal, changes)
        self._validate_changes(changes)
        return CatalogPreview(
            proposal_id=proposal.proposal_id,
            operation=proposal.request.operation,
            files=self.transactions.preview(changes),
        )

    def _load(self, proposal_id: str) -> CatalogProposal:
        if _ID.fullmatch(proposal_id) is None:
            raise ValidationError("invalid catalog proposal identifier")
        path = self._proposals / f"{proposal_id}.json"
        _safe_file(path, self.settings.workspace_path)
        try:
            return CatalogProposal.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValidationError(f"invalid catalog proposal {proposal_id}: {error}") from error

    def _check_revisions(
        self, proposal: CatalogProposal, changes: tuple[PlannedFileChange, ...]
    ) -> None:
        current = {
            str(change.path.relative_to(self.settings.workspace_path)): (
                _revision(change.before) if change.before is not None else None
            )
            for change in changes
        }
        if current != proposal.expected_revisions:
            raise ValidationError("catalog proposal is stale; create a new proposal")

    def _plan(self, request: CatalogRequest) -> tuple[PlannedFileChange, ...]:
        if isinstance(request, ProjectCreateRequest):
            changes = self._plan_project_create(request)
        elif isinstance(request, ProjectUpdateRequest | ProjectArchiveRequest):
            changes = self._plan_project_change(request)
        else:
            return self._plan_tags(request)
        return self._augment_project_index(changes)

    def _augment_project_index(
        self, changes: tuple[PlannedFileChange, ...]
    ) -> tuple[PlannedFileChange, ...]:
        from research_kb.project_service import ProjectService

        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory).resolve()
            staged_settings = _stage_workspace(self.settings, stage)
            _apply_to_stage(changes, self.settings.workspace_path, stage)
            staged_store = MarkdownStore(staged_settings.papers_dir)
            ProjectService(staged_settings, staged_store).index()
            combined = {change.path: change for change in changes}
            for staged in staged_settings.obsidian_vault_path.rglob("*.md"):
                real = self.settings.workspace_path / staged.relative_to(stage)
                before = real.read_text(encoding="utf-8") if real.exists() else None
                after = staged.read_text(encoding="utf-8")
                if before != after:
                    combined[real] = PlannedFileChange(real, before, after)
            return tuple(combined[path] for path in sorted(combined, key=str))

    def _validate_tags(self, tags: tuple[str, ...]) -> None:
        known = set(parse_tag_registry(self.settings.tag_registry_path).names)
        unknown = set(tags) - known
        if unknown:
            raise ValidationError(f"unknown project tags: {', '.join(sorted(unknown))}")

    def _plan_project_create(self, request: ProjectCreateRequest) -> tuple[PlannedFileChange, ...]:
        self._validate_tags(request.tags)
        note = ProjectNote(
            project_id=request.project_id,
            title=request.title,
            started=request.started,
            target=request.target,
            tags=request.tags,
        )
        path = self.settings.projects_dir / f"{request.project_id}.md"
        if path.exists():
            raise ValidationError(f"project already exists: {request.project_id}")
        after = _render_project(
            note, request.description, request.goals, request.scope_in, request.scope_out
        )
        return (PlannedFileChange(path, None, after),)

    def _plan_project_change(
        self, request: ProjectUpdateRequest | ProjectArchiveRequest
    ) -> tuple[PlannedFileChange, ...]:
        path = self.settings.projects_dir / f"{request.project_id}.md"
        before = path.read_text(encoding="utf-8")
        project = parse_project_note(path)
        metadata, body = read_frontmatter(path)
        if isinstance(request, ProjectArchiveRequest):
            metadata["status"] = "archived"
        else:
            if request.tags is not None:
                self._validate_tags(request.tags)
            for name in ("title", "tags", "status"):
                value = getattr(request, name)
                if value is not None:
                    metadata[name] = value
            if request.title is not None:
                body, count = re.subn(r"(?m)^# .+$", f"# {request.title.strip()}", body, count=1)
                if count != 1:
                    raise ValidationError("project note is missing its title heading")
            if request.set_target:
                metadata["target"] = request.target
            body = _update_project_sections(body, request)
        ProjectNote.model_validate(metadata)
        if metadata["project_id"] != project.project_id:
            raise ValidationError("project_id is immutable")
        after = _dump_markdown(metadata, body)
        return (PlannedFileChange(path, before, after),)

    def _plan_tags(
        self, request: TagRenameRequest | TagMergeRequest
    ) -> tuple[PlannedFileChange, ...]:
        registry_path = self.settings.tag_registry_path
        registry_before = registry_path.read_text(encoding="utf-8")
        registry = parse_tag_registry(registry_path)
        definitions = {entry.name: entry.definition for entry in registry.entries}
        existing_aliases = _load_aliases(self.settings)
        if isinstance(request, TagRenameRequest):
            old_tag = _resolve_aliases(existing_aliases, request.old_tag)
            if old_tag not in definitions or request.new_tag in definitions:
                raise ValidationError("rename requires an existing source and a new target tag")
            replacements = {old_tag: request.new_tag}
            definitions[request.new_tag] = request.definition or definitions[old_tag]
        else:
            source_tags = tuple(
                _resolve_aliases(existing_aliases, source) for source in request.source_tags
            )
            target_tag = _resolve_aliases(existing_aliases, request.target_tag)
            missing = set(source_tags + (target_tag,)) - set(definitions)
            if missing or target_tag in source_tags or len(source_tags) != len(set(source_tags)):
                raise ValidationError("merge requires distinct existing source and target tags")
            replacements = {source: target_tag for source in source_tags}
        replacements = {
            source: _resolve_aliases(existing_aliases, target)
            for source, target in replacements.items()
        }
        registry_after = _render_registry_aliases(registry_before, definitions, replacements)
        changes: list[PlannedFileChange] = [
            PlannedFileChange(registry_path, registry_before, registry_after)
        ]
        for path in sorted(self.settings.papers_dir.glob("*.md")):
            document = self.store.parse(path)
            tags = tuple(sorted({replacements.get(tag, tag) for tag in document.note.tags}))
            if tags != document.note.tags:
                before = path.read_text(encoding="utf-8")
                changes.append(
                    PlannedFileChange(
                        path,
                        before,
                        _replace_metadata(
                            before,
                            {
                                "tags": tags,
                                "zotero_tag_sync": "not-synced",
                                "zotero_tag_sync_date": None,
                            },
                        ),
                    )
                )
        for path in sorted(self.settings.projects_dir.glob("*.md")):
            metadata, body = read_frontmatter(path)
            tags = tuple(metadata.get("tags", ()))
            updated = tuple(sorted({replacements.get(tag, tag) for tag in tags}))
            if updated != tags:
                before = path.read_text(encoding="utf-8")
                changes.append(
                    PlannedFileChange(
                        path, before, _dump_markdown({**metadata, "tags": updated}, body)
                    )
                )
        aliases_path = self.settings.state_dir / "catalog" / "tag-aliases.json"
        aliases_before = aliases_path.read_text(encoding="utf-8") if aliases_path.exists() else None
        alias_values = existing_aliases
        alias_values.update(replacements)
        for alias in alias_values:
            _resolve_aliases(alias_values, alias)
        aliases = {"schema_version": 1, "aliases": alias_values}
        changes.append(
            PlannedFileChange(
                aliases_path, aliases_before, json.dumps(aliases, indent=2, sort_keys=True) + "\n"
            )
        )
        changes.extend(self._snapshot_retirements(tuple(changes)))
        return tuple(changes)

    def _snapshot_retirements(
        self, changes: tuple[PlannedFileChange, ...]
    ) -> tuple[PlannedFileChange, ...]:
        """Retire verified baselines made obsolete by approved catalog changes."""
        snapshots = ReviewSnapshotStore(self.settings, self.store)
        retirements: list[PlannedFileChange] = []
        for change in changes:
            if change.path.parent != self.settings.papers_dir or change.before is None:
                continue
            citekey = change.path.stem
            baseline = snapshots.load(citekey)
            if baseline is None:
                continue
            document = self.store.parse(change.path)
            current = type(baseline).capture(document.note, snapshots.human_notes(document.body))
            same_available_baseline = (
                baseline.zotero_key == current.zotero_key
                and baseline.citekey == current.citekey
                and baseline.human_read_status == current.human_read_status
                and baseline.human_read_date == current.human_read_date
                and baseline.human_rating == current.human_rating
                and baseline.human_priority == current.human_priority
                and baseline.human_relevance == current.human_relevance
                and baseline.human_notes_sha256 == current.human_notes_sha256
                and (
                    baseline.protected_frontmatter_sha256 is None
                    or baseline.protected_frontmatter_sha256 == current.protected_frontmatter_sha256
                )
            )
            if not same_available_baseline:
                raise ValidationError(
                    f"active review snapshot for {citekey} no longer matches protected content"
                )
            snapshot_path = snapshots.path(citekey)
            retirements.append(
                PlannedFileChange(
                    snapshot_path,
                    snapshot_path.read_text(encoding="utf-8"),
                    None,
                )
            )
        return tuple(retirements)

    def _validate_changes(self, changes: tuple[PlannedFileChange, ...]) -> None:
        """Validate the complete staged vault before the first real write."""
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory).resolve()
            staged_settings = _stage_workspace(self.settings, stage)
            _apply_to_stage(changes, self.settings.workspace_path, stage)
            staged_store = MarkdownStore(staged_settings.papers_dir)
            parse_tag_registry(staged_settings.tag_registry_path)
            for project in staged_settings.projects_dir.glob("*.md"):
                parse_project_note(project)
            report = ValidationService(staged_settings, staged_store).run()
            if not report.ok:
                details = "; ".join(f"{issue.code}: {issue.message}" for issue in report.errors)
                raise ValidationError(f"staged catalog changes failed validation: {details}")


class CatalogApprovalService:
    """Trusted user-facing boundary that applies a previously stored proposal."""

    def __init__(self, operations: CatalogOperations) -> None:
        self.operations = operations

    def apply(self, proposal_id: str) -> TransactionResult:
        proposal = self.operations._load(proposal_id)
        if proposal.status != "pending":
            raise ValidationError(f"catalog proposal is already {proposal.status}")
        changes = self.operations._plan(proposal.request)
        self.operations._check_revisions(proposal, changes)
        proposal_path = self.operations._proposals / f"{proposal.proposal_id}.json"
        mirror_path = (
            self.operations.settings.obsidian_vault_path
            / "System/Proposals"
            / (f"{proposal.proposal_id}.md")
        )
        _safe_file(mirror_path, self.operations.settings.workspace_path)
        proposal_before = proposal_path.read_text(encoding="utf-8")
        mirror_before = mirror_path.read_text(encoding="utf-8")
        applied = proposal.model_copy(update={"status": "applied"})
        changes = changes + (
            PlannedFileChange(
                proposal_path,
                proposal_before,
                json.dumps(applied.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            ),
            PlannedFileChange(mirror_path, mirror_before, _render_proposal_mirror(applied)),
        )
        result = self.operations.transactions.apply(
            changes, validate=self.operations._validate_changes
        )
        return result

    def reject(self, proposal_id: str) -> TransactionResult:
        """Persist an explicit user rejection and remove it from pending views."""
        proposal = self.operations._load(proposal_id)
        if proposal.status != "pending":
            raise ValidationError(f"catalog proposal is already {proposal.status}")
        proposal_path = self.operations._proposals / f"{proposal.proposal_id}.json"
        mirror_path = (
            self.operations.settings.obsidian_vault_path
            / "System/Proposals"
            / f"{proposal.proposal_id}.md"
        )
        _safe_file(proposal_path, self.operations.settings.workspace_path)
        _safe_file(mirror_path, self.operations.settings.workspace_path)
        rejected = proposal.model_copy(update={"status": "rejected"})
        changes = (
            PlannedFileChange(
                proposal_path,
                proposal_path.read_text(encoding="utf-8"),
                json.dumps(rejected.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            ),
            PlannedFileChange(
                mirror_path,
                mirror_path.read_text(encoding="utf-8"),
                _render_proposal_mirror(rejected),
            ),
        )
        return self.operations.transactions.apply(changes)


def resolve_tag_alias(settings: Settings, tag: str) -> str:
    """Resolve durable catalog aliases for validation and search consumers."""
    return _resolve_aliases(_load_aliases(settings), tag)


def _resolve_aliases(aliases: dict[str, str], tag: str) -> str:
    current = tag
    visited: set[str] = set()
    while current in aliases:
        if current in visited or not isinstance(aliases[current], str):
            raise ValidationError("tag alias metadata contains a cycle or invalid target")
        visited.add(current)
        current = aliases[current]
    return current


def _load_aliases(settings: Settings) -> dict[str, str]:
    path = settings.state_dir / "catalog" / "tag-aliases.json"
    if not path.exists():
        return {}
    _safe_file(path, settings.workspace_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        aliases = raw["aliases"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValidationError(f"invalid tag alias metadata: {error}") from error
    if not isinstance(aliases, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in aliases.items()
    ):
        raise ValidationError("invalid tag alias metadata")
    return dict(aliases)


def _render_proposal_mirror(proposal: CatalogProposal) -> str:
    metadata = {
        "schema_version": 1,
        "type": "catalog-proposal",
        "proposal_id": proposal.proposal_id,
        "status": proposal.status,
        "operation": proposal.request.operation,
        "pending_count": 1 if proposal.status == "pending" else 0,
    }
    rationale = proposal.request.rationale
    body = (
        f"\n# Catalog proposal {proposal.proposal_id}\n\n## Rationale\n\n{rationale}\n\n"
        f"## Review\n\nRun `research catalog preview {proposal.proposal_id}` to inspect the diff.\n"
    )
    return _dump_markdown(metadata, body)


def _revision(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _dump_markdown(metadata: dict[str, object], body: str) -> str:
    dumped = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).rstrip("\n")
    return f"---\n{dumped}\n---\n{body}"


def _replace_metadata(text: str, updates: dict[str, object]) -> str:
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ValidationError("invalid Markdown frontmatter")
    closing = text.index("\n---\n", 4)
    metadata = yaml.safe_load(text[4:closing])
    if not isinstance(metadata, dict):
        raise ValidationError("invalid Markdown frontmatter")
    merged = {**metadata, **updates}
    PaperNote.model_validate(merged)
    return _dump_markdown(merged, text[closing + 5 :])


def _metadata_from_text(text: str, model: type[PaperNote] | type[ProjectNote]) -> None:
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ValidationError("invalid Markdown frontmatter")
    closing = text.index("\n---\n", 4)
    metadata = yaml.safe_load(text[4:closing])
    model.model_validate(metadata)


def _render_project(
    project: ProjectNote,
    description: str,
    goals: tuple[str, ...],
    scope_in: tuple[str, ...],
    scope_out: tuple[str, ...],
) -> str:
    body = (
        f"\n# {project.title}\n\n## Description\n\n{description.strip()}\n\n"
        f"## Goals\n\n{_bullets(goals)}\n\n## Scope\n\n### In scope\n\n{_bullets(scope_in)}\n\n"
        f"### Out of scope\n\n{_bullets(scope_out)}\n\n## Related papers\n\n"
        "<!-- BEGIN MANAGED:PROJECT_PAPERS -->\n\nNo papers are linked to this project.\n\n"
        "<!-- END MANAGED:PROJECT_PAPERS -->\n\n## Notes\n"
    )
    return _dump_markdown(project.model_dump(mode="json"), body)


def _bullets(items: tuple[str, ...]) -> str:
    return "\n".join(f"- {item.strip()}" for item in items)


def _replace_section(body: str, heading: str, content: str, next_heading: str) -> str:
    pattern = re.compile(
        rf"(?ms)(^## {re.escape(heading)}\s*\n).*?(?=^## {re.escape(next_heading)}\s*$)"
    )
    replaced, count = pattern.subn(rf"\1\n{content.strip()}\n\n", body, count=1)
    if count != 1:
        raise ValidationError(f"project note is missing ## {heading}")
    return replaced


def _update_project_sections(body: str, request: ProjectUpdateRequest) -> str:
    if request.description is not None:
        body = _replace_section(body, "Description", request.description, "Goals")
    if request.goals is not None:
        body = _replace_section(body, "Goals", _bullets(request.goals), "Scope")
    if request.scope_in is not None:
        pattern = re.compile(r"(?ms)(^### In scope\s*\n).*?(?=^### Out of scope\s*$)")
        body, count = pattern.subn(rf"\1\n{_bullets(request.scope_in)}\n\n", body, count=1)
        if count != 1:
            raise ValidationError("project note is missing ### In scope")
    if request.scope_out is not None:
        pattern = re.compile(r"(?ms)(^### Out of scope\s*\n).*?(?=^## Related papers\s*$)")
        body, count = pattern.subn(rf"\1\n{_bullets(request.scope_out)}\n\n", body, count=1)
        if count != 1:
            raise ValidationError("project note is missing ### Out of scope")
    return body


def _render_registry_aliases(
    text: str, definitions: dict[str, str], replacements: dict[str, str]
) -> str:
    rendered = text.rstrip() + "\n"
    for old, new in replacements.items():
        heading = f"### `{old}`"
        pattern = re.compile(rf"(?ms)({re.escape(heading)}\s*\n).*?(?=^#{{1,6}}\s|\Z)")
        alias_definition = f"Deprecated alias of `{new}`. {definitions[old]}"
        rendered, count = pattern.subn(rf"\1\n{alias_definition}\n\n", rendered, count=1)
        if count != 1:
            raise ValidationError(f"tag registry entry is missing: {old}")
    existing = set(parse_tag_registry_from_text(rendered))
    for new in sorted(set(replacements.values()) - existing):
        rendered += f"\n### `{new}`\n\n{definitions[new]}\n"
    return rendered


def parse_tag_registry_from_text(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"(?m)^### `([^`]+)`\s*$", text))


def _safe_directory(path: Path, workspace: Path, *, create: bool) -> Path:
    root = workspace.resolve()
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise ValidationError(f"unsafe symlinked catalog path: {candidate}")
        if candidate.resolve() == root:
            break
    if create:
        path.mkdir(parents=True, exist_ok=True)
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValidationError("catalog state path is outside workspace")
    return resolved


def _safe_file(path: Path, workspace: Path) -> Path:
    _safe_directory(path.parent, workspace, create=False)
    if path.is_symlink() or not path.is_file():
        raise ValidationError(f"unsafe catalog state file: {path}")
    return path


def _copy_vault_without_symlinks(source: Path, destination: Path) -> None:
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ValidationError(f"unsafe symlink in vault: {path}")
    shutil.copytree(source, destination)


def _stage_workspace(settings: Settings, stage: Path) -> Settings:
    """Copy validation inputs while preserving the configured workspace layout."""
    relative_vault = settings.obsidian_vault_path.relative_to(settings.workspace_path)
    _copy_vault_without_symlinks(settings.obsidian_vault_path, stage / relative_vault)
    manifest = settings.workspace_path / ".research-workspace.json"
    if manifest.is_file():
        (stage / ".research-workspace.json").write_text(
            manifest.read_text(encoding="utf-8"), encoding="utf-8"
        )
    staged = Settings(  # type: ignore[call-arg]
        _env_file=None,
        research_vault_path=stage,
        research_obsidian_dir=relative_vault,
    )
    if settings.paper_text_dir.is_dir():
        for evidence in settings.paper_text_dir.rglob("*"):
            if evidence.is_symlink():
                raise ValidationError(f"unsafe symlink in extraction cache: {evidence}")
        shutil.copytree(settings.paper_text_dir, staged.paper_text_dir, dirs_exist_ok=True)
    return staged


def _apply_to_stage(changes: tuple[PlannedFileChange, ...], workspace: Path, stage: Path) -> None:
    for change in changes:
        relative = change.path.relative_to(workspace)
        if relative.parts[0] != "vault":
            continue
        target = stage / relative
        if change.after is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(change.after, encoding="utf-8")
