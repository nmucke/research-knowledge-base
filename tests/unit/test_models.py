"""Tests for typed Zotero domain models."""

from research_kb.models import ZoteroCreator, ZoteroItem


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
