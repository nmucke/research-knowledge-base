"""Tests for read-only paper-note and review validation."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from research_kb.config import Settings
from research_kb.exceptions import ValidationError
from research_kb.markdown_store import MarkdownStore
from research_kb.models import PaperNote
from research_kb.validation_service import (
    HumanFieldSnapshot,
    ValidationService,
    ValidationSeverity,
)


def _settings(tmp_path: Path, **updates: object) -> Settings:
    settings = Settings(_env_file=None, research_vault_path=tmp_path)
    if updates:
        settings = settings.model_copy(update=updates)
    return settings


def _registry(tmp_path: Path) -> None:
    path = tmp_path / "System" / "tag-registry.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Tag registry\n\n### `domain/weather`\n\nWeather research.\n\n"
        "### `method/diffusion-models`\n\nDiffusion-based models.\n",
        encoding="utf-8",
    )


def _store(tmp_path: Path) -> MarkdownStore:
    return MarkdownStore(tmp_path / "Literature" / "Papers")


def _note(citekey: str = "doeUseful2026", **updates: object) -> PaperNote:
    note = PaperNote(
        zotero_key="ABCD1234",
        citekey=citekey,
        title="A useful paper",
    )
    return note.model_copy(update=updates)


def _reviewed_note(**updates: object) -> PaperNote:
    fields: dict[str, object] = {
        "pdf_attachment_key": "PDFX5678",
        "ai_review_status": "reviewed",
        "ai_review_scope": "full-text",
        "ai_review_coverage": "complete",
        "ai_review_agent": "Codex",
        "ai_review_date": date(2026, 8, 6),
        "ai_recommendation": "read",
        "ai_recommendation_reason": "Relevant methodology.",
        "ai_recommendation_confidence": "high",
        "tags": ("domain/weather",),
        "ai_applied_tags": ("method/diffusion-models",),
    }
    fields.update(updates)
    return _note(**fields)


def _add_review(store: MarkdownStore, path: Path) -> None:
    store.replace_managed_block(
        path,
        "AI_REVIEW",
        "### Summary\n\n- Useful result.\n\n### Reading recommendation\n\n**Read.** Relevant.",
    )


def _cache(tmp_path: Path, **updates: object) -> Path:
    metadata: dict[str, object] = {
        "citekey": "doeUseful2026",
        "zotero_key": "ABCD1234",
        "attachment_key": "PDFX5678",
        "source_mtime": 1.0,
        "source_size": 100,
        "extracted_at": "2026-08-06T12:00:00+00:00",
        "extractor": "pymupdf",
        "extractor_version": "1.26",
        "pages": 1,
        "failed_pages": [],
    }
    metadata.update(updates)
    path = tmp_path / ".research" / "paper-text" / "doeUseful2026.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = yaml.safe_dump(metadata, sort_keys=False).rstrip()
    path.write_text(f"---\n{frontmatter}\n---\n\n<!-- PAGE 1 -->\n\nText.\n", encoding="utf-8")
    return path


def _codes(report: object) -> set[str]:
    return {issue.code for issue in report.issues}  # type: ignore[attr-defined]


def test_validates_default_and_complete_reviewed_notes(tmp_path: Path) -> None:
    _registry(tmp_path)
    store = _store(tmp_path)
    store.create(_note())
    reviewed = store.create(_reviewed_note().model_copy(update={"citekey": "reviewed2026"}))
    _add_review(store, reviewed)
    cache = _cache(tmp_path, citekey="reviewed2026")
    cache.rename(cache.with_name("reviewed2026.md"))

    report = ValidationService(_settings(tmp_path), store).run()

    assert report.ok
    assert report.checked_count == 2
    assert report.issues == report.errors == report.warnings == ()


def test_collects_review_tag_and_missing_cache_failures(tmp_path: Path) -> None:
    _registry(tmp_path)
    store = _store(tmp_path)
    path = store.create(
        _reviewed_note(
            ai_review_agent=" ",
            ai_recommendation_reason=None,
            tags=("domain/weather", "method/not-registered", "Bad Tag"),
            ai_applied_tags=(
                "domain/weather",
                "method/diffusion-models",
                "domain/weather",
                "method/diffusion-models",
                "domain/weather",
                "method/diffusion-models",
            ),
            ai_suggested_tags=("domain/weather", "data/new-one", "task/new-two"),
            zotero_tags=("domain/weather",),
        )
    )
    _add_review(store, path)

    report = ValidationService(_settings(tmp_path), store).run("doeUseful2026")

    assert not report.ok
    assert {
        "review-agent-missing",
        "review-field-missing",
        "tag-invalid",
        "tag-duplicate",
        "tag-unknown",
        "applied-tag-limit",
        "suggested-tag-limit",
        "suggested-tag-known",
        "suggested-tag-synchronized",
        "full-text-cache-missing",
    } <= _codes(report)


def test_unknown_approved_tag_can_be_a_warning(tmp_path: Path) -> None:
    _registry(tmp_path)
    store = _store(tmp_path)
    store.create(_note(tags=("domain/not-registered",)))

    report = ValidationService(
        _settings(tmp_path, unknown_tag_policy="warning"), store
    ).run()

    assert report.ok
    assert len(report.warnings) == 1
    assert report.warnings[0].severity is ValidationSeverity.WARNING
    assert report.warnings[0].code == "tag-unknown"


def test_reports_required_identity_enum_and_boolean_schema_failures(tmp_path: Path) -> None:
    _registry(tmp_path)
    store = _store(tmp_path)
    path = store.create(_note())
    contents = path.read_text(encoding="utf-8")
    contents = contents.replace("type: paper\n", "")
    contents = contents.replace("ai_review_status: not-reviewed", "ai_review_status: done")
    contents = contents.replace(
        "ai_review_human_verified: false", "ai_review_human_verified: 'no'"
    )
    path.write_text(contents, encoding="utf-8")

    report = ValidationService(_settings(tmp_path), store).run()

    assert "identity-missing" in _codes(report)
    assert "schema-invalid" in _codes(report)
    assert len(report.errors) >= 3


@pytest.mark.parametrize(
    "replacement",
    [
        "",
        "<!-- END MANAGED:AI_REVIEW -->\n<!-- END MANAGED:AI_REVIEW -->",
        "<!-- END MANAGED:ZOTERO_ANNOTATIONS -->",
    ],
)
def test_reports_missing_duplicate_and_overlapping_managed_markers(
    tmp_path: Path, replacement: str
) -> None:
    _registry(tmp_path)
    store = _store(tmp_path)
    path = store.create(_note())
    contents = path.read_text(encoding="utf-8")
    path.write_text(
        contents.replace("<!-- END MANAGED:AI_REVIEW -->", replacement),
        encoding="utf-8",
    )

    report = ValidationService(_settings(tmp_path), store).run()

    assert "managed-block-invalid" in _codes(report)


def test_full_text_cache_must_match_note_and_truthful_coverage(tmp_path: Path) -> None:
    _registry(tmp_path)
    store = _store(tmp_path)
    path = store.create(_reviewed_note())
    _add_review(store, path)
    _cache(
        tmp_path,
        zotero_key="ZZZZ9999",
        attachment_key="OTHER123",
        failed_pages=[1],
    )

    report = ValidationService(_settings(tmp_path), store).run()

    assert sum(issue.code == "extraction-cache-mismatch" for issue in report.issues) == 2
    assert "review-coverage-inconsistent" in _codes(report)


def test_whole_vault_continues_after_malformed_note_and_target_errors_are_clear(
    tmp_path: Path,
) -> None:
    _registry(tmp_path)
    store = _store(tmp_path)
    store.create(_note("valid2026"))
    malformed = store.papers_dir / "broken.md"
    malformed.write_text("---\ntitle: [bad\n---\n", encoding="utf-8")
    before = malformed.read_bytes()
    service = ValidationService(_settings(tmp_path), store)

    report = service.run()
    missing = service.run("missing2026")

    assert report.checked_count == 2
    assert "frontmatter-invalid" in _codes(report)
    assert missing.checked_count == 0
    assert _codes(missing) == {"note-not-found"}
    assert malformed.read_bytes() == before
    with pytest.raises(ValidationError, match="unsafe citekey"):
        service.run("../escape")


def test_invalid_registry_is_reported_once_without_hiding_note_errors(tmp_path: Path) -> None:
    registry = tmp_path / "System" / "tag-registry.md"
    registry.parent.mkdir(parents=True)
    registry.write_text("### `unknown/tag`\n\nDefinition.\n", encoding="utf-8")
    store = _store(tmp_path)
    path = store.create(_note())
    path.write_text(path.read_text().replace("title: A useful paper", "title: ''"))

    report = ValidationService(_settings(tmp_path), store).run()

    assert report.checked_count == 1
    assert "tag-registry-invalid" in _codes(report)
    assert "identity-missing" in _codes(report)


def test_missing_papers_directory_and_queued_scope_are_not_confused_with_reviews(
    tmp_path: Path,
) -> None:
    _registry(tmp_path)
    store = _store(tmp_path)
    missing = ValidationService(_settings(tmp_path), store).run()

    assert missing.checked_count == 0
    assert _codes(missing) == {"papers-directory-missing"}

    store.create(
        _note(
            ai_review_status="queued",
            ai_review_scope="full-text",
            ai_review_coverage="unknown",
        )
    )
    queued = ValidationService(_settings(tmp_path), store).run()

    assert queued.ok
    assert "full-text-cache-missing" not in _codes(queued)


def test_symlinked_note_outside_the_vault_is_rejected_without_reading_it(
    tmp_path: Path,
) -> None:
    _registry(tmp_path)
    store = _store(tmp_path)
    store.papers_dir.mkdir(parents=True)
    outside = tmp_path / "private.md"
    outside.write_text("not a paper note", encoding="utf-8")
    linked = store.papers_dir / "linked.md"
    try:
        linked.symlink_to(outside)
    except OSError as error:  # pragma: no cover - platform permission boundary
        pytest.skip(f"symlinks unavailable: {error}")

    report = ValidationService(_settings(tmp_path), store).run()

    assert report.checked_count == 1
    assert _codes(report) == {"note-path-unsafe"}


def test_agent_workflow_baseline_detects_changes_to_every_human_owned_field(
    tmp_path: Path,
) -> None:
    _registry(tmp_path)
    store = _store(tmp_path)
    before = _note()
    store.create(
        before.model_copy(
            update={
                "human_read_status": "read",
                "human_read_date": date(2026, 8, 6),
                "human_rating": 5,
                "human_priority": 4,
                "human_relevance": 3,
            }
        )
    )

    report = ValidationService(_settings(tmp_path), store).run(
        "doeUseful2026",
        human_baselines={"doeUseful2026": HumanFieldSnapshot.from_note(before)},
    )

    ownership = [issue for issue in report.issues if issue.code == "human-field-changed"]
    assert len(ownership) == 5
    assert any("must never infer" in issue.message for issue in ownership)
