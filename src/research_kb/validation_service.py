"""Read-only validation for paper notes and their review provenance."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar

import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError as PydanticValidationError

from research_kb.config import Settings
from research_kb.exceptions import ManagedBlockError, MarkdownParseError, ValidationError
from research_kb.markdown_store import MarkdownStore
from research_kb.models import TAG_NAMESPACES, ExtractionMetadata, PaperNote
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
class HumanFieldSnapshot:
    """Protected fields captured immediately before an agent workflow."""

    field_names: ClassVar[tuple[str, ...]] = (
        "human_read_status",
        "human_read_date",
        "human_rating",
        "human_priority",
        "human_relevance",
    )

    human_read_status: str
    human_read_date: date | None
    human_rating: int | None
    human_priority: int | None
    human_relevance: int | None

    @classmethod
    def from_note(cls, note: PaperNote) -> HumanFieldSnapshot:
        """Capture only user-owned state, never AI or Zotero metadata."""
        return cls(
            human_read_status=note.human_read_status,
            human_read_date=note.human_read_date,
            human_rating=note.human_rating,
            human_priority=note.human_priority,
            human_relevance=note.human_relevance,
        )


@dataclass(frozen=True)
class _RawDocument:
    metadata: dict[str, Any]
    body: str


class ValidationService:
    """Validate notes without rewriting paper, cache, or registry files."""

    def __init__(self, settings: Settings, markdown_store: MarkdownStore) -> None:
        self.settings = settings
        self.markdown_store = markdown_store

    def run(
        self,
        citekey: str | None = None,
        *,
        human_baselines: Mapping[str, HumanFieldSnapshot] | None = None,
    ) -> ValidationReport:
        """Validate notes, optionally comparing pre-agent protected-field snapshots."""
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

        for path in paths:
            issues.extend(self._validate_path(path, registry_tags, human_baselines or {}))

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

    def _target_paths(
        self, citekey: str | None
    ) -> tuple[tuple[Path, ...], tuple[ValidationIssue, ...]]:
        if citekey is None:
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
        human_baselines: Mapping[str, HumanFieldSnapshot],
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

        issues.extend(self._review_issues(path, note, raw.body))
        baseline = human_baselines.get(note.citekey)
        if baseline is not None:
            issues.extend(self._human_ownership_issues(path, note, baseline))
        issues.extend(self._tag_issues(path, note, registry_tags))
        issues.extend(self._extraction_issues(path, note))
        return issues

    def _human_ownership_issues(
        self, path: Path, note: PaperNote, baseline: HumanFieldSnapshot
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for field in HumanFieldSnapshot.field_names:
            if getattr(note, field) == getattr(baseline, field):
                continue
            message = f"Protected field {field!r} changed during the agent workflow."
            if field == "human_read_status" and note.human_read_status == "read":
                message += " AI review state must never infer that the user read the paper."
            issues.append(self._issue(path, "human-field-changed", message))
        return issues

    def _review_issues(self, path: Path, note: PaperNote, body: str) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        review_text = self._managed_content(body, "AI_REVIEW")
        has_review_text = review_text is not None and review_text.strip() not in {
            "",
            _NO_REVIEW_PLACEHOLDER,
        }
        has_review = has_review_text or note.ai_review_status in {"reviewed", "outdated"}
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
        elif has_review_text and note.ai_review_status == "not-reviewed":
            issues.append(
                self._issue(
                    path,
                    "review-status-inconsistent",
                    "The managed AI review block has content but status is not-reviewed.",
                )
            )
        return issues

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

    def _extraction_issues(self, path: Path, note: PaperNote) -> list[ValidationIssue]:
        if note.ai_review_scope != "full-text" or note.ai_review_status not in {
            "reviewed",
            "outdated",
        }:
            return []
        cache_path = self.settings.paper_text_dir / path.name
        if not cache_path.is_file():
            return [
                self._issue(
                    path,
                    "full-text-cache-missing",
                    f"Full-text review has no extraction cache at {self._display(cache_path)}.",
                )
            ]

        try:
            metadata = self._read_extraction_metadata(cache_path)
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
        return issues

    def _registry_tags(self) -> frozenset[str]:
        return frozenset(parse_tag_registry(self.settings.tag_registry_path).names)

    @staticmethod
    def _read_raw_document(path: Path) -> _RawDocument:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            raise MarkdownParseError(f"{path}: unable to read note: {error}") from error
        if not text.startswith("---\n"):
            raise MarkdownParseError(f"{path}: frontmatter must start with ---")
        closing = text.find("\n---\n", 4)
        if closing < 0:
            raise MarkdownParseError(f"{path}: frontmatter is missing its closing ---")
        raw = text[4:closing]
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
                    raise MarkdownParseError(
                        f"{path}: duplicate frontmatter field(s): {', '.join(duplicates)}"
                    )
            metadata = yaml.safe_load(raw)
        except yaml.YAMLError as error:
            raise MarkdownParseError(f"{path}: invalid YAML frontmatter: {error}") from error
        if not isinstance(metadata, dict) or not all(isinstance(key, str) for key in metadata):
            raise MarkdownParseError(f"{path}: frontmatter must be a mapping")
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
    def _read_extraction_metadata(path: Path) -> ExtractionMetadata:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            raise ValidationError(f"{path}: unable to read extraction cache: {error}") from error
        if not text.startswith("---\n"):
            raise ValidationError(f"{path}: extraction cache is missing YAML frontmatter")
        closing = text.find("\n---\n", 4)
        if closing < 0:
            raise ValidationError(f"{path}: extraction cache frontmatter is not closed")
        try:
            metadata = yaml.safe_load(text[4:closing])
            return ExtractionMetadata.model_validate(metadata)
        except (yaml.YAMLError, PydanticValidationError) as error:
            raise ValidationError(f"{path}: invalid extraction metadata: {error}") from error

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
        return ValidationIssue(self._display(path), severity, code, message)

    def _display(self, path: Path) -> Path:
        try:
            return path.resolve().relative_to(self.settings.vault_path)
        except (OSError, ValueError):
            return path

    def _safe_note_path(self, path: Path) -> bool:
        try:
            root = self.markdown_store.papers_dir.resolve()
            resolved = path.resolve()
        except OSError:
            return False
        return resolved.parent == root and path.suffix == ".md"
