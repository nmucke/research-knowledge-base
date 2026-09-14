"""Structural checks for the Obsidian Bases dashboards."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

DASHBOARDS_DIR = Path(__file__).parents[2] / "src/research_kb/assets/vault/Literature/Dashboards"
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


def _property(name: str) -> str:
    """Normalise a column or sort key.

    Obsidian rewrites `note.year` to `year` whenever it saves a dashboard, so
    both spellings must compare equal or these tests break every time a
    dashboard is opened.
    """
    return name.removeprefix("note.")


def _columns(view: Mapping[str, Any]) -> set[str]:
    columns = view["order"]
    assert isinstance(columns, list)
    assert all(isinstance(column, str) for column in columns)
    return {_property(column) for column in columns}


def _sort(view: Mapping[str, Any]) -> list[dict[str, str]]:
    keys = view["sort"]
    assert isinstance(keys, list)
    return [{**key, "property": _property(key["property"])} for key in keys]


def test_dashboard_files_are_exactly_the_required_yaml_bases() -> None:
    files = {path.name for path in DASHBOARDS_DIR.glob("*.base")}

    assert files == EXPECTED_DASHBOARDS | {
        "Outdated Reviews.base",
        "Extraction Problems.base",
        "Pending Approvals.base",
    }
    for filename in files - {"Pending Approvals.base"}:
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
                "authors",
                "year",
                "human_priority",
                "human_relevance",
                "ai_review_status",
                "ai_recommendation",
                "date_added",
            },
        ),
        (
            "AI Reviewed.base",
            "AI Reviewed",
            {'ai_review_status == "reviewed"'},
            {
                "formula.paper",
                "ai_recommendation",
                "ai_relevance",
                "ai_recommendation_confidence",
                "ai_review_scope",
                "ai_review_agent",
                "ai_review_date",
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
                "authors",
                "year",
                "ai_recommendation",
                "ai_relevance",
                "ai_recommendation_confidence",
                "human_priority",
                "human_relevance",
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
        "authors",
        "year",
        "human_priority",
        "human_relevance",
        "ai_recommendation",
        "date_added",
    }
    assert _sort(view) == [
        {"property": "human_priority", "direction": "DESC"},
        {"property": "human_relevance", "direction": "DESC"},
        {"property": "date_added", "direction": "ASC"},
    ]


def test_human_read_has_read_and_ai_unverified_views() -> None:
    dashboard = _load_dashboard("Human Read.base")
    human_read = _view(dashboard, "Human Read")
    unverified = _view(dashboard, "Read but AI-unverified")

    assert _filters(human_read) == {'human_read_status == "read"'}
    assert _columns(human_read) >= {
        "formula.paper",
        "human_read_date",
        "human_rating",
        "human_relevance",
    }
    assert _filters(unverified) == {
        'human_read_status == "read"',
        'ai_review_status == "reviewed"',
        "ai_review_human_verified == false",
    }
    assert _columns(unverified) >= {
        "formula.paper",
        "ai_recommendation",
        "ai_relevance",
        "ai_review_date",
    }


PROJECTS_DASHBOARD = (
    Path(__file__).parents[2] / "src/research_kb/assets/vault/Projects/dashboard.base"
)


def test_projects_dashboard_covers_projects_and_both_paper_directions() -> None:
    with PROJECTS_DASHBOARD.open(encoding="utf-8") as stream:
        dashboard = yaml.safe_load(stream)

    assert isinstance(dashboard, dict)
    assert dashboard["formulas"] == {
        "project": "file.asLink(title)",
        "paper": "file.asLink(title)",
    }
    # The dashboard spans two folders, so each view carries its own folder filter.
    assert _filters(dashboard) == {'file.ext == "md"'}
    assert _filters(_view(dashboard, "Projects")) == {
        'file.inFolder("Projects")',
        'type == "project"',
    }
    assert _filters(_view(dashboard, "Linked papers")) == {
        'file.inFolder("Literature/Papers")',
        'type == "paper"',
        "note.projects",
    }
    assert _filters(_view(dashboard, "Unlinked reviewed papers")) == {
        'file.inFolder("Literature/Papers")',
        'type == "paper"',
        'ai_review_status == "reviewed"',
        "!note.projects",
    }
    assert _columns(_view(dashboard, "Projects")) >= {"formula.project", "status"}
    assert _columns(_view(dashboard, "Linked papers")) >= {"formula.paper", "projects"}
