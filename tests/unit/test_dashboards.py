"""Structural checks for the Obsidian Bases dashboards."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

DASHBOARDS_DIR = Path(__file__).parents[2] / "Literature" / "Dashboards"
EXPECTED_DASHBOARDS = {
    "AI Reviewed.base",
    "Human Read.base",
    "Inbox.base",
    "Reading Queue.base",
    "Recommended Reading.base",
}
GLOBAL_FILTERS = {
    'file.inFolder("Literature/Papers")',
    'file.ext == "md"',
    'type == "paper"',
}


def _load_dashboard(filename: str) -> dict[str, Any]:
    with (DASHBOARDS_DIR / filename).open(encoding="utf-8") as stream:
        parsed = yaml.safe_load(stream)

    assert isinstance(parsed, dict)
    return parsed


def _filters(container: Mapping[str, Any]) -> set[str]:
    filters = container["filters"]
    assert isinstance(filters, dict)
    conditions = filters["and"]
    assert isinstance(conditions, list)
    assert all(isinstance(condition, str) for condition in conditions)
    return set(conditions)


def _view(dashboard: Mapping[str, Any], name: str) -> dict[str, Any]:
    views = dashboard["views"]
    assert isinstance(views, list)
    matches = [view for view in views if isinstance(view, dict) and view.get("name") == name]
    assert len(matches) == 1
    return matches[0]


def _columns(view: Mapping[str, Any]) -> set[str]:
    columns = view["order"]
    assert isinstance(columns, list)
    assert all(isinstance(column, str) for column in columns)
    return set(columns)


def test_dashboard_files_are_exactly_the_required_yaml_bases() -> None:
    files = {path.name for path in DASHBOARDS_DIR.glob("*.base")}

    assert files == EXPECTED_DASHBOARDS
    for filename in files:
        dashboard = _load_dashboard(filename)
        formulas = dashboard["formulas"]
        assert isinstance(formulas, dict)
        assert formulas["paper"] == "file.asLink(title)"
        properties = dashboard["properties"]
        assert isinstance(properties, dict)
        assert properties["formula.paper"] == {"displayName": "Title"}
        assert _filters(dashboard) == GLOBAL_FILTERS


@pytest.mark.parametrize(
    ("filename", "view_name", "filters", "columns"),
    [
        (
            "Inbox.base",
            "Inbox",
            {'human_read_status == "unread"'},
            {
                "formula.paper",
                "note.authors",
                "note.year",
                "note.human_priority",
                "note.human_relevance",
                "note.ai_review_status",
                "note.ai_recommendation",
                "note.date_added",
            },
        ),
        (
            "AI Reviewed.base",
            "AI Reviewed",
            {'ai_review_status == "reviewed"'},
            {
                "formula.paper",
                "note.ai_recommendation",
                "note.ai_relevance",
                "note.ai_recommendation_confidence",
                "note.ai_review_scope",
                "note.ai_review_agent",
                "note.ai_review_date",
            },
        ),
        (
            "Recommended Reading.base",
            "Recommended Reading",
            {
                'ai_recommendation == "must-read" || ai_recommendation == "read"',
                'human_read_status == "unread" || human_read_status == "queued"',
            },
            {
                "formula.paper",
                "note.authors",
                "note.year",
                "note.ai_recommendation",
                "note.ai_relevance",
                "note.ai_recommendation_confidence",
                "note.human_priority",
                "note.human_relevance",
            },
        ),
    ],
)
def test_single_view_dashboards_have_the_required_filters_and_columns(
    filename: str, view_name: str, filters: set[str], columns: set[str]
) -> None:
    view = _view(_load_dashboard(filename), view_name)

    assert view["type"] == "table"
    assert _filters(view) == filters
    assert _columns(view) == columns


def test_reading_queue_has_the_required_filter_columns_and_priority_sort() -> None:
    view = _view(_load_dashboard("Reading Queue.base"), "Reading Queue")

    assert _filters(view) == {'human_read_status == "queued"'}
    assert _columns(view) == {
        "formula.paper",
        "note.authors",
        "note.year",
        "note.human_priority",
        "note.human_relevance",
        "note.ai_recommendation",
        "note.date_added",
    }
    assert view["sort"] == [
        {"property": "note.human_priority", "direction": "DESC"},
        {"property": "note.human_relevance", "direction": "DESC"},
        {"property": "note.date_added", "direction": "ASC"},
    ]


def test_human_read_has_read_and_ai_unverified_views() -> None:
    dashboard = _load_dashboard("Human Read.base")
    human_read = _view(dashboard, "Human Read")
    unverified = _view(dashboard, "Read but AI-unverified")

    assert _filters(human_read) == {'human_read_status == "read"'}
    assert _columns(human_read) >= {
        "formula.paper",
        "note.human_read_date",
        "note.human_rating",
        "note.human_relevance",
    }
    assert _filters(unverified) == {
        'human_read_status == "read"',
        'ai_review_status == "reviewed"',
        "ai_review_human_verified == false",
    }
    assert _columns(unverified) >= {
        "formula.paper",
        "note.ai_recommendation",
        "note.ai_relevance",
        "note.ai_review_date",
    }
