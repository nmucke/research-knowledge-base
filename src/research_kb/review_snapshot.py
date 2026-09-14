"""One-shot protected-content snapshots for agent review workflows."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from research_kb.config import Settings
from research_kb.exceptions import ValidationError
from research_kb.markdown_store import MarkdownStore
from research_kb.models import ReviewSnapshot

_HUMAN_NOTES = re.compile(r"(?ms)^## Human notes[ \t]*\n(?P<content>.*?)(?=^## AI review[ \t]*$)")


class ReviewSnapshotStore:
    """Persist and consume protected state without copying private note text."""

    def __init__(self, settings: Settings, markdown_store: MarkdownStore) -> None:
        self.settings = settings
        self.markdown_store = markdown_store

    def capture(self, citekey: str) -> Path:
        """Create an immutable baseline at the start of a review workflow."""
        path = self.markdown_store.note_path(citekey)
        document = self.markdown_store.parse(path)
        snapshot = ReviewSnapshot.capture(document.note, self.human_notes(document.body))
        target = self.path(citekey)
        self._require_safe_root()
        if target.exists():
            self._require_safe_file(target)
            raise ValidationError(
                f"{target}: an active review snapshot already exists; consume it before "
                "starting another workflow"
            )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ValidationError(
                f"{target}: unable to create snapshot directory: {error}"
            ) from error
        self._require_safe_root()
        payload = snapshot.model_dump_json(indent=2) + "\n"
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                text=True,
            )
        except OSError as error:
            raise ValidationError(f"{target}: unable to create review snapshot: {error}") from error
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise ValidationError(f"{target}: unable to write review snapshot: {error}") from error
        return target

    def load(self, citekey: str) -> ReviewSnapshot | None:
        """Load a safe baseline, returning none outside an active workflow."""
        path = self.path(citekey)
        self._require_safe_root()
        if not path.exists():
            return None
        self._require_safe_file(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            snapshot = ReviewSnapshot.model_validate(raw)
        except (OSError, UnicodeError, json.JSONDecodeError, PydanticValidationError) as error:
            raise ValidationError(f"{path}: invalid review snapshot: {error}") from error
        if snapshot.citekey != citekey:
            raise ValidationError(f"{path}: snapshot citekey does not match its filename")
        return snapshot

    def consume(self, citekey: str) -> None:
        """Remove a successful workflow's baseline without touching the paper note."""
        path = self.path(citekey)
        self._require_safe_root()
        if not path.exists():
            return
        self._require_safe_file(path)
        try:
            path.unlink()
        except OSError as error:
            raise ValidationError(f"{path}: unable to consume review snapshot: {error}") from error

    def path(self, citekey: str) -> Path:
        """Use MarkdownStore's citation-key validation for the snapshot filename."""
        note_name = self.markdown_store.note_path(citekey).with_suffix(".json").name
        return self.settings.review_snapshot_dir / note_name

    @staticmethod
    def human_notes(body: str) -> str:
        """Return the exact protected section content, including its whitespace."""
        match = _HUMAN_NOTES.search(body)
        if match is None:
            raise ValidationError("paper note is missing the protected Human notes section")
        return match.group("content")

    def _require_safe_root(self) -> None:
        root = self.settings.review_snapshot_dir
        try:
            resolved = root.resolve()
            vault = self.settings.vault_path.resolve()
        except OSError as error:
            raise ValidationError(f"{root}: invalid review snapshot directory: {error}") from error
        if root.is_symlink() or not resolved.is_relative_to(vault):
            raise ValidationError(f"{root}: review snapshot directory resolves outside the vault")

    def _require_safe_file(self, path: Path) -> None:
        self._require_safe_root()
        try:
            root = self.settings.review_snapshot_dir.resolve()
            resolved = path.resolve()
        except OSError as error:
            raise ValidationError(f"{path}: invalid review snapshot path: {error}") from error
        if path.is_symlink() or resolved.parent != root or not path.is_file():
            raise ValidationError(f"{path}: unsafe review snapshot path")
