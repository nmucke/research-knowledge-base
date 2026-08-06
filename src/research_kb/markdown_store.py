"""Safe, deliberately narrow storage operations for paper Markdown notes."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError as PydanticValidationError

from research_kb.exceptions import ManagedBlockError, MarkdownParseError
from research_kb.models import PaperNote

_CITEKEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._$^-]*\Z")
_MARKER = re.compile(r"<!-- (BEGIN|END) MANAGED:(AI_REVIEW|ZOTERO_ANNOTATIONS) -->")
_BLOCKS = frozenset(("AI_REVIEW", "ZOTERO_ANNOTATIONS"))

# These are copied from Zotero (or resolved from a Zotero attachment).  Keeping
# this allow-list here makes it impossible for synchronisation to overwrite the
# human or AI-owned portion of frontmatter by accident.
_ZOTERO_FIELDS = frozenset(
    (
        "zotero_key",
        "zotero_version",
        "zotero_server_id",
        "title",
        "authors",
        "year",
        "publication",
        "volume",
        "issue",
        "pages",
        "doi",
        "url",
        "abstract",
        "zotero_collections",
        "zotero_tags",
        "zotero_uri",
        "pdf_attachment_key",
        "pdf_uri",
        "date_added",
        "date_modified",
        "zotero_missing",
    )
)
_AI_FIELDS = frozenset(
    (
        "ai_review_status",
        "ai_review_scope",
        "ai_review_coverage",
        "ai_review_agent",
        "ai_review_model",
        "ai_review_date",
        "ai_review_version",
        "ai_recommendation",
        "ai_recommendation_reason",
        "ai_relevance",
        "ai_recommendation_confidence",
        "ai_applied_tags",
        "ai_suggested_tags",
    )
)
_TAG_SYNC_FIELDS = frozenset(
    (
        "zotero_version",
        "zotero_tags",
        "zotero_tag_sync",
        "zotero_tag_sync_date",
    )
)


@dataclass(frozen=True)
class PaperDocument:
    """A validated note and the Markdown following its YAML frontmatter."""

    note: PaperNote
    body: str


class MarkdownStore:
    """Perform conservative, atomic changes beneath one papers directory."""

    def __init__(self, papers_dir: Path) -> None:
        self.papers_dir = papers_dir

    def note_path(self, citekey: str) -> Path:
        self._validate_citekey(citekey)
        return self.papers_dir / f"{citekey}.md"

    def render_new(self, note: PaperNote) -> str:
        metadata = note.model_dump(mode="json")
        frontmatter = yaml.safe_dump(
            metadata, allow_unicode=True, default_flow_style=False, sort_keys=False
        ).rstrip("\n")
        heading = " ".join(note.title.splitlines()).strip()
        return (
            f"---\n{frontmatter}\n---\n\n# {heading}\n\n"
            "## Why I saved this\n\n"
            "## Human notes\n\n"
            "### Summary\n\n"
            "### Important results\n\n"
            "### Critique\n\n"
            "### Connections\n\n"
            "## AI review\n\n"
            "<!-- BEGIN MANAGED:AI_REVIEW -->\n\n"
            "No AI review has been generated.\n\n"
            "<!-- END MANAGED:AI_REVIEW -->\n\n"
            "## Zotero annotations\n\n"
            "<!-- BEGIN MANAGED:ZOTERO_ANNOTATIONS -->\n\n"
            "No Zotero annotations have been imported.\n\n"
            "<!-- END MANAGED:ZOTERO_ANNOTATIONS -->\n"
        )

    def parse(self, path: Path) -> PaperDocument:
        self._validate_note_path(path)
        text = self._read(path)
        metadata, body, _start, _end = self._split_frontmatter(path, text)
        try:
            note = PaperNote.model_validate(metadata)
        except PydanticValidationError as exc:
            raise MarkdownParseError(f"{path}: invalid paper frontmatter: {exc}") from exc
        self._marker_pairs(path, body, require_all=True)
        return PaperDocument(note=note, body=body)

    def create(self, note: PaperNote) -> Path:
        path = self.note_path(note.citekey)
        if path.exists():
            raise FileExistsError(f"refusing to overwrite existing note: {path}")
        self._atomic_write(path, self.render_new(note), must_not_exist=True)
        return path

    def update_zotero_fields(self, path: Path, updates: Mapping[str, Any]) -> bool:
        """Update only fields owned by Zotero while preserving the rest of the note."""
        return self._update_owned_fields(
            path,
            updates,
            allowed_fields=_ZOTERO_FIELDS,
            immutable_zotero_key=True,
        )

    def update_ai_fields(self, path: Path, updates: Mapping[str, Any]) -> bool:
        """Update AI-owned metadata without allowing human verification to be automated."""
        return self._update_owned_fields(
            path,
            updates,
            allowed_fields=_AI_FIELDS,
            immutable_zotero_key=False,
        )

    def update_tag_sync_fields(self, path: Path, updates: Mapping[str, Any]) -> bool:
        """Record a completed tag push without exposing other note ownership domains."""
        return self._update_owned_fields(
            path,
            updates,
            allowed_fields=_TAG_SYNC_FIELDS,
            immutable_zotero_key=False,
        )

    def _update_owned_fields(
        self,
        path: Path,
        updates: Mapping[str, Any],
        *,
        allowed_fields: frozenset[str],
        immutable_zotero_key: bool,
    ) -> bool:
        self._validate_note_path(path)
        forbidden = set(updates) - allowed_fields
        if forbidden:
            names = ", ".join(sorted(forbidden))
            raise ValueError(f"{path}: refusing unowned frontmatter update(s): {names}")
        text = self._read(path)
        metadata, body, start, end = self._split_frontmatter(path, text)
        if (
            immutable_zotero_key
            and "zotero_key" in updates
            and updates["zotero_key"] != metadata.get("zotero_key")
        ):
            raise ValueError(f"{path}: zotero_key is immutable for an existing note")
        merged = {**metadata, **updates}
        try:
            current_note = PaperNote.model_validate(metadata)
            updated_note = PaperNote.model_validate(merged)
        except PydanticValidationError as exc:
            raise MarkdownParseError(f"{path}: invalid paper frontmatter: {exc}") from exc
        self._marker_pairs(path, body, require_all=True)
        current = current_note.model_dump(mode="json")
        updated = updated_note.model_dump(mode="json")
        effective_updates = {key: updated[key] for key in updates if current[key] != updated[key]}
        if not effective_updates:
            return False
        new_metadata = self._patch_frontmatter(metadata, effective_updates, text[start:end])
        new_text = f"{text[:start]}{new_metadata}{text[end:]}"
        if new_text == text:
            return False
        self._atomic_write(path, new_text)
        return True

    def replace_managed_block(self, path: Path, block_name: str, content: str) -> bool:
        self._validate_note_path(path)
        if block_name not in _BLOCKS:
            raise ManagedBlockError(f"{path}: unsupported managed block {block_name!r}")
        text = self._read(path)
        metadata, body, _start, closing = self._split_frontmatter(path, text)
        try:
            PaperNote.model_validate(metadata)
        except PydanticValidationError as exc:
            raise MarkdownParseError(f"{path}: invalid paper frontmatter: {exc}") from exc
        pairs = self._marker_pairs(path, body, require_all=True)
        begin, end = pairs[block_name]
        begin_end = begin.end()
        replacement = content.strip("\n")
        inner = f"\n\n{replacement}\n\n" if replacement else "\n\n"
        new_body = f"{body[:begin_end]}{inner}{body[end.start():]}"
        self._marker_pairs(path, new_body, require_all=True)
        new_text = f"{text[:closing + 5]}{new_body}"
        if new_text == text:
            return False
        self._atomic_write(path, new_text)
        return True

    def rename(self, path: Path, new_citekey: str) -> Path:
        self._validate_note_path(path)
        self._validate_citekey(new_citekey)
        target = self.note_path(new_citekey)
        text = self._read(path)
        metadata, body, start, closing = self._split_frontmatter(path, text)
        try:
            note = PaperNote.model_validate(metadata)
        except PydanticValidationError as exc:
            raise MarkdownParseError(f"{path}: invalid paper frontmatter: {exc}") from exc
        self._marker_pairs(path, body, require_all=True)
        if target == path:
            if note.citekey != new_citekey:
                raise MarkdownParseError(
                    f"{path}: filename citekey {new_citekey!r} does not match frontmatter"
                )
            return target
        if target.exists():
            raise FileExistsError(f"refusing to overwrite existing note: {target}")
        # os.replace is atomic and also ensures a cross-directory rename cannot
        # accidentally be used to escape the store's directory.
        if path.parent != self.papers_dir:
            raise ValueError(f"{path}: note is outside papers directory")
        updated = self._patch_frontmatter(metadata, {"citekey": new_citekey}, text[start:closing])
        self._atomic_write(target, f"{text[:start]}{updated}{text[closing:]}", must_not_exist=True)
        try:
            path.unlink()
        except OSError:
            target.unlink(missing_ok=True)
            raise
        return target

    @staticmethod
    def _validate_citekey(citekey: str) -> None:
        if not _CITEKEY.fullmatch(citekey) or citekey in {".", ".."}:
            raise ValueError(f"unsafe citekey: {citekey!r}")

    def _validate_note_path(self, path: Path) -> None:
        try:
            root = self.papers_dir.resolve()
            resolved = path.resolve()
        except OSError as exc:
            raise ValueError(f"invalid paper-note path {path}: {exc}") from exc
        if resolved.parent != root or path.suffix != ".md":
            raise ValueError(f"{path}: note is outside papers directory")

    @staticmethod
    def _read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise MarkdownParseError(f"{path}: unable to read note: {exc}") from exc

    @staticmethod
    def _split_frontmatter(path: Path, text: str) -> tuple[dict[str, Any], str, int, int]:
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
        except yaml.YAMLError as exc:
            raise MarkdownParseError(f"{path}: invalid YAML frontmatter: {exc}") from exc
        if not isinstance(metadata, dict) or not all(isinstance(key, str) for key in metadata):
            raise MarkdownParseError(f"{path}: frontmatter must be a mapping")
        end = closing + 5
        return metadata, text[end:], 4, closing

    @staticmethod
    def _marker_pairs(
        path: Path, body: str, *, require_all: bool
    ) -> dict[str, tuple[re.Match[str], re.Match[str]]]:
        active: list[tuple[str, re.Match[str]]] = []
        pairs: dict[str, tuple[re.Match[str], re.Match[str]]] = {}
        for marker in _MARKER.finditer(body):
            kind, name = marker.groups()
            if kind == "BEGIN":
                if name in pairs or any(open_name == name for open_name, _ in active):
                    raise ManagedBlockError(f"{path}: duplicate BEGIN marker for {name}")
                active.append((name, marker))
            else:
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
        if require_all:
            missing = _BLOCKS - set(pairs)
            if missing:
                raise ManagedBlockError(
                    f"{path}: missing managed block(s): {', '.join(sorted(missing))}"
                )
        return pairs

    @staticmethod
    def _patch_frontmatter(
        metadata: Mapping[str, Any], updates: Mapping[str, Any], raw: str
    ) -> str:
        result = raw
        field_names = "|".join(re.escape(key) for key in metadata)
        next_field = re.compile(rf"(?m)^(?:{field_names}):")
        for key, value in updates.items():
            rendered = yaml.safe_dump(
                {key: value}, allow_unicode=True, default_flow_style=False, sort_keys=False
            ).rstrip("\n")
            field = re.search(rf"(?m)^{re.escape(key)}:", result)
            if field:
                following = next_field.search(result, field.end())
                end = following.start() if following else len(result)
                separator = "\n" if following else ""
                result = f"{result[:field.start()]}{rendered}{separator}{result[end:]}"
            else:
                result = f"{result}\n{rendered}" if result else rendered
        return result

    @staticmethod
    def _atomic_write(path: Path, content: str, *, must_not_exist: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
        temporary_path = Path(temporary)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if must_not_exist:
                try:
                    os.link(temporary_path, path)
                except FileExistsError as exc:
                    raise FileExistsError(f"refusing to overwrite existing note: {path}") from exc
            else:
                os.replace(temporary_path, path)
        finally:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
