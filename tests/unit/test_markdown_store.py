from __future__ import annotations

import os
from pathlib import Path

import pytest

from research_kb.exceptions import ManagedBlockError, MarkdownParseError
from research_kb.markdown_store import MarkdownStore
from research_kb.models import PaperNote


def _note(citekey: str = "lovelace2026notes") -> PaperNote:
    return PaperNote(
        zotero_key="ABCD1234",
        zotero_version=1,
        zotero_server_id="server",
        citekey=citekey,
        title="Notes on the Analytical Engine",
        authors=("Ada Lovelace",),
    )


def test_render_parse_and_create_use_the_standard_template(tmp_path: Path) -> None:
    store = MarkdownStore(tmp_path / "vault" / "Literature" / "Papers")
    path = store.create(_note())

    assert path == store.note_path("lovelace2026notes")
    assert store.parse(path).note == _note()
    assert store.parse(path).body == (
        "\n# Notes on the Analytical Engine\n\n## Why I saved this\n\n## Human notes\n\n"
        "### Summary\n\n### Important results\n\n### Critique\n\n### Connections\n\n"
        "## AI review\n\n<!-- BEGIN MANAGED:AI_REVIEW -->\n\nNo AI review has been generated.\n\n"
        "<!-- END MANAGED:AI_REVIEW -->\n\n## Zotero annotations\n\n"
        "<!-- BEGIN MANAGED:ZOTERO_ANNOTATIONS -->\n\nNo Zotero annotations have been imported.\n\n"
        "<!-- END MANAGED:ZOTERO_ANNOTATIONS -->\n"
    )
    with pytest.raises(FileExistsError):
        store.create(_note())


def test_zotero_update_preserves_human_ai_and_body_bytes(tmp_path: Path) -> None:
    store = MarkdownStore(tmp_path)
    path = store.create(_note())
    original = path.read_text(encoding="utf-8")
    original = original.replace("## Human notes\n", "## Human notes\nMy private observation.\n")
    original = original.replace("No AI review has been generated.", "Existing AI review.")
    path.write_text(original, encoding="utf-8")

    assert store.update_zotero_fields(path, {"title": "A Better Title", "year": 1843})
    changed = path.read_text(encoding="utf-8")
    assert "title: A Better Title" in changed
    assert "year: 1843" in changed
    assert "My private observation." in changed
    assert "Existing AI review." in changed
    assert changed[changed.index("# ") :] == original[original.index("# ") :]

    before = path.read_text(encoding="utf-8")
    assert not store.update_zotero_fields(path, {"title": "A Better Title"})
    assert path.read_text(encoding="utf-8") == before
    with pytest.raises(ValueError, match="human_read_status"):
        store.update_zotero_fields(path, {"human_read_status": "read"})
    assert path.read_text(encoding="utf-8") == before


def test_ai_update_is_owned_and_cannot_set_human_verification(tmp_path: Path) -> None:
    store = MarkdownStore(tmp_path)
    path = store.create(_note())
    original = path.read_text(encoding="utf-8")
    original_body = store.parse(path).body

    assert store.update_ai_fields(path, {"ai_review_status": "outdated"})
    parsed = store.parse(path)
    assert parsed.note.ai_review_status == "outdated"
    assert parsed.note.human_read_status == "unread"
    assert parsed.body == original_body
    assert "No AI review has been generated." in parsed.body

    with pytest.raises(ValueError, match="ai_review_human_verified"):
        store.update_ai_fields(path, {"ai_review_human_verified": True})
    with pytest.raises(ValueError, match="human_read_status"):
        store.update_ai_fields(path, {"human_read_status": "read"})
    assert "ai_review_human_verified: false" in path.read_text(encoding="utf-8")
    assert original[original.index("# ") :] == path.read_text(encoding="utf-8")[
        path.read_text(encoding="utf-8").index("# ") :
    ]


def test_zotero_list_update_replaces_the_entire_yaml_field_span(tmp_path: Path) -> None:
    store = MarkdownStore(tmp_path)
    path = store.create(_note())

    assert store.update_zotero_fields(path, {"authors": ("Grace Hopper", "Katherine Johnson")})
    contents = path.read_text(encoding="utf-8")
    assert "Ada Lovelace" not in contents
    assert store.parse(path).note.authors == ("Grace Hopper", "Katherine Johnson")


def test_managed_replacement_is_scoped_idempotent_and_rejects_bad_markers(tmp_path: Path) -> None:
    store = MarkdownStore(tmp_path)
    path = store.create(_note())
    original = path.read_text(encoding="utf-8")

    assert store.replace_managed_block(path, "AI_REVIEW", "A concise review.")
    changed = path.read_text(encoding="utf-8")
    assert "A concise review." in changed
    assert "No Zotero annotations have been imported." in changed
    assert not store.replace_managed_block(path, "AI_REVIEW", "A concise review.")

    before_injection = path.read_text(encoding="utf-8")
    with pytest.raises(ManagedBlockError, match=str(path)):
        store.replace_managed_block(path, "AI_REVIEW", "<!-- BEGIN MANAGED:AI_REVIEW -->")
    assert path.read_text(encoding="utf-8") == before_injection

    path.write_text(original.replace("<!-- END MANAGED:AI_REVIEW -->", ""), encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    with pytest.raises(ManagedBlockError, match=str(path)):
        store.replace_managed_block(path, "AI_REVIEW", "must not write")
    assert path.read_text(encoding="utf-8") == before


def test_malformed_frontmatter_does_not_get_rewritten(tmp_path: Path) -> None:
    store = MarkdownStore(tmp_path)
    path = store.create(_note())
    path.write_text("---\ntitle: [bad\n---\ntext\n", encoding="utf-8")
    before = path.read_text(encoding="utf-8")

    with pytest.raises(MarkdownParseError, match=str(path)):
        store.update_zotero_fields(path, {"title": "Nope"})
    assert path.read_text(encoding="utf-8") == before


def test_writes_use_a_same_directory_temporary_file_and_rename_is_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MarkdownStore(tmp_path)
    path = store.create(_note())
    seen: list[Path] = []
    replace = os.replace

    def observing_replace(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        seen.append(source_path)
        assert source_path.parent == path.parent
        assert source_path.exists()
        replace(source, destination)

    monkeypatch.setattr("research_kb.markdown_store.os.replace", observing_replace)
    assert store.update_zotero_fields(path, {"title": "Changed"})
    assert seen and not seen[0].exists()
    monkeypatch.undo()

    target = store.rename(path, "lovelace2026renamed")
    assert target.exists() and not path.exists()
    assert store.parse(target).note.citekey == "lovelace2026renamed"
    with pytest.raises(ValueError):
        store.note_path("../escape")
    store.create(_note("occupied"))
    with pytest.raises(FileExistsError):
        store.rename(target, "occupied")


def test_parse_rejects_duplicate_fields_and_paths_outside_the_store(tmp_path: Path) -> None:
    papers_dir = tmp_path / "papers"
    store = MarkdownStore(papers_dir)
    path = store.create(_note())
    original = path.read_text(encoding="utf-8")
    path.write_text(original.replace("title:", "title: Duplicate\ntitle:", 1), encoding="utf-8")

    with pytest.raises(MarkdownParseError, match="duplicate frontmatter field.*title"):
        store.parse(path)

    outside = tmp_path / "outside.md"
    outside.write_text(original, encoding="utf-8")
    with pytest.raises(ValueError, match="outside papers directory"):
        store.update_zotero_fields(outside, {"title": "Unsafe"})
    assert outside.read_text(encoding="utf-8") == original


def test_render_flattens_multiline_title_only_in_heading(tmp_path: Path) -> None:
    note = _note().model_copy(update={"title": "First line\nInjected heading"})
    rendered = MarkdownStore(tmp_path).render_new(note)

    assert "title: 'First line\n\n  Injected heading'" in rendered
    assert "# First line Injected heading\n" in rendered
    assert "\nInjected heading\n" not in rendered.split("---\n", 2)[-1]
