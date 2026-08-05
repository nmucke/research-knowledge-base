"""Focused behavioural tests for conservative note synchronisation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from research_kb.exceptions import SyncConflictError, SyncError, ZoteroUnavailableError
from research_kb.markdown_store import MarkdownStore
from research_kb.models import PaperNote, ZoteroItem, ZoteroItemBatch
from research_kb.sync_service import SyncAction, SyncService


def _item(key: str = "ABCD1234", *, version: int = 2, title: str = "New title") -> ZoteroItem:
    return ZoteroItem(
        key=key, version=version, library_id=0, item_type="journalArticle", title=title, creators=()
    )


@dataclass
class _Zotero:
    batches: list[object]
    targeted: dict[str, ZoteroItem]
    server_id: str | None = "server"

    def __post_init__(self) -> None:
        self.calls: list[int | None] = []

    def discover(self) -> SimpleNamespace:
        return SimpleNamespace(server_id=self.server_id)

    def list_items(self, _type: str, _id: int, since: int | None = None) -> ZoteroItemBatch:
        self.calls.append(since)
        response = self.batches.pop(0)
        if isinstance(response, Exception):
            raise response
        assert isinstance(response, ZoteroItemBatch)
        return response

    def get_item(self, item_key: str, *, library_type: str, library_id: int) -> ZoteroItem:
        del library_type, library_id
        return self.targeted[item_key]


class _BBT:
    def __init__(self, keys: dict[str, str]) -> None:
        self.keys = keys

    def get_citation_key(self, item_key: str, *, library_id: int) -> str:
        del library_id
        return self.keys[item_key]


def _settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        papers_dir=tmp_path / "Literature" / "Papers",
        research_dir=tmp_path / ".research",
        zotero_library_type="user",
        zotero_library_id=0,
    )


def _service(
    tmp_path: Path, zotero: _Zotero, keys: dict[str, str]
) -> tuple[SyncService, MarkdownStore]:
    settings = _settings(tmp_path)
    store = MarkdownStore(settings.papers_dir)
    return SyncService(settings, zotero, _BBT(keys), store), store


def _state(tmp_path: Path, *, server: str = "server", version: int = 4) -> None:
    path = tmp_path / ".research" / "sync-state.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "server_id": server,
                "library_type": "user",
                "library_id": 0,
                "library_version": version,
            }
        ),
        encoding="utf-8",
    )


def _existing(store: MarkdownStore, *, key: str = "ABCD1234", citekey: str = "old") -> Path:
    return store.create(PaperNote(zotero_key=key, citekey=citekey, title="Old title"))


def test_new_full_sync_creates_note_and_persists_state(tmp_path: Path) -> None:
    zotero = _Zotero([ZoteroItemBatch(items=(_item(),), library_version=5)], {})
    service, store = _service(tmp_path, zotero, {"ABCD1234": "new"})

    report = service.run()

    assert report.mode == "full"
    assert report.items[0].action is SyncAction.CREATED
    assert store.parse(store.note_path("new")).note.zotero_key == "ABCD1234"
    assert json.loads((tmp_path / ".research/sync-state.json").read_text())["library_version"] == 5


def test_incremental_server_change_and_malformed_state_select_correct_reads(tmp_path: Path) -> None:
    batch = ZoteroItemBatch(items=(), library_version=6)
    zotero = _Zotero([batch], {})
    service, _store = _service(tmp_path, zotero, {})
    _state(tmp_path)
    assert service.run().mode == "incremental"
    assert zotero.calls == [4]

    _state(tmp_path, server="other")
    zotero.batches = [batch]
    assert service.run().mode == "full"
    assert zotero.calls[-1] is None

    (tmp_path / ".research/sync-state.json").write_text("not json", encoding="utf-8")
    zotero.batches = [batch]
    assert service.run().mode == "full"


def test_incremental_failure_retries_once_as_full(tmp_path: Path) -> None:
    _state(tmp_path)
    zotero = _Zotero(
        [ZoteroUnavailableError("down"), ZoteroItemBatch(items=(), library_version=8)], {}
    )
    service, _store = _service(tmp_path, zotero, {})

    report = service.run()

    assert report.mode == "full" and report.fallback_reason == "incremental-read-failed"
    assert zotero.calls == [4, None]


def test_missing_server_id_forces_full_without_changing_cursor(tmp_path: Path) -> None:
    _state(tmp_path, version=4)
    item = _item()
    zotero = _Zotero(
        [ZoteroItemBatch(items=(item,), library_version=8)], {}, server_id=None
    )
    service, store = _service(tmp_path, zotero, {item.key: "old"})
    path = store.create(
        PaperNote(
            zotero_key=item.key,
            zotero_server_id="known-server",
            citekey="old",
            title="Old title",
        )
    )

    report = service.run()

    assert report.mode == "full"
    assert report.state_updated is False
    assert zotero.calls == [None]
    assert store.parse(path).note.zotero_server_id == "known-server"
    assert json.loads((tmp_path / ".research/sync-state.json").read_text())["library_version"] == 4


def test_targeted_and_dry_run_do_not_persist_or_write(tmp_path: Path) -> None:
    item = _item()
    zotero = _Zotero([], {item.key: item})
    service, store = _service(tmp_path, zotero, {item.key: "new"})

    targeted = service.run(item_key=item.key)
    assert targeted.mode == "targeted" and targeted.library_version is None
    assert not (tmp_path / ".research/sync-state.json").exists()

    dry_root = tmp_path / "dry-run"
    zotero = _Zotero([ZoteroItemBatch(items=(item,), library_version=5)], {})
    service, store = _service(dry_root, zotero, {item.key: "dry"})
    report = service.run(dry_run=True)
    assert report.items[0].action is SyncAction.CREATED
    assert not store.note_path("dry").exists()
    assert not (dry_root / ".research/sync-state.json").exists()


def test_rename_update_missing_reset_and_human_content_are_preserved(tmp_path: Path) -> None:
    item = _item(title="Changed")
    zotero = _Zotero([ZoteroItemBatch(items=(item,), library_version=5)], {})
    service, store = _service(tmp_path, zotero, {item.key: "new"})
    path = _existing(store)
    text = path.read_text(encoding="utf-8").replace("## Human notes\n", "## Human notes\nprivate\n")
    path.write_text(text, encoding="utf-8")

    report = service.run()

    moved = store.note_path("new")
    assert report.items[0].action is SyncAction.RENAMED
    assert "private" in moved.read_text(encoding="utf-8")
    assert store.parse(moved).note.zotero_missing is False

    missing_zotero = _Zotero([ZoteroItemBatch(items=(), library_version=6)], {})
    missing_service, _ = _service(tmp_path, missing_zotero, {})
    missing = missing_service.run(full=True)
    assert missing.items[0].action is SyncAction.MISSING
    assert store.parse(moved).note.zotero_missing is True


def test_incremental_removed_keys_mark_only_the_removed_note_missing(tmp_path: Path) -> None:
    _state(tmp_path)
    zotero = _Zotero(
        [ZoteroItemBatch(items=(), library_version=5, removed_item_keys=("ABCD1234",))], {}
    )
    service, store = _service(tmp_path, zotero, {})
    removed = _existing(store, key="ABCD1234", citekey="removed")
    retained = _existing(store, key="EFGH5678", citekey="retained")

    report = service.run()

    assert [result.action for result in report.items] == [SyncAction.MISSING]
    assert store.parse(removed).note.zotero_missing is True
    assert store.parse(retained).note.zotero_missing is False


def test_overlap_and_unsafe_bbt_citekey_abort_before_note_writes(tmp_path: Path) -> None:
    item = _item()
    overlap = _Zotero(
        [ZoteroItemBatch(items=(item,), library_version=5, removed_item_keys=(item.key,))], {}
    )
    service, store = _service(tmp_path, overlap, {item.key: "safe"})
    with pytest.raises(SyncConflictError):
        service.run()
    assert not store.note_path("safe").exists()

    unsafe = _Zotero([ZoteroItemBatch(items=(item,), library_version=5)], {})
    service, store = _service(tmp_path / "unsafe", unsafe, {item.key: "../../unsafe"})
    with pytest.raises(SyncError, match="unsafe citekey"):
        service.run()
    assert not store.papers_dir.exists()


def test_abstract_review_is_invalidated_and_state_is_not_advanced_after_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = _item(title="Changed")
    zotero = _Zotero([ZoteroItemBatch(items=(item,), library_version=5)], {})
    service, store = _service(tmp_path, zotero, {item.key: "old"})
    path = store.create(
        PaperNote(
            zotero_key=item.key,
            citekey="old",
            title="Old title",
            ai_review_status="reviewed",
            ai_review_scope="abstract-only",
        )
    )
    service.run()
    assert store.parse(path).note.ai_review_status == "outdated"

    _state(tmp_path, version=5)
    zotero.batches = [ZoteroItemBatch(items=(), library_version=6)]
    monkeypatch.setattr(
        service, "_write_state", lambda *_args: (_ for _ in ()).throw(OSError("no"))
    )
    with pytest.raises(OSError):
        service.run()
    assert json.loads((tmp_path / ".research/sync-state.json").read_text())["library_version"] == 5


def test_conflicts_prevent_collision_and_citekey_identity_mismatch(tmp_path: Path) -> None:
    first, second = _item("ABCD1234"), _item("EFGH5678")
    zotero = _Zotero([ZoteroItemBatch(items=(first, second), library_version=2)], {})
    service, store = _service(tmp_path, zotero, {first.key: "same", second.key: "same"})
    with pytest.raises(SyncConflictError):
        service.run(dry_run=True)
    assert not store.note_path("same").exists()

    path = _existing(store, key="EFGH5678", citekey="same")
    zotero = _Zotero([ZoteroItemBatch(items=(first,), library_version=3)], {})
    service, _ = _service(tmp_path, zotero, {first.key: "same"})
    with pytest.raises(SyncConflictError):
        service.run()
    assert path.exists()


def test_rename_releases_old_citekey_for_a_later_new_item(tmp_path: Path) -> None:
    first, second = _item("ABCD1234"), _item("EFGH5678")
    zotero = _Zotero([ZoteroItemBatch(items=(first, second), library_version=2)], {})
    service, store = _service(tmp_path, zotero, {first.key: "new", second.key: "old"})
    _existing(store, key=first.key, citekey="old")

    report = service.run()

    assert [result.action for result in report.items] == [SyncAction.RENAMED, SyncAction.CREATED]
    assert store.note_path("new").exists()
    assert store.parse(store.note_path("old")).note.zotero_key == second.key
