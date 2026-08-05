"""Tests for typed Zotero domain models and paper-note frontmatter."""

from datetime import date

import pytest
from pydantic import ValidationError

from research_kb.models import PaperNote, SyncState, ZoteroCreator, ZoteroItem


def test_zotero_creator_display_name_supports_personal_and_single_field_names() -> None:
    person = ZoteroCreator(first_name="Ada", last_name="Lovelace", creator_type="author")
    assert person.display_name == "Ada Lovelace"
    assert ZoteroCreator(name="CERN", creator_type="contributor").display_name == "CERN"


def test_zotero_item_authors_filters_creators() -> None:
    author = ZoteroCreator(first_name="Ada", last_name="Lovelace", creator_type="author")
    editor = ZoteroCreator(first_name="Grace", last_name="Hopper", creator_type="editor")
    item = ZoteroItem(
        key="ABCDE123",
        version=1,
        library_id=0,
        item_type="journalArticle",
        title="Notes",
        creators=(author, editor),
    )

    assert item.authors == (author,)


@pytest.mark.parametrize("field", ["date_added", "date_modified"])
def test_zotero_item_rejects_malformed_timestamps(field: str) -> None:
    values: dict[str, object] = {
        "key": "ABCDE123",
        "version": 1,
        "library_id": 0,
        "item_type": "journalArticle",
        "title": "Notes",
        "creators": (),
    }
    values[field] = "not-a-date"

    with pytest.raises(ValidationError):
        ZoteroItem(**values)


def test_paper_note_has_safe_new_note_defaults() -> None:
    note = PaperNote(zotero_key="ABCD1234", citekey="chen2025flowdas", title="FlowDAS")

    assert note.human_read_status == "unread"
    assert note.ai_review_status == "not-reviewed"
    assert note.ai_review_version == 0


def test_paper_note_accepts_safe_better_bibtex_punctuation() -> None:
    note = PaperNote(
        zotero_key="ABCD1234",
        citekey="jiaD$^2$iTDynamicDiffusion2025",
        title="Dynamic diffusion",
    )

    assert note.citekey == "jiaD$^2$iTDynamicDiffusion2025"
    assert note.ai_review_human_verified is False
    assert note.tags == note.ai_applied_tags == note.ai_suggested_tags == ()
    assert note.zotero_tag_sync == "not-synced"
    assert note.publication is None
    assert note.human_read_date is None
    assert note.ai_recommendation is None
    assert note.zotero_missing is False


@pytest.mark.parametrize(
    ("values", "valid"),
    [
        (
            {"server_id": "server", "library_type": "user", "library_id": 0, "library_version": 0},
            True,
        ),
        ({"server_id": " ", "library_type": "user", "library_id": 0, "library_version": 0}, False),
        (
            {"server_id": "server", "library_type": "team", "library_id": 0, "library_version": 0},
            False,
        ),
        (
            {
                "server_id": "server",
                "library_type": "user",
                "library_id": True,
                "library_version": 0,
            },
            False,
        ),
        (
            {"server_id": "server", "library_type": "user", "library_id": 0, "library_version": -1},
            False,
        ),
    ],
)
def test_sync_state_validates_incremental_cursor(values: dict[str, object], valid: bool) -> None:
    if valid:
        state = SyncState(**values)
        assert state.schema_version == 1
    else:
        with pytest.raises(ValidationError):
            SyncState(**values)


def test_paper_note_accepts_the_complete_frontmatter_schema() -> None:
    note = PaperNote(
        zotero_key="ABCD1234",
        zotero_version=143,
        zotero_server_id="sPMHtLD6HHBd",
        citekey="chen2025flowdas",
        title="FlowDAS",
        authors=("First Author", "Second Author"),
        year=2025,
        publication="Journal of Examples",
        volume="8",
        issue="2",
        pages="1-10",
        doi="10.1234/example",
        url="https://example.test/paper",
        abstract="An example paper.",
        zotero_collections=("Data Assimilation",),
        zotero_tags=("diffusion models",),
        zotero_uri="zotero://select/library/items/ABCD1234",
        pdf_attachment_key="PDFX5678",
        pdf_uri="zotero://open-pdf/library/items/PDFX5678",
        date_added=date(2026, 8, 5),
        date_modified=date(2026, 8, 5),
        human_read_status="queued",
        human_read_date=date(2026, 8, 6),
        human_rating=4,
        human_priority=5,
        human_relevance=5,
        ai_review_status="reviewed",
        ai_review_scope="full-text",
        ai_review_coverage="complete",
        ai_review_agent="codex",
        ai_review_model="test-model",
        ai_review_date=date(2026, 8, 6),
        ai_review_version=1,
        ai_review_human_verified=True,
        ai_recommendation="read",
        ai_recommendation_reason="Directly relevant.",
        ai_relevance=5,
        ai_recommendation_confidence="high",
        tags=("task/data-assimilation",),
        ai_applied_tags=("task/data-assimilation",),
        ai_suggested_tags=("method/example",),
        zotero_tag_sync="synced",
        zotero_tag_sync_date=date(2026, 8, 7),
    )

    assert note.model_dump()["schema_version"] == 1
    assert note.ai_recommendation == "read"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("human_read_status", "started"),
        ("ai_review_status", "in-progress"),
        ("ai_recommendation", "maybe"),
        ("zotero_key", "short"),
        ("citekey", "../../unsafe"),
    ],
)
def test_paper_note_rejects_invalid_enums_and_identity(field: str, value: str) -> None:
    values: dict[str, str] = {"zotero_key": "ABCD1234", "citekey": "safe-key", "title": "Title"}
    values[field] = value

    with pytest.raises(ValidationError):
        PaperNote(**values)


def test_paper_note_forbids_extra_fields_and_is_immutable() -> None:
    with pytest.raises(ValidationError):
        PaperNote(zotero_key="ABCD1234", citekey="safe-key", title="Title", unknown="no")

    note = PaperNote(zotero_key="ABCD1234", citekey="safe-key", title="Title")
    with pytest.raises(ValidationError):
        note.title = "Changed"


@pytest.mark.parametrize(
    ("field", "value"),
    [("title", "  \n"), ("ai_review_human_verified", "false"), ("ai_review_human_verified", 0)],
)
def test_paper_note_rejects_blank_titles_and_non_boolean_verification(
    field: str, value: object
) -> None:
    values: dict[str, object] = {
        "zotero_key": "ABCD1234",
        "citekey": "safe-key",
        "title": "Title",
    }
    values[field] = value

    with pytest.raises(ValidationError):
        PaperNote(**values)


def test_paper_note_from_zotero_maps_item_data_and_builds_user_uri() -> None:
    item = ZoteroItem(
        key="ABCD1234",
        version=143,
        library_id=42,
        item_type="journalArticle",
        title="FlowDAS",
        creators=(ZoteroCreator(first_name="Ada", last_name="Lovelace", creator_type="author"),),
        date="2025-06-01",
        publication="Journal of Examples",
        doi="10.1234/example",
        url="https://example.test/paper",
        abstract="An example paper.",
        tags=("diffusion models",),
        volume="8",
        issue="2",
        pages="1-10",
        collections=("COLLECT01",),
        date_added="2026-08-05T10:00:00Z",
        date_modified="2026-08-06T10:00:00Z",
    )

    note = PaperNote.from_zotero(item, "chen2025flowdas", "server-id")

    assert note.zotero_key == item.key
    assert note.zotero_version == item.version
    assert note.zotero_server_id == "server-id"
    assert note.authors == ("Ada Lovelace",)
    assert note.year == 2025
    assert note.zotero_tags == item.tags
    assert note.volume == "8"
    assert note.issue == "2"
    assert note.pages == "1-10"
    assert note.zotero_collections == ("COLLECT01",)
    assert note.date_added == date(2026, 8, 5)
    assert note.date_modified == date(2026, 8, 6)
    assert note.zotero_uri == "zotero://select/library/items/ABCD1234"
    assert note.pdf_attachment_key is None
    assert note.pdf_uri is None


def test_paper_note_from_zotero_builds_group_uri() -> None:
    item = ZoteroItem(
        key="ABCD1234",
        version=1,
        library_id=42,
        item_type="journalArticle",
        title="Notes",
        creators=(),
    )

    note = PaperNote.from_zotero(item, "notes", "server-id", library_type="group")

    assert note.zotero_uri == "zotero://select/groups/42/items/ABCD1234"
