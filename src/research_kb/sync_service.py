"""Conservative Zotero-to-Markdown synchronisation."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

from research_kb.config import Settings
from research_kb.exceptions import ResearchKBError, SyncConflictError, SyncError
from research_kb.markdown_store import MarkdownStore, PaperDocument
from research_kb.models import PaperNote, SyncState, ZoteroItem, ZoteroItemBatch
from research_kb.zotero_client import ZoteroServerInfo


class SyncAction(StrEnum):
    """The change planned or made for one note."""

    CREATED = "created"
    UPDATED = "updated"
    RENAMED = "renamed"
    UNCHANGED = "unchanged"
    MISSING = "missing"


@dataclass(frozen=True)
class SyncItemResult:
    action: SyncAction
    zotero_key: str
    citekey: str
    path: Path
    previous_citekey: str | None = None


@dataclass(frozen=True)
class SyncReport:
    mode: Literal["full", "incremental", "targeted"]
    dry_run: bool
    previous_version: int | None
    library_version: int | None
    state_updated: bool
    items: tuple[SyncItemResult, ...]
    fallback_reason: str | None = None

    @property
    def counts(self) -> dict[SyncAction, int]:
        return {action: sum(item.action is action for item in self.items) for action in SyncAction}

    @property
    def created(self) -> int:
        return self.counts[SyncAction.CREATED]

    @property
    def updated(self) -> int:
        return self.counts[SyncAction.UPDATED]

    @property
    def renamed(self) -> int:
        return self.counts[SyncAction.RENAMED]

    @property
    def unchanged(self) -> int:
        return self.counts[SyncAction.UNCHANGED]

    @property
    def missing(self) -> int:
        return self.counts[SyncAction.MISSING]

    @property
    def created_count(self) -> int:
        return self.created

    @property
    def updated_count(self) -> int:
        return self.updated

    @property
    def renamed_count(self) -> int:
        return self.renamed

    @property
    def unchanged_count(self) -> int:
        return self.unchanged

    @property
    def missing_count(self) -> int:
        return self.missing


class _ZoteroClient(Protocol):
    def discover(self) -> ZoteroServerInfo: ...

    def get_item(self, item_key: str, *, library_type: str, library_id: int) -> ZoteroItem: ...

    def list_items(
        self, library_type: str, library_id: int, *, since: int | None = None
    ) -> ZoteroItemBatch: ...


class _BetterBibTeXClient(Protocol):
    def get_citation_key(self, item_key: str, *, library_id: int) -> str: ...


class SyncService:
    """Synchronise only Zotero-owned metadata, leaving note prose untouched."""

    def __init__(
        self,
        settings: Settings,
        zotero_client: _ZoteroClient,
        better_bibtex_client: _BetterBibTeXClient,
        markdown_store: MarkdownStore,
    ) -> None:
        self._settings = settings
        self._zotero_client = zotero_client
        self._better_bibtex_client = better_bibtex_client
        self._store = markdown_store

    def run(
        self,
        *,
        dry_run: bool = False,
        full: bool = False,
        item_key: str | None = None,
        citekey: str | None = None,
    ) -> SyncReport:
        if full and (item_key is not None or citekey is not None):
            raise SyncError("--full cannot be combined with a targeted item")
        if item_key is not None and citekey is not None:
            raise SyncError("item_key and citekey are mutually exclusive")

        documents = self._documents()
        info = self._zotero_client.discover()
        server_id = info.server_id
        if item_key is not None or citekey is not None:
            key = item_key or self._key_for_citekey(documents, citekey or "")
            item = self._zotero_client.get_item(
                key,
                library_type=self._settings.zotero_library_type,
                library_id=self._settings.zotero_library_id,
            )
            results = self._sync_items((item,), documents, server_id, dry_run=dry_run)
            return SyncReport("targeted", dry_run, None, None, False, tuple(results))

        state, fallback = (
            (None, "missing-server-id")
            if server_id is None
            else ((None, None) if full else self._load_state(server_id))
        )
        mode: Literal["full", "incremental"] = "full" if full or fallback else "incremental"
        previous = self._state_version(state) if mode == "incremental" else None
        try:
            batch = self._zotero_client.list_items(
                self._settings.zotero_library_type,
                self._settings.zotero_library_id,
                since=previous if mode == "incremental" else None,
            )
        except ResearchKBError:
            if mode != "incremental":
                raise
            mode, previous, fallback = "full", None, "incremental-read-failed"
            batch = self._zotero_client.list_items(
                self._settings.zotero_library_type, self._settings.zotero_library_id, since=None
            )
        items = tuple(batch.items)
        version = int(batch.library_version)
        current_keys = {item.key for item in items}
        removed_keys = set(batch.removed_item_keys)
        overlap = current_keys & removed_keys
        if overlap:
            keys = ", ".join(sorted(overlap))
            raise SyncConflictError(
                f"Zotero batch contains current and removed item key(s): {keys}"
            )
        results = self._sync_items(items, documents, server_id, dry_run=dry_run)
        if mode == "full":
            missing_keys = {
                document.note.zotero_key for document in documents.values()
            } - current_keys
            results.extend(self._mark_missing(documents, missing_keys, dry_run=dry_run))
        elif removed_keys:
            results.extend(self._mark_missing(documents, removed_keys, dry_run=dry_run))
        if not dry_run and server_id is not None:
            self._write_state(server_id, version)
        return SyncReport(
            mode,
            dry_run,
            previous,
            version,
            not dry_run and server_id is not None,
            tuple(results),
            fallback,
        )

    def _documents(self) -> dict[Path, PaperDocument]:
        paths = sorted(self._store.papers_dir.glob("*.md"))
        documents = {path: self._store.parse(path) for path in paths}
        for attribute in ("zotero_key", "citekey"):
            seen: dict[str, Path] = {}
            for path, document in documents.items():
                identity = str(getattr(document.note, attribute))
                previous = seen.get(identity)
                if previous is not None:
                    raise SyncConflictError(
                        f"{attribute} {identity!r} is claimed by both {previous} and {path}"
                    )
                seen[identity] = path
        return documents

    @staticmethod
    def _key_for_citekey(documents: dict[Path, PaperDocument], citekey: str) -> str:
        matches = [
            document.note.zotero_key
            for document in documents.values()
            if document.note.citekey == citekey
        ]
        if len(matches) != 1:
            raise SyncError(f"no unique existing note for citekey {citekey!r}")
        return str(matches[0])

    def _sync_items(
        self,
        items: Iterable[ZoteroItem],
        documents: dict[Path, PaperDocument],
        server_id: str | None,
        *,
        dry_run: bool,
    ) -> list[SyncItemResult]:
        ordered_items = sorted(items, key=lambda candidate: candidate.key)
        resolved = [
            (
                item,
                self._better_bibtex_client.get_citation_key(
                    item.key, library_id=self._settings.zotero_library_id
                ),
            )
            for item in ordered_items
        ]
        citekeys = [citekey for _item, citekey in resolved]
        if len(citekeys) != len(set(citekeys)):
            raise SyncConflictError("Better BibTeX returned one citekey for multiple Zotero items")
        try:
            for _item, citekey in resolved:
                self._store.note_path(citekey)
        except ValueError as exc:
            raise SyncError(f"Better BibTeX returned an unsafe citekey: {exc}") from exc
        results: list[SyncItemResult] = []
        for item, citekey in resolved:
            path, document = self._match(documents, item.key, citekey)
            fresh = PaperNote.from_zotero(
                item, citekey, server_id, library_type=self._settings.zotero_library_type
            )
            if document is None:
                target = self._store.note_path(citekey)
                if not dry_run:
                    self._store.create(fresh)
                results.append(SyncItemResult(SyncAction.CREATED, item.key, citekey, target))
                continue
            original = document.note
            assert path is not None
            target = path
            renamed = original.citekey != citekey
            if renamed:
                target = self._store.note_path(citekey)
                if not dry_run:
                    target = self._store.rename(path, citekey)
            updates = self._zotero_updates(original, fresh)
            changed_review = (
                original.ai_review_status == "reviewed"
                and original.ai_review_scope == "abstract-only"
                and (original.title != fresh.title or original.abstract != fresh.abstract)
            )
            reset_missing = original.zotero_missing
            if not dry_run:
                if updates:
                    self._store.update_zotero_fields(target, updates)
                if changed_review:
                    self._store.update_ai_fields(target, {"ai_review_status": "outdated"})
                if reset_missing:
                    self._store.update_zotero_fields(target, {"zotero_missing": False})
            if renamed:
                documents.pop(path)
                documents[target] = replace(
                    document,
                    note=original.model_copy(update={"citekey": citekey, **updates}),
                )
            action = (
                SyncAction.RENAMED
                if renamed
                else (
                    SyncAction.UPDATED
                    if updates or changed_review or reset_missing
                    else SyncAction.UNCHANGED
                )
            )
            results.append(
                SyncItemResult(
                    action, item.key, citekey, target, original.citekey if renamed else None
                )
            )
        return results

    @staticmethod
    def _zotero_updates(old: PaperNote, new: PaperNote) -> dict[str, Any]:
        names = (
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
        )
        return {
            name: getattr(new, name)
            for name in names
            if getattr(old, name) != getattr(new, name)
            and not (name == "zotero_server_id" and new.zotero_server_id is None)
        }

    @staticmethod
    def _match(
        documents: dict[Path, PaperDocument], key: str, citekey: str
    ) -> tuple[Path | None, PaperDocument | None]:
        by_key = [(path, doc) for path, doc in documents.items() if doc.note.zotero_key == key]
        by_citekey = [(path, doc) for path, doc in documents.items() if doc.note.citekey == citekey]
        if len(by_key) > 1 or len(by_citekey) > 1:
            raise SyncConflictError(
                f"multiple notes claim Zotero key {key!r} or citekey {citekey!r}"
            )
        if by_key and by_citekey and by_key[0][0] != by_citekey[0][0]:
            raise SyncConflictError(
                f"Zotero key {key!r} and citekey {citekey!r} resolve to different notes"
            )
        if by_citekey and by_citekey[0][1].note.zotero_key != key:
            raise SyncConflictError(
                f"citekey {citekey!r} belongs to Zotero key "
                f"{by_citekey[0][1].note.zotero_key!r}, not {key!r}"
            )
        return by_key[0] if by_key else (by_citekey[0] if by_citekey else (None, None))

    def _mark_missing(
        self,
        documents: dict[Path, PaperDocument],
        missing_keys: set[str],
        *,
        dry_run: bool,
    ) -> list[SyncItemResult]:
        results: list[SyncItemResult] = []
        for path, document in sorted(documents.items(), key=lambda pair: str(pair[0])):
            note = document.note
            if note.zotero_key not in missing_keys:
                continue
            if not dry_run:
                self._store.update_zotero_fields(path, {"zotero_missing": True})
            results.append(SyncItemResult(SyncAction.MISSING, note.zotero_key, note.citekey, path))
        return results

    def _load_state(self, server_id: str) -> tuple[SyncState | None, str | None]:
        path = self._state_path
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            state = SyncState.model_validate(value)
        except (OSError, ValueError, TypeError):
            return None, "missing-or-invalid-state"
        if (
            state.server_id != server_id
            or state.library_type != self._settings.zotero_library_type
            or state.library_id != self._settings.zotero_library_id
        ):
            return None, "server-changed"
        return state, None

    @property
    def _state_path(self) -> Path:
        return Path(self._settings.research_dir) / "sync-state.json"

    @staticmethod
    def _state_version(state: SyncState | None) -> int | None:
        return state.library_version if state is not None else None

    def _write_state(self, server_id: str, version: int) -> None:
        state = SyncState(
            server_id=server_id,
            library_type=self._settings.zotero_library_type,
            library_id=self._settings.zotero_library_id,
            library_version=version,
        )
        path = self._state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".sync-state.", dir=path.parent, text=True)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state.model_dump(mode="json"), handle, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
