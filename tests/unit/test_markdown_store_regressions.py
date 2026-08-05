"""Adversarial regression tests for lossless paper-note storage."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import research_kb.markdown_store as markdown_store_module
from research_kb.exceptions import ManagedBlockError, MarkdownParseError
from research_kb.markdown_store import MarkdownStore, PaperDocument
from research_kb.models import PaperNote


def _note(**overrides: object) -> PaperNote:
    """A complete baseline note; individual tests change only relevant metadata."""
    values: dict[str, object] = {
        "schema_version": 1,
        "type": "paper",
        "zotero_key": "ABCD1234",
        "zotero_version": 7,
        "zotero_server_id": "library-id",
        "citekey": "garcia2026dynamics",
        "title": "Dynamique des fluides",
        "authors": ("García, Ana",),
        "year": 2026,
        "publication": "Journal of Tests",
        "volume": None,
        "issue": None,
        "pages": None,
        "doi": None,
        "url": None,
        "abstract": None,
        "zotero_collections": (),
        "zotero_tags": (),
        "zotero_uri": "zotero://select/library/items/ABCD1234",
        "pdf_attachment_key": None,
        "pdf_uri": None,
        "date_added": "2026-01-02",
        "date_modified": "2026-01-03",
        "human_read_status": "queued",
        "human_read_date": None,
        "human_rating": None,
        "human_priority": None,
        "human_relevance": None,
        "ai_review_status": "not-reviewed",
        "ai_review_scope": None,
        "ai_review_coverage": None,
        "ai_review_agent": None,
        "ai_review_model": None,
        "ai_review_date": None,
        "ai_review_version": 0,
        "ai_review_human_verified": False,
        "ai_recommendation": None,
        "ai_recommendation_reason": None,
        "ai_relevance": None,
        "ai_recommendation_confidence": None,
        "tags": (),
        "ai_applied_tags": (),
        "ai_suggested_tags": (),
        "zotero_tag_sync": "not-synced",
        "zotero_tag_sync_date": None,
    }
    values.update(overrides)
    return PaperNote(**values)


def _content(*, title: str = "Dynamique des fluides") -> str:
    return f'''---
schema_version: 1
type: paper
zotero_key: ABCD1234
zotero_version: 7
zotero_server_id: library-id
citekey: garcia2026dynamics
title: "{title}"
authors: ["García, Ana"]
year: 2026
publication: "Journal of Tests"
volume:
issue:
pages:
doi:
url:
abstract:
zotero_collections: []
zotero_tags: []
zotero_uri: zotero://select/library/items/ABCD1234
pdf_attachment_key:
pdf_uri:
date_added: 2026-01-02
date_modified: 2026-01-03
human_read_status: queued
human_read_date: 2026-02-04
human_rating: 5
human_priority: 3
human_relevance: 4
ai_review_status: not-reviewed
ai_review_scope:
ai_review_coverage:
ai_review_agent:
ai_review_model:
ai_review_date:
ai_review_version: 0
ai_review_human_verified: false
ai_recommendation:
ai_recommendation_reason:
ai_relevance:
ai_recommendation_confidence:
tags: []
ai_applied_tags: []
ai_suggested_tags: []
zotero_tag_sync: not-synced
zotero_tag_sync_date:
---
# {title}

## Why I saved this
This is unmanaged and deliberately **not** AI-owned.

## Human notes
The human wrote: preserve every byte, including `inline code`.

### Summary
An exact, intentionally odd paragraph:
  two leading spaces and an emoji U0001f9ea.

## AI review
<!-- BEGIN MANAGED:AI_REVIEW -->
No AI review has been generated.
<!-- END MANAGED:AI_REVIEW -->

## Zotero annotations
<!-- BEGIN MANAGED:ZOTERO_ANNOTATIONS -->
No Zotero annotations have been imported.
<!-- END MANAGED:ZOTERO_ANNOTATIONS -->

## Appendix
Unmanaged trailing content must survive too.
'''


def _write(papers_dir: Path, content: str = _content()) -> Path:
    path = papers_dir / "garcia2026dynamics.md"
    path.write_text(content, encoding="utf-8")
    return path


def test_unicode_quoted_and_multiline_yaml_round_trip(tmp_path: Path) -> None:
    store = MarkdownStore(tmp_path)
    note = _note(
        title='“Étude” of YAML: a colon',
        authors=("García, Ana", "李, 雷"),
        abstract="First line.\nSecond line: quoted ‘value’.\n",
    )

    rendered = store.render_new(note)
    path = tmp_path / "garcia2026dynamics.md"
    path.write_text(rendered, encoding="utf-8")

    parsed = store.parse(path)
    assert parsed.note.title == note.title
    assert parsed.note.authors == note.authors
    assert parsed.note.abstract == note.abstract


def test_update_preserves_every_human_field_and_all_unmanaged_body_bytes(tmp_path: Path) -> None:
    store = MarkdownStore(tmp_path)
    path = _write(tmp_path)
    before = path.read_text(encoding="utf-8")
    human_frontmatter = before[
        before.index("human_read_status:") : before.index("ai_review_status:")
    ]
    human_and_unmanaged = before[before.index("## Why I saved this") : before.index("## AI review")]
    trailing = before[before.index("## Zotero annotations") :]

    assert store.update_zotero_fields(path, {"title": "Metadata changed", "zotero_version": 8})

    after = path.read_text(encoding="utf-8")
    assert human_frontmatter in after
    assert human_and_unmanaged in after
    assert trailing in after
    parsed = store.parse(path)
    assert parsed.note.human_read_status == "queued"
    assert parsed.note.human_read_date == date(2026, 2, 4)
    assert parsed.note.human_rating == 5
    assert parsed.note.human_priority == 3
    assert parsed.note.human_relevance == 4


@pytest.mark.parametrize(
    "content, expected_error",
    [
        ("---\ntitle: [unterminated\n---\nbody\n", MarkdownParseError),
        (_content().replace("<!-- END MANAGED:AI_REVIEW -->\n", ""), ManagedBlockError),
        (
            _content().replace(
                "<!-- END MANAGED:AI_REVIEW -->",
                "<!-- END MANAGED:AI_REVIEW -->\n<!-- END MANAGED:AI_REVIEW -->",
            ),
            ManagedBlockError,
        ),
        (
            _content().replace(
                "<!-- BEGIN MANAGED:AI_REVIEW -->\nNo AI review has been generated.\n"
                "<!-- END MANAGED:AI_REVIEW -->",
                "<!-- END MANAGED:AI_REVIEW -->\nNo AI review has been generated.\n"
                "<!-- BEGIN MANAGED:AI_REVIEW -->",
            ),
            ManagedBlockError,
        ),
        (
            _content().replace(
                "No AI review has been generated.",
                "<!-- BEGIN MANAGED:ZOTERO_ANNOTATIONS -->\nNo AI review has been generated.",
            ),
            ManagedBlockError,
        ),
    ],
    ids=["malformed-yaml", "missing", "duplicate", "reversed", "overlapping"],
)
def test_corrupt_documents_are_rejected_without_mutating_them(
    tmp_path: Path, content: str, expected_error: type[Exception]
) -> None:
    store = MarkdownStore(tmp_path)
    path = _write(tmp_path, content)

    with pytest.raises(expected_error):
        store.replace_managed_block(path, "AI_REVIEW", "replacement")

    assert path.read_text(encoding="utf-8") == content


@pytest.mark.parametrize("citekey", ["../escape", "nested/key", ".", "", "a\\b"])
def test_unsafe_citekeys_cannot_escape_papers_directory(tmp_path: Path, citekey: str) -> None:
    store = MarkdownStore(tmp_path)
    path = _write(tmp_path)

    with pytest.raises((ValueError, MarkdownParseError)):
        store.rename(path, citekey)

    assert path.exists()
    assert list(tmp_path.iterdir()) == [path]


def test_safe_better_bibtex_punctuation_is_valid_in_note_path(tmp_path: Path) -> None:
    store = MarkdownStore(tmp_path)

    assert store.note_path("jiaD$^2$iTDynamicDiffusion2025") == (
        tmp_path / "jiaD$^2$iTDynamicDiffusion2025.md"
    )


def test_create_and_rename_refuse_collisions_without_overwriting_existing_notes(
    tmp_path: Path,
) -> None:
    store = MarkdownStore(tmp_path)
    original = _write(tmp_path)
    colliding = tmp_path / "already-there.md"
    colliding.write_text("do not overwrite", encoding="utf-8")

    with pytest.raises((FileExistsError, ValueError)):
        store.create(_note())
    assert original.read_text(encoding="utf-8") == _content()

    with pytest.raises((FileExistsError, ValueError)):
        store.rename(original, "already-there")
    assert original.exists()
    assert colliding.read_text(encoding="utf-8") == "do not overwrite"


def test_idempotent_metadata_update_returns_false_and_leaves_mtime_and_content_unchanged(
    tmp_path: Path,
) -> None:
    store = MarkdownStore(tmp_path)
    path = _write(tmp_path)
    before_content = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns

    assert store.update_zotero_fields(path, {"title": "Dynamique des fluides"}) is False

    assert path.read_bytes() == before_content
    assert path.stat().st_mtime_ns == before_mtime


def test_atomic_replace_failure_leaves_original_note_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MarkdownStore(tmp_path)
    path = _write(tmp_path)
    before = path.read_bytes()

    def fail_replace(source: Path | str, destination: Path | str) -> None:
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(markdown_store_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replacement failure"):
        store.update_zotero_fields(path, {"title": "Will not be committed"})

    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_document_is_a_simple_note_and_body_value_object() -> None:
    document = PaperDocument(_note(), "# Body\n")
    assert document.note == _note()
    assert document.body == "# Body\n"
