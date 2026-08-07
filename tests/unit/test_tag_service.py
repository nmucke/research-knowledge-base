"""Adversarial tests for controlled, add-only Zotero tag pushes."""

from __future__ import annotations

from pathlib import Path

import pytest

from research_kb.config import Settings
from research_kb.exceptions import ValidationError, ZoteroConflictError
from research_kb.markdown_store import MarkdownStore
from research_kb.models import PaperNote, ZoteroItem, ZoteroTag
from research_kb.tag_service import TagService


def _settings(tmp_path: Path) -> Settings:
    (tmp_path / "vault" / "System").mkdir(parents=True)
    (tmp_path / "vault" / "System" / "tag-registry.md").write_text(
        "### `domain/weather`\n\nWeather.\n\n### `method/ensemble`\n\nEnsembles.\n",
        encoding="utf-8",
    )
    return Settings(research_vault_path=tmp_path)


def _note(**updates: object) -> PaperNote:
    values: dict[str, object] = {
        "zotero_key": "ABCD1234",
        "zotero_version": 1,
        "citekey": "doe2026",
        "title": "Weather",
    }
    values.update(updates)
    return PaperNote(**values)


def _item(*tags: ZoteroTag, version: int = 4) -> ZoteroItem:
    return ZoteroItem(
        key="ABCD1234",
        version=version,
        library_id=0,
        item_type="journalArticle",
        title="Weather",
        creators=(),
        tags=tuple(tag.tag for tag in tags),
        tag_entries=tags,
    )


class _Client:
    def __init__(self, items: list[ZoteroItem], conflicts: int = 0) -> None:
        self.items = items
        self.conflicts = conflicts
        self.writes: list[tuple[ZoteroTag, ...]] = []

    def get_item(self, item_key: str, *, library_type: str, library_id: int) -> ZoteroItem:
        assert item_key == "ABCD1234" and library_type == "user" and library_id == 0
        return self.items.pop(0)

    def patch_tags(
        self,
        item_key: str,
        tags: tuple[ZoteroTag, ...],
        version: int,
        *,
        api_key: str,
        library_type: str,
        library_id: int,
    ) -> int:
        self.writes.append(tags)
        if self.conflicts:
            self.conflicts -= 1
            raise ZoteroConflictError("race")
        return version + 1


def _service(
    tmp_path: Path, client: _Client, note: PaperNote
) -> tuple[TagService, MarkdownStore, Path]:
    settings = _settings(tmp_path)
    store = MarkdownStore(settings.papers_dir)
    path = store.create(note)
    return TagService(settings, store, client), store, path


def test_plan_excludes_ai_tags_and_preserves_zotero_tag_entries(tmp_path: Path) -> None:
    existing = ZoteroTag(tag="automatic", type=1)
    client = _Client([_item(existing)])
    service, _store, path = _service(
        tmp_path,
        client,
        _note(
            tags=("method/ensemble",),
            ai_applied_tags=("domain/weather",),
            ai_suggested_tags=("method/not-approved",),
        ),
    )

    plan = service.plan(path)

    assert plan.existing_zotero_tag_entries == (existing,)
    assert plan.approved_curated_tags == ("method/ensemble",)
    assert plan.ai_applied_tags == ("domain/weather",)
    assert plan.suggested_tags == ("method/not-approved",)
    assert plan.pending_tags == ("method/ensemble",)
    assert plan.merged_zotero_tag_entries == (existing, ZoteroTag(tag="method/ensemble"))


def test_plan_does_not_append_an_approved_tag_already_present_twice(tmp_path: Path) -> None:
    duplicate = ZoteroTag(tag="domain/weather", type=1)
    service, _store, path = _service(
        tmp_path,
        _Client([_item(duplicate, duplicate)]),
        _note(tags=("domain/weather",)),
    )

    plan = service.plan(path)

    assert plan.pending_tags == ()
    assert plan.merged_zotero_tag_entries == (duplicate, duplicate)


@pytest.mark.parametrize("tag", ["domain/missing", "unknown/topic"])
def test_unknown_or_unallowed_approved_tags_are_rejected(tmp_path: Path, tag: str) -> None:
    service, _store, path = _service(tmp_path, _Client([_item()]), _note(tags=(tag,)))

    with pytest.raises(ValidationError, match="absent from the tag registry"):
        service.plan(path)


def test_registered_tag_in_disallowed_namespace_is_rejected(tmp_path: Path) -> None:
    settings = _settings(tmp_path).model_copy(update={"allowed_tag_namespaces": ("method",)})
    store = MarkdownStore(settings.papers_dir)
    path = store.create(_note(tags=("domain/weather",)))
    service = TagService(settings, store, _Client([_item()]))

    with pytest.raises(ValidationError, match="disallowed namespace"):
        service.plan(path)


def test_plan_rejects_duplicate_tags_or_mismatched_identity(tmp_path: Path) -> None:
    duplicate_service, _store, duplicate_path = _service(
        tmp_path,
        _Client([_item()]),
        _note(tags=("domain/weather", "domain/weather")),
    )
    with pytest.raises(ValidationError, match="duplicated"):
        duplicate_service.plan(duplicate_path)

    other = _item().model_copy(update={"key": "WXYZ5678"})
    (tmp_path / "mismatch").mkdir()
    mismatch_service, _store, mismatch_path = _service(
        tmp_path / "mismatch", _Client([other]), _note()
    )
    with pytest.raises(ValidationError, match="unexpected library identity"):
        mismatch_service.plan(mismatch_path)


def test_plan_rejects_note_from_another_zotero_server(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = MarkdownStore(settings.papers_dir)
    path = store.create(_note(zotero_server_id="server-a"))
    service = TagService(settings, store, _Client([_item()]), server_id="server-b")

    with pytest.raises(ValidationError, match="different Zotero server"):
        service.plan(path)


def test_local_user_zero_alias_accepts_zoteros_numeric_library_id(tmp_path: Path) -> None:
    item = _item().model_copy(update={"library_id": 123456})
    service, _store, path = _service(tmp_path, _Client([item]), _note())

    plan = service.plan(path)

    assert plan.zotero_key == "ABCD1234"


def test_push_is_add_only_and_preserves_body_and_ownership(tmp_path: Path) -> None:
    existing = ZoteroTag(tag="free-form", type=1)
    client = _Client([_item(existing)])
    service, store, path = _service(tmp_path, client, _note(tags=("domain/weather",)))
    original = path.read_text(encoding="utf-8").replace(
        "## Human notes\n", "## Human notes\nPrivate.\n"
    )
    path.write_text(original, encoding="utf-8")

    report = service.push(path, api_key="secret")

    assert report.pushed and report.attempts == 1
    assert client.writes == [(existing, ZoteroTag(tag="domain/weather"))]
    parsed = store.parse(path).note
    assert parsed.zotero_tags == ("free-form", "domain/weather")
    assert parsed.zotero_tag_sync == "synced"
    changed = path.read_text(encoding="utf-8")
    assert "Private." in changed
    assert changed[changed.index("# ") :] == original[original.index("# ") :]


def test_dry_run_and_noop_do_no_writes(tmp_path: Path) -> None:
    client = _Client([_item(), _item(ZoteroTag(tag="domain/weather"))])
    service, _store, path = _service(tmp_path, client, _note(tags=("domain/weather",)))

    assert service.push(path, api_key="secret", dry_run=True).pushed is False
    assert service.push(path, api_key="secret").pushed is False
    assert client.writes == []


def test_push_retries_once_with_new_latest_tags(tmp_path: Path) -> None:
    initial = _item(ZoteroTag(tag="other", type=1), version=4)
    latest = _item(ZoteroTag(tag="other", type=1), ZoteroTag(tag="racer", type=1), version=5)
    client = _Client([initial, latest], conflicts=1)
    service, _store, path = _service(tmp_path, client, _note(tags=("domain/weather",)))

    report = service.push(path, api_key="secret")

    assert report.attempts == 2
    assert client.writes[1] == (
        ZoteroTag(tag="other", type=1),
        ZoteroTag(tag="racer", type=1),
        ZoteroTag(tag="domain/weather"),
    )


def test_second_conflict_surfaces_and_never_updates_note(tmp_path: Path) -> None:
    client = _Client([_item(), _item(version=5)], conflicts=2)
    service, store, path = _service(tmp_path, client, _note(tags=("domain/weather",)))
    before = path.read_text(encoding="utf-8")

    with pytest.raises(ZoteroConflictError):
        service.push(path, api_key="secret")

    assert len(client.writes) == 2
    assert path.read_text(encoding="utf-8") == before
    assert store.parse(path).note.zotero_tag_sync == "not-synced"
