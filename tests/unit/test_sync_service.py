"""Focused behavioural tests for conservative note synchronisation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from research_kb.exceptions import SyncConflictError, SyncError, ZoteroUnavailableError
from research_kb.markdown_store import MarkdownStore
from research_kb.models import PaperNote, ZoteroAttachment, ZoteroItem, ZoteroItemBatch
from research_kb.sync_service import SyncAction, SyncService


def _item(key: str = "ABCD1234", *, version: int = 2, title: str = "New title") -> ZoteroItem:
    return ZoteroItem(
        key=key, version=version, library_id=0, item_type="journalArticle", title=title, creators=()
    )


def _attachment(key: str = "PDFX5678", *, parent: str = "ABCD1234") -> ZoteroAttachment:
    return ZoteroAttachment(
        key=key,
        version=3,
        parent_item=parent,
        content_type="application/pdf",
        filename="paper.pdf",
        link_mode="imported_file",
        title="Paper PDF",
        date_modified="2026-08-06T10:00:00Z",
        mtime=1_786_009_600_000,
    )


@dataclass
class _Zotero:
    batches: list[object]
    targeted: dict[str, ZoteroItem]
    server_id: str | None = "server"
    attachments: dict[str, ZoteroAttachment | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.calls: list[int | None] = []
        self.attachment_calls: list[str] = []

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

    def select_pdf_attachment(
        self, item_key: str, *, library_type: str, library_id: int
    ) -> ZoteroAttachment | None:
        del library_type, library_id
        self.attachment_calls.append(item_key)
        return self.attachments.get(item_key)


class _BBT:
    def __init__(self, keys: dict[str, str]) -> None:
        self.keys = keys

    def get_citation_key(self, item_key: str, *, library_id: int) -> str:
        del library_id
        return self.keys[item_key]


def _settings(
    tmp_path: Path, *, library_type: str = "user", library_id: int = 0
) -> SimpleNamespace:
    return SimpleNamespace(
        papers_dir=tmp_path / "vault" / "Literature" / "Papers",
        research_dir=tmp_path / ".research",
        zotero_library_type=library_type,
        zotero_library_id=library_id,
    )


def _service(
    tmp_path: Path,
    zotero: _Zotero,
    keys: dict[str, str],
    *,
    library_type: str = "user",
    library_id: int = 0,
) -> tuple[SyncService, MarkdownStore]:
    settings = _settings(tmp_path, library_type=library_type, library_id=library_id)
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


def test_sync_records_selected_pdf_and_correct_open_uri(tmp_path: Path) -> None:
    item = _item()
    attachment = _attachment(parent=item.key)
    zotero = _Zotero(
        [ZoteroItemBatch(items=(item,), library_version=5)],
        {},
        attachments={item.key: attachment},
    )
    service, store = _service(tmp_path, zotero, {item.key: "paper"})

    service.run()

    note = store.parse(store.note_path("paper")).note
    assert note.pdf_attachment_key == attachment.key
    assert note.pdf_uri == "zotero://open-pdf/library/items/PDFX5678"
    assert zotero.attachment_calls == [item.key]


def test_group_sync_uses_group_open_pdf_uri(tmp_path: Path) -> None:
    item = _item()
    attachment = _attachment(parent=item.key)
    zotero = _Zotero(
        [ZoteroItemBatch(items=(item,), library_version=5)],
        {},
        attachments={item.key: attachment},
    )
    service, store = _service(
        tmp_path, zotero, {item.key: "paper"}, library_type="group", library_id=42
    )

    service.run()

    note = store.parse(store.note_path("paper")).note
    assert note.pdf_uri == "zotero://open-pdf/groups/42/items/PDFX5678"


@pytest.mark.parametrize("scope", ["partial-text", "full-text"])
def test_pdf_attachment_change_invalidates_a_text_review(tmp_path: Path, scope: str) -> None:
    item = _item()
    replacement = _attachment("NEWPDF12", parent=item.key)
    zotero = _Zotero(
        [ZoteroItemBatch(items=(item,), library_version=5)],
        {},
        attachments={item.key: replacement},
    )
    service, store = _service(tmp_path, zotero, {item.key: "paper"})
    path = store.create(
        PaperNote(
            zotero_key=item.key,
            citekey="paper",
            title=item.title,
            pdf_attachment_key="OLDPDF12",
            pdf_uri="zotero://open-pdf/library/items/OLDPDF12",
            ai_review_status="reviewed",
            ai_review_scope=scope,  # type: ignore[arg-type]
        )
    )

    report = service.run()

    note = store.parse(path).note
    assert report.items[0].action is SyncAction.UPDATED
    assert note.pdf_attachment_key == replacement.key
    assert note.ai_review_status == "outdated"


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
    zotero = _Zotero([ZoteroItemBatch(items=(item,), library_version=8)], {}, server_id=None)
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
