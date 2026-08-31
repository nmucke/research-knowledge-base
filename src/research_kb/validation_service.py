"""Read-only validation for paper notes and their review provenance."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError as PydanticValidationError

from research_kb.config import Settings
from research_kb.exceptions import (
    ManagedBlockError,
    MarkdownParseError,
    ResearchKBError,
    ValidationError,
)
from research_kb.markdown_store import MarkdownStore
from research_kb.models import (
    PROJECT_ID,
    TAG_NAMESPACES,
    ExtractionMetadata,
    PaperNote,
    ReviewSnapshot,
)
from research_kb.project_registry import load_projects
from research_kb.project_service import ProjectService
from research_kb.review_contract import validate_managed_review
from research_kb.review_snapshot import ReviewSnapshotStore
from research_kb.tag_registry import parse_tag_registry

_REQUIRED_IDENTITY = ("type", "schema_version", "zotero_key", "citekey", "title")
_MARKER = re.compile(r"<!-- (BEGIN|END) MANAGED:(AI_REVIEW|ZOTERO_ANNOTATIONS) -->")
_REQUIRED_BLOCKS = frozenset(("AI_REVIEW", "ZOTERO_ANNOTATIONS"))
_TAG = re.compile(
    rf"(?P<namespace>{'|'.join(TAG_NAMESPACES)})/[a-z0-9]+(?:-[a-z0-9]+)*\Z"
)
_REVIEW_FIELDS = (
    "ai_review_scope",
    "ai_review_coverage",
    "ai_review_agent",
    "ai_review_date",
    "ai_recommendation",
    "ai_recommendation_reason",
    "ai_recommendation_confidence",
)
_NO_REVIEW_PLACEHOLDER = "No AI review has been generated."
_HUMAN_FIELDS = (
    "human_read_status",
    "human_read_date",
    "human_rating",
    "human_priority",
    "human_relevance",
)
_REVIEW_METADATA_FIELDS = (
    "ai_review_agent",
    "ai_review_model",
    "ai_review_date",
    "ai_recommendation",
    "ai_recommendation_reason",
    "ai_recommendation_confidence",
    "ai_relevance",
)
_CACHE_PAGE_MARKER = re.compile(r"(?m)^<!-- PAGE ([1-9][0-9]*) -->$")


class ValidationSeverity(StrEnum):
    """Severity controls both CLI exit behavior and unknown-tag policy."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class ValidationIssue:
    """One actionable problem associated with a vault-relative path."""

    path: Path
    severity: ValidationSeverity
    code: str
    message: str


@dataclass(frozen=True)
class ValidationReport:
    """Complete deterministic result for a targeted or whole-vault run."""

    checked_count: int
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is ValidationSeverity.ERROR)

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is ValidationSeverity.WARNING)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class _RawDocument:
    metadata: dict[str, Any]
    body: str


class ValidationService:
    """Validate notes without rewriting paper, cache, or registry files."""

    def __init__(self, settings: Settings, markdown_store: MarkdownStore) -> None:
        self.settings = settings
        self.markdown_store = markdown_store
        self.snapshot_store = ReviewSnapshotStore(settings, markdown_store)

    def run(self, citekey: str | None = None) -> ValidationReport:
        """Validate notes and any active pre-agent protected-content snapshots."""
        paths, target_issues = self._target_paths(citekey)
        issues = list(target_issues)
        if citekey is not None and target_issues:
            return ValidationReport(checked_count=0, issues=tuple(target_issues))
        registry_tags: frozenset[str] | None
        try:
            registry_tags = self._registry_tags()
        except ValidationError as error:
            registry_tags = None
            issues.append(
                self._issue(
                    self.settings.tag_registry_path,
                    "tag-registry-invalid",
                    str(error),
                )
            )

        try:
            project_ids = frozenset(
                project.project_id for project in load_projects(self.settings.projects_dir)
            )
        except ValidationError as error:
            project_ids = None
            issues.append(
                self._issue(self.settings.projects_dir, "project-note-invalid", str(error))
            )

        for path in paths:
            issues.extend(self._validate_path(path, registry_tags, project_ids))
        if citekey is None:
            issues.extend(self._project_index_issues())

        ordered = tuple(
            sorted(
                issues,
                key=lambda issue: (
                    str(issue.path),
                    issue.severity.value,
                    issue.code,
                    issue.message,
                ),
            )
        )
        return ValidationReport(checked_count=len(paths), issues=ordered)

    def capture_workflow_snapshot(self, citekey: str) -> Path:
        """Capture protected content immediately before an agent edits a note."""
        return self.snapshot_store.capture(citekey)

    def consume_workflow_snapshot(self, citekey: str) -> None:
        """Consume the baseline after a successful targeted workflow validation."""
        self.snapshot_store.consume(citekey)

    def _target_paths(
        self, citekey: str | None
    ) -> tuple[tuple[Path, ...], tuple[ValidationIssue, ...]]:
        if citekey is None:
            if not self._safe_root(self.markdown_store.papers_dir):
                issue = self._issue(
                    self.markdown_store.papers_dir,
                    "papers-directory-unsafe",
                    "Configured paper-notes directory resolves outside the vault.",
                )
                return (), (issue,)
            if not self.markdown_store.papers_dir.is_dir():
                issue = self._issue(
                    self.markdown_store.papers_dir,
                    "papers-directory-missing",
                    "Configured paper-notes directory does not exist or is not a directory.",
                )
                return (), (issue,)
            try:
                paths = tuple(sorted(self.markdown_store.papers_dir.glob("*.md")))
            except OSError as error:
                issue = self._issue(
                    self.markdown_store.papers_dir,
                    "papers-unreadable",
                    f"Unable to list paper notes: {error}",
                )
                return (), (issue,)
            return paths, ()

        try:
            path = self.markdown_store.note_path(citekey)
        except ValueError as error:
            raise ValidationError(str(error)) from error
        if not path.is_file():
            return (), (
                self._issue(
                    path,
                    "note-not-found",
                    f"No paper note exists for citation key {citekey!r}.",
                ),
            )
        return (path,), ()

    def _validate_path(
        self,
        path: Path,
        registry_tags: frozenset[str] | None,
        project_ids: frozenset[str] | None = None,
    ) -> list[ValidationIssue]:
        if not self._safe_note_path(path):
            return [
                self._issue(
                    path,
                    "note-path-unsafe",
                    "Paper note resolves outside the configured paper-notes directory.",
                )
            ]
        issues: list[ValidationIssue] = []
        try:
            raw = self._read_raw_document(path)
        except MarkdownParseError as error:
            return [self._issue(path, "frontmatter-invalid", str(error))]

        for field in _REQUIRED_IDENTITY:
            value = raw.metadata.get(field)
            if field not in raw.metadata or value is None or (
                isinstance(value, str) and not value.strip()
            ):
                issues.append(
                    self._issue(
                        path,
                        "identity-missing",
                        f"Required identity field {field!r} is missing or empty.",
                    )
                )

        issues.extend(self._managed_block_issues(path, raw.body))

        try:
            note = PaperNote.model_validate(raw.metadata)
        except PydanticValidationError as error:
            for detail in error.errors(include_url=False):
                location = ".".join(str(part) for part in detail["loc"]) or "frontmatter"
                issues.append(
                    self._issue(
                        path,
                        "schema-invalid",
                        f"{location}: {detail['msg']}",
                    )
                )
            return issues

        if path.stem != note.citekey:
            issues.append(
                self._issue(
                    path,
                    "citekey-filename-mismatch",
                    f"Filename {path.stem!r} does not match citekey {note.citekey!r}.",
                )
            )

        has_review = self._has_review(note, raw.body)
        issues.extend(self._review_issues(path, note, raw.body, has_review))
        try:
            baseline = self.snapshot_store.load(note.citekey)
        except ValidationError as error:
            issues.append(self._issue(path, "review-snapshot-invalid", str(error)))
            baseline = None
        if baseline is not None:
            issues.extend(self._human_ownership_issues(path, note, raw.body, baseline))
        issues.extend(self._tag_issues(path, note, registry_tags))
        issues.extend(self._project_issues(path, note, project_ids))
        issues.extend(self._extraction_issues(path, note, has_review))
        return issues

    def _human_ownership_issues(
        self, path: Path, note: PaperNote, body: str, baseline: ReviewSnapshot
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if baseline.zotero_key != note.zotero_key:
            issues.append(
                self._issue(
                    path,
                    "review-snapshot-identity-mismatch",
                    "Review snapshot Zotero identity does not match the paper note.",
                )
            )
        for field in _HUMAN_FIELDS:
            if getattr(note, field) == getattr(baseline, field):
                continue
            message = f"Protected field {field!r} changed during the agent workflow."
            if field == "human_read_status" and note.human_read_status == "read":
                message += " AI review state must never infer that the user read the paper."
            issues.append(self._issue(path, "human-field-changed", message))
        try:
            current = ReviewSnapshot.capture(note, self.snapshot_store.human_notes(body))
        except ValidationError as error:
            issues.append(self._issue(path, "human-notes-invalid", str(error)))
        else:
            if current.human_notes_sha256 != baseline.human_notes_sha256:
                issues.append(
                    self._issue(
                        path,
                        "human-notes-changed",
                        "Protected Human notes content changed during the agent workflow.",
                    )
                )
        return issues

    def _review_issues(
        self, path: Path, note: PaperNote, body: str, has_review: bool
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        review_text = self._managed_content(body, "AI_REVIEW")
        has_review_text = review_text is not None and review_text.strip() not in {
            "",
            _NO_REVIEW_PLACEHOLDER,
        }
        if has_review and not self._present(note.ai_review_agent):
            issues.append(
                self._issue(
                    path,
                    "review-agent-missing",
                    "An AI review exists without an agent identifier.",
                )
            )

        if note.ai_review_status == "reviewed":
            for field in _REVIEW_FIELDS:
                if not self._present(getattr(note, field)):
                    issues.append(
                        self._issue(
                            path,
                            "review-field-missing",
                            f"Reviewed paper is missing {field!r}.",
                        )
                    )
            if not has_review_text:
                issues.append(
                    self._issue(
                        path,
                        "review-content-missing",
                        "ai_review_status is reviewed but the managed AI review block is empty.",
                    )
                )
            elif review_text is not None:
                issues.extend(
                    self._issue(path, violation.code, violation.message)
                    for violation in validate_managed_review(note, review_text)
                )
        elif has_review and note.ai_review_status not in {"reviewed", "outdated"}:
            issues.append(
                self._issue(
                    path,
                    "review-status-inconsistent",
                    "AI review content or metadata exists under a non-review status.",
                )
            )
        return issues

    def _has_review(self, note: PaperNote, body: str) -> bool:
        review_text = self._managed_content(body, "AI_REVIEW")
        has_review_text = review_text is not None and review_text.strip() not in {
            "",
            _NO_REVIEW_PLACEHOLDER,
        }
        has_metadata = any(self._present(getattr(note, field)) for field in _REVIEW_METADATA_FIELDS)
        return bool(
            has_review_text
            or has_metadata
            or note.ai_review_status in {"reviewed", "outdated"}
            or note.ai_review_version > 0
            or note.ai_applied_tags
            or note.ai_suggested_tags
        )

    def _tag_issues(
        self, path: Path, note: PaperNote, registry_tags: frozenset[str] | None
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        groups = (
            ("tags", note.tags),
            ("ai_applied_tags", note.ai_applied_tags),
            ("ai_suggested_tags", note.ai_suggested_tags),
        )
        for field, values in groups:
            duplicates = sorted({tag for tag in values if values.count(tag) > 1})
            for tag in duplicates:
                issues.append(
                    self._issue(path, "tag-duplicate", f"{field} contains duplicate tag {tag!r}.")
                )
            for tag in values:
                if _TAG.fullmatch(tag) is None:
                    reason = "Tags must use a known namespace and lowercase kebab-case."
                    issues.append(
                        self._issue(path, "tag-invalid", f"Invalid {field} tag {tag!r}. {reason}")
                    )

        if len(note.ai_applied_tags) > 5:
            issues.append(
                self._issue(
                    path,
                    "applied-tag-limit",
                    "An AI review may apply at most five existing tags.",
                )
            )
        if len(note.ai_suggested_tags) > 2:
            issues.append(
                self._issue(
                    path,
                    "suggested-tag-limit",
                    "An AI review may suggest at most two new tags.",
                )
            )

        synchronized_suggestions = set(note.ai_suggested_tags) & (
            set(note.tags) | set(note.zotero_tags)
        )
        for tag in sorted(synchronized_suggestions):
            issues.append(
                self._issue(
                    path,
                    "suggested-tag-synchronized",
                    f"Suggested tag {tag!r} is also treated as approved or synchronized.",
                )
            )

        if note.zotero_tag_sync == "synced":
            if note.zotero_tag_sync_date is None:
                issues.append(
                    self._issue(
                        path,
                        "tag-sync-date-missing",
                        "A synchronized tag state must record zotero_tag_sync_date.",
                    )
                )
            missing_from_zotero = tuple(sorted(set(note.tags) - set(note.zotero_tags)))
            if missing_from_zotero:
                issues.append(
                    self._issue(
                        path,
                        "tag-sync-inconsistent",
                        "The note is marked synchronized but approved tag(s) are absent from "
                        f"zotero_tags: {', '.join(missing_from_zotero)}.",
                    )
                )
        elif note.zotero_tag_sync_date is not None:
            issues.append(
                self._issue(
                    path,
                    "tag-sync-date-inconsistent",
                    "An unsynchronized tag state must not retain zotero_tag_sync_date.",
                )
            )

        if registry_tags is not None:
            for tag in note.tags:
                if _TAG.fullmatch(tag) is not None and tag not in registry_tags:
                    severity = ValidationSeverity(self.settings.unknown_tag_policy)
                    issues.append(
                        self._issue(
                            path,
                            "tag-unknown",
                            f"Approved tag {tag!r} is absent from the tag registry.",
                            severity=severity,
                        )
                    )
            for tag in note.ai_applied_tags:
                if _TAG.fullmatch(tag) is not None and tag not in registry_tags:
                    issues.append(
                        self._issue(
                            path,
                            "applied-tag-unknown",
                            f"AI-applied tag {tag!r} is absent from the tag registry.",
                        )
                    )
            for tag in note.ai_suggested_tags:
                if tag in registry_tags:
                    issues.append(
                        self._issue(
                            path,
                            "suggested-tag-known",
                            f"Suggested tag {tag!r} already exists in the tag registry.",
                        )
                    )
        return issues

    def _project_issues(
        self, path: Path, note: PaperNote, project_ids: frozenset[str] | None
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for field, values in (
            ("projects", note.projects),
            ("ai_suggested_projects", note.ai_suggested_projects),
        ):
            for project_id in sorted({item for item in values if values.count(item) > 1}):
                issues.append(
                    self._issue(
                        path,
                        "project-duplicate",
                        f"{field} contains duplicate project {project_id!r}.",
                    )
                )
            for project_id in values:
                if PROJECT_ID.fullmatch(project_id) is None:
                    issues.append(
                        self._issue(
                            path,
                            "project-invalid",
                            f"Invalid {field} entry {project_id!r}. "
                            "Project identifiers use lowercase kebab-case.",
                        )
                    )
                elif project_ids is not None and project_id not in project_ids:
                    issues.append(
                        self._issue(
                            path,
                            "project-unknown",
                            f"{field} references {project_id!r}, "
                            "which has no note in the Projects folder.",
                        )
                    )
        for project_id in note.ai_suggested_projects:
            if project_id in note.projects:
                issues.append(
                    self._issue(
                        path,
                        "project-suggested-approved",
                        f"Project {project_id!r} is already approved; "
                        "remove it from ai_suggested_projects.",
                    )
                )
        return issues

    def _project_index_issues(self) -> list[ValidationIssue]:
        """Report derived project links that no longer match paper frontmatter."""
        try:
            report = ProjectService(self.settings, self.markdown_store).index(dry_run=True)
        except ResearchKBError as error:
            return [self._issue(self.settings.projects_dir, "project-index-unreadable", str(error))]
        return [
            self._issue(
                path,
                "project-index-stale",
                "Derived project links are out of date. Run: research projects index.",
            )
            for path in report.changed
        ]

    def _extraction_issues(
        self, path: Path, note: PaperNote, has_review: bool
    ) -> list[ValidationIssue]:
        if note.ai_review_scope != "full-text" or not has_review:
            return []
        cache_path = self.settings.paper_text_dir / path.name
        if not self._safe_root(self.settings.paper_text_dir):
            return [
                self._issue(
                    cache_path,
                    "extraction-cache-unsafe",
                    "Extraction-cache directory resolves outside the vault.",
                )
            ]
        if not cache_path.is_file():
            return [
                self._issue(
                    path,
                    "full-text-cache-missing",
                    f"Full-text review has no extraction cache at {self._display(cache_path)}.",
                )
            ]
        if not self._safe_child_file(cache_path, self.settings.paper_text_dir):
            return [
                self._issue(
                    cache_path,
                    "extraction-cache-unsafe",
                    "Extraction cache is a symlink or resolves outside its cache directory.",
                )
            ]

        try:
            metadata, cache_body = self._read_extraction_metadata(cache_path)
        except ValidationError as error:
            return [self._issue(cache_path, "extraction-cache-invalid", str(error))]

        issues: list[ValidationIssue] = []
        expected = {
            "citekey": note.citekey,
            "zotero_key": note.zotero_key,
            "attachment_key": note.pdf_attachment_key,
        }
        for field, value in expected.items():
            if getattr(metadata, field) != value:
                issues.append(
                    self._issue(
                        cache_path,
                        "extraction-cache-mismatch",
                        f"Cache {field} does not match the paper note.",
                    )
                )
        if metadata.failed_pages and note.ai_review_coverage == "complete":
            issues.append(
                self._issue(
                    path,
                    "review-coverage-inconsistent",
                    "Extraction recorded failed pages but review coverage is complete.",
                )
            )
        markers = tuple(int(page) for page in _CACHE_PAGE_MARKER.findall(cache_body))
        if markers != tuple(range(1, metadata.pages + 1)):
            issues.append(
                self._issue(
                    cache_path,
                    "extraction-cache-pages-invalid",
                    "Extraction cache page markers do not match its page metadata.",
                )
            )
        without_markers = _CACHE_PAGE_MARKER.sub("", cache_body)
        if not without_markers.strip():
            issues.append(
                self._issue(
                    cache_path,
                    "extraction-cache-empty",
                    "Extraction cache contains no reviewable text.",
                )
            )
        return issues

    def _registry_tags(self) -> frozenset[str]:
        path = self.settings.tag_registry_path
        if not self._safe_child_file(path, path.parent):
            raise ValidationError(
                f"{path}: tag registry is missing, is a symlink, or resolves outside the vault"
            )
        return frozenset(parse_tag_registry(self.settings.tag_registry_path).names)

    @staticmethod
    def _read_raw_document(path: Path) -> _RawDocument:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise MarkdownParseError(f"{path}: unable to read note: {error}") from error
        if not text.startswith("---\n"):
            raise MarkdownParseError(f"{path}: frontmatter must start with ---")
        closing = text.find("\n---\n", 4)
        if closing < 0:
            raise MarkdownParseError(f"{path}: frontmatter is missing its closing ---")
        raw = text[4:closing]
        try:
            metadata = ValidationService._strict_yaml_mapping(raw)
        except ValueError as error:
            raise MarkdownParseError(f"{path}: invalid YAML frontmatter: {error}") from error
        return _RawDocument(metadata, text[closing + 5 :])

    def _managed_block_issues(self, path: Path, body: str) -> list[ValidationIssue]:
        try:
            self._marker_pairs(path, body)
        except ManagedBlockError as error:
            return [self._issue(path, "managed-block-invalid", str(error))]
        return []

    @staticmethod
    def _marker_pairs(path: Path, body: str) -> dict[str, tuple[re.Match[str], re.Match[str]]]:
        active: list[tuple[str, re.Match[str]]] = []
        pairs: dict[str, tuple[re.Match[str], re.Match[str]]] = {}
        for marker in _MARKER.finditer(body):
            kind, name = marker.groups()
            if kind == "BEGIN":
                if name in pairs or any(open_name == name for open_name, _ in active):
                    raise ManagedBlockError(f"{path}: duplicate BEGIN marker for {name}")
                active.append((name, marker))
                continue
            if not active:
                raise ManagedBlockError(f"{path}: END marker for {name} has no BEGIN marker")
            open_name, opening = active.pop()
            if open_name != name:
                raise ManagedBlockError(
                    f"{path}: overlapping managed blocks {open_name} and {name}"
                )
            pairs[name] = (opening, marker)
        if active:
            name, _marker = active[-1]
            raise ManagedBlockError(f"{path}: BEGIN marker for {name} has no END marker")
        missing = _REQUIRED_BLOCKS - set(pairs)
        if missing:
            raise ManagedBlockError(
                f"{path}: missing managed block(s): {', '.join(sorted(missing))}"
            )
        return pairs

    def _managed_content(self, body: str, block_name: str) -> str | None:
        try:
            pairs = self._marker_pairs(Path("paper.md"), body)
        except ManagedBlockError:
            return None
        opening, closing = pairs[block_name]
        return body[opening.end() : closing.start()]

    @staticmethod
    def _read_extraction_metadata(path: Path) -> tuple[ExtractionMetadata, str]:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise ValidationError(f"{path}: unable to read extraction cache: {error}") from error
        if not text.startswith("---\n"):
            raise ValidationError(f"{path}: extraction cache is missing YAML frontmatter")
        closing = text.find("\n---\n", 4)
        if closing < 0:
            raise ValidationError(f"{path}: extraction cache frontmatter is not closed")
        try:
            raw = ValidationService._strict_yaml_mapping(text[4:closing])
            metadata = ExtractionMetadata.model_validate(raw)
        except (ValueError, PydanticValidationError) as error:
            raise ValidationError(f"{path}: invalid extraction metadata: {error}") from error
        return metadata, text[closing + 5 :]

    @staticmethod
    def _strict_yaml_mapping(raw: str) -> dict[str, Any]:
        try:
            node = yaml.compose(raw, Loader=yaml.SafeLoader)
            if isinstance(node, yaml.MappingNode):
                keys = [
                    key.value
                    for key, _value in node.value
                    if isinstance(key, yaml.ScalarNode) and isinstance(key.value, str)
                ]
                duplicates = sorted({key for key in keys if keys.count(key) > 1})
                if duplicates:
                    raise ValueError(f"duplicate field(s): {', '.join(duplicates)}")
            metadata = yaml.safe_load(raw)
        except yaml.YAMLError as error:
            raise ValueError(str(error)) from error
        if not isinstance(metadata, dict) or not all(isinstance(key, str) for key in metadata):
            raise ValueError("frontmatter must be a mapping")
        return metadata

    @staticmethod
    def _present(value: object) -> bool:
        return value is not None and (not isinstance(value, str) or bool(value.strip()))

    def _issue(
        self,
        path: Path,
        code: str,
        message: str,
        *,
        severity: ValidationSeverity = ValidationSeverity.ERROR,
    ) -> ValidationIssue:
        one_line = " ".join(message.split())
        return ValidationIssue(self._display(path), severity, code, one_line)

    def _display(self, path: Path) -> Path:
        try:
            return path.resolve().relative_to(self.settings.vault_path)
        except (OSError, ValueError):
            return path

    def _safe_note_path(self, path: Path) -> bool:
        if not self._safe_root(self.markdown_store.papers_dir):
            return False
        return self._safe_child_file(path, self.markdown_store.papers_dir)

    def _safe_root(self, root: Path) -> bool:
        try:
            resolved = root.resolve()
            vault = self.settings.vault_path.resolve()
        except OSError:
            return False
        return not root.is_symlink() and resolved.is_relative_to(vault)

    def _safe_child_file(self, path: Path, root: Path) -> bool:
        if not self._safe_root(root):
            return False
        try:
            resolved_root = root.resolve()
            resolved = path.resolve()
        except OSError:
            return False
        return (
            not path.is_symlink()
            and path.is_file()
            and resolved.parent == resolved_root
            and path.suffix == ".md"
        )
