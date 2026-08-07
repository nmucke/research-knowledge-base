"""Tests for page-aware extraction, cache provenance, and failure handling."""

from __future__ import annotations

import os
from pathlib import Path

import pymupdf
import pytest
import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError

from research_kb.config import Settings
from research_kb.exceptions import PDFExtractionError, PDFNotFoundError
from research_kb.extraction_service import (
    ExtractedPages,
    ExtractionService,
    PageFailure,
    PyMuPDFTextExtractor,
)
from research_kb.markdown_store import MarkdownStore
from research_kb.models import ExtractionMetadata, PaperNote


class PathResolver:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.calls: list[tuple[str, str, int]] = []

    def resolve_attachment_path(
        self, attachment_key: str, *, library_type: str, library_id: int
    ) -> Path:
        self.calls.append((attachment_key, library_type, library_id))
        return self.path


class CountingExtractor:
    def __init__(self, result: ExtractedPages, *, version: str = "test-1") -> None:
        self.result = result
        self.version = version
        self.calls: list[Path] = []

    def extract(self, path: Path) -> ExtractedPages:
        self.calls.append(path)
        return self.result


def _settings(tmp_path: Path, *, library_type: str = "user", library_id: int = 0) -> Settings:
    return Settings(
        _env_file=None,
        research_vault_path=tmp_path,
        zotero_library_type=library_type,
        zotero_library_id=library_id,
    )


def _paper_store(tmp_path: Path, *, attachment_key: str | None = "PDFX5678") -> MarkdownStore:
    store = MarkdownStore(tmp_path / "vault" / "Literature" / "Papers")
    store.create(
        PaperNote(
            zotero_key="ABCD1234",
            citekey="doeUseful2026",
            title="Useful paper",
            pdf_attachment_key=attachment_key,
        )
    )
    return store


def _pdf(path: Path, pages: tuple[str, ...]) -> None:
    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)
    document.save(path)
    document.close()


def test_extracts_synthetic_pdf_with_strict_metadata_and_page_markers(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    _pdf(pdf_path, ("First page\n" * 80, "Second page\n" * 80))
    store = _paper_store(tmp_path)
    resolver = PathResolver(pdf_path)
    service = ExtractionService(
        _settings(tmp_path, library_type="group", library_id=42), resolver, store
    )

    result = service.extract("doeUseful2026")

    assert result.cache_hit is False
    assert result.status == "complete"
    assert result.diagnostics.pages == 2
    assert result.diagnostics.total_characters > 0
    assert result.diagnostics.empty_pages == ()
    assert resolver.calls == [("PDFX5678", "group", 42)]
    output = result.output_path.read_text(encoding="utf-8")
    assert "<!-- PAGE 1 -->\n\nFirst page" in output
    assert "<!-- PAGE 2 -->\n\nSecond page" in output
    raw_metadata = yaml.safe_load(output.split("---\n", 2)[1])
    metadata = ExtractionMetadata.model_validate(raw_metadata)
    assert metadata.citekey == "doeUseful2026"
    assert metadata.zotero_key == "ABCD1234"
    assert metadata.attachment_key == "PDFX5678"
    assert metadata.source_mtime == pdf_path.stat().st_mtime
    assert metadata.source_size == pdf_path.stat().st_size
    assert metadata.extractor == "pymupdf"
    assert metadata.extractor_version == pymupdf.__version__
    assert metadata.pages == 2


def test_cache_hit_invalidation_and_force_use_all_provenance_fields(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"source")
    store = _paper_store(tmp_path)
    extractor = CountingExtractor(ExtractedPages(("readable text " * 50,)))
    service = ExtractionService(
        _settings(tmp_path), PathResolver(pdf_path), store, extractor=extractor
    )

    first = service.extract("doeUseful2026")
    cached = service.extract("doeUseful2026")
    forced = service.extract("doeUseful2026", force=True)
    os.utime(pdf_path, (pdf_path.stat().st_atime, pdf_path.stat().st_mtime + 10))
    mtime_invalidated = service.extract("doeUseful2026")
    pdf_path.write_bytes(b"larger source contents")
    size_invalidated = service.extract("doeUseful2026")
    extractor.version = "test-2"
    version_invalidated = service.extract("doeUseful2026")

    assert first.cache_hit is False
    assert cached.cache_hit is True
    assert forced.cache_hit is False
    assert mtime_invalidated.cache_hit is False
    assert size_invalidated.cache_hit is False
    assert version_invalidated.cache_hit is False
    assert len(extractor.calls) == 5


def test_fresh_and_cached_diagnostics_match_after_line_ending_normalization(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"source")
    extractor = CountingExtractor(
        ExtractedPages(
            (
                "first line\r\nsecond line\r\n" * 30,
                "third line\rfourth line\r" * 30,
            )
        )
    )
    service = ExtractionService(
        _settings(tmp_path),
        PathResolver(pdf_path),
        _paper_store(tmp_path),
        extractor=extractor,
    )

    fresh = service.extract("doeUseful2026")
    cached = service.extract("doeUseful2026")

    assert cached.cache_hit is True
    assert fresh.diagnostics.characters_per_page == cached.diagnostics.characters_per_page
    assert fresh.diagnostics.total_characters == cached.diagnostics.total_characters
    assert "\r" not in cached.output_path.read_bytes().decode("utf-8")


def test_changed_attachment_key_invalidates_cache(tmp_path: Path) -> None:
    first_pdf = tmp_path / "first.pdf"
    second_pdf = tmp_path / "second.pdf"
    first_pdf.write_bytes(b"same")
    second_pdf.write_bytes(b"same")
    store = _paper_store(tmp_path)
    resolver = PathResolver(first_pdf)
    extractor = CountingExtractor(ExtractedPages(("readable text " * 50,)))
    service = ExtractionService(_settings(tmp_path), resolver, store, extractor=extractor)
    service.extract("doeUseful2026")
    note_path = store.note_path("doeUseful2026")
    store.update_zotero_fields(note_path, {"pdf_attachment_key": "NEWP5678"})
    resolver.path = second_pdf

    result = service.extract("doeUseful2026")

    assert result.cache_hit is False
    assert len(extractor.calls) == 2


def test_partial_page_failure_preserves_readable_pages_and_survives_cache_hit(
    tmp_path: Path,
) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"placeholder")
    store = _paper_store(tmp_path)
    extractor = CountingExtractor(
        ExtractedPages(
            ("readable text " * 50, "", "more readable text " * 40),
            (PageFailure(2, "damaged content stream"),),
        )
    )
    service = ExtractionService(
        _settings(tmp_path), PathResolver(source), store, extractor=extractor
    )

    result = service.extract("doeUseful2026")
    cached = service.extract("doeUseful2026")

    assert result.status == cached.status == "partial"
    assert result.diagnostics.empty_pages == (2,)
    assert result.diagnostics.page_failures[0].page == 2
    assert any("Page 2 extraction failed" in warning for warning in result.diagnostics.warnings)
    assert any("Page 2 extraction failed" in warning for warning in cached.diagnostics.warnings)
    assert "<!-- PAGE 3 -->\n\nmore readable text" in result.output_path.read_text(
        encoding="utf-8"
    )
    assert len(extractor.calls) == 1


def test_quality_diagnostics_warn_for_empty_low_text_and_scanned_appearance(
    tmp_path: Path,
) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"placeholder")
    extractor = CountingExtractor(ExtractedPages(("tiny", "", "", "", "substantial")))
    service = ExtractionService(
        _settings(tmp_path), PathResolver(source), _paper_store(tmp_path), extractor=extractor
    )

    result = service.extract("doeUseful2026")
    diagnostics = result.diagnostics
    cached = service.extract("doeUseful2026")

    assert diagnostics.characters_per_page == (4, 0, 0, 0, 11)
    assert diagnostics.empty_pages == (2, 3, 4)
    assert diagnostics.total_characters == 15
    assert diagnostics.low_text_fraction == 1.0
    assert diagnostics.average_characters_per_page == 3.0
    assert result.status == cached.status == "partial"
    assert "More than 20% of pages are empty." in diagnostics.warnings
    assert "Average extracted text per page is suspiciously low." in diagnostics.warnings
    assert "The PDF appears scanned; OCR is not supported." in diagnostics.warnings


@pytest.mark.parametrize("texts", [(), ("", "")])
def test_no_usable_text_raises_and_does_not_create_cache(
    tmp_path: Path, texts: tuple[str, ...]
) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"placeholder")
    service = ExtractionService(
        _settings(tmp_path),
        PathResolver(source),
        _paper_store(tmp_path),
        extractor=CountingExtractor(ExtractedPages(texts)),
    )

    with pytest.raises(PDFExtractionError, match="no usable extractable text.*OCR"):
        service.extract("doeUseful2026")

    assert not service.cache_path("doeUseful2026").exists()


def test_encrypted_and_unopenable_pdfs_raise_clear_domain_errors(tmp_path: Path) -> None:
    encrypted = tmp_path / "encrypted.pdf"
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "secret")
    document.save(
        encrypted,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner",
        user_pw="reader",
    )
    document.close()
    extractor = PyMuPDFTextExtractor()

    with pytest.raises(PDFExtractionError, match="encrypted.*password"):
        extractor.extract(encrypted)

    unreadable = tmp_path / "broken.pdf"
    unreadable.write_bytes(b"not a PDF")
    with pytest.raises(PDFExtractionError, match="Could not open PDF"):
        extractor.extract(unreadable)


def test_missing_note_attachment_and_source_have_actionable_errors(tmp_path: Path) -> None:
    source = tmp_path / "missing.pdf"
    settings = _settings(tmp_path)
    empty_store = MarkdownStore(tmp_path / "vault" / "Literature" / "Papers")
    service = ExtractionService(settings, PathResolver(source), empty_store)
    with pytest.raises(PDFExtractionError, match="run research sync first"):
        service.extract("doeUseful2026")

    no_attachment = _paper_store(tmp_path, attachment_key=None)
    service = ExtractionService(settings, PathResolver(source), no_attachment)
    with pytest.raises(PDFNotFoundError, match="has no PDF attachment"):
        service.extract("doeUseful2026")

    attached_store = MarkdownStore(tmp_path / "other" / "Literature" / "Papers")
    attached_store.create(
        PaperNote(
            zotero_key="ABCD1234",
            citekey="doeUseful2026",
            title="Useful paper",
            pdf_attachment_key="PDFX5678",
        )
    )
    service = ExtractionService(settings, PathResolver(source), attached_store)
    with pytest.raises(PDFNotFoundError, match="is unavailable"):
        service.extract("doeUseful2026")


def test_unsafe_or_mismatched_citekey_never_selects_another_note(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"placeholder")
    store = _paper_store(tmp_path)
    service = ExtractionService(
        _settings(tmp_path),
        PathResolver(source),
        store,
        extractor=CountingExtractor(ExtractedPages(("text",))),
    )

    with pytest.raises(PDFExtractionError, match="Invalid citation key"):
        service.extract("../doeUseful2026")

    path = store.note_path("doeUseful2026")
    path.write_text(path.read_text(encoding="utf-8").replace(
        "citekey: doeUseful2026", "citekey: anotherKey"
    ), encoding="utf-8")
    with pytest.raises(PDFExtractionError, match="declares citation key 'anotherKey'"):
        service.extract("doeUseful2026")


def test_malformed_cache_is_regenerated_instead_of_trusted(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"placeholder")
    extractor = CountingExtractor(ExtractedPages(("readable text " * 50,)))
    service = ExtractionService(
        _settings(tmp_path), PathResolver(source), _paper_store(tmp_path), extractor=extractor
    )
    cache = service.cache_path("doeUseful2026")
    cache.parent.mkdir(parents=True)
    cache.write_text("---\ncitekey: wrong\ncitekey: duplicate\n---\n", encoding="utf-8")

    result = service.extract("doeUseful2026")

    assert result.cache_hit is False
    assert len(extractor.calls) == 1
    assert "<!-- PAGE 1 -->" in cache.read_text(encoding="utf-8")


def test_review_context_auto_extracts_and_returns_all_required_paths(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"placeholder")
    settings = _settings(tmp_path)
    extractor = CountingExtractor(ExtractedPages(("readable text " * 50,)))
    service = ExtractionService(
        settings, PathResolver(source), _paper_store(tmp_path), extractor=extractor
    )

    context = service.review_context("doeUseful2026")

    assert context.paper_note == settings.papers_dir / "doeUseful2026.md"
    assert context.extracted_paper == settings.paper_text_dir / "doeUseful2026.md"
    assert context.reading_profile == settings.reading_profile_path
    assert context.tag_registry == settings.tag_registry_path
    assert context.extracted_paper.exists()


def test_extraction_metadata_rejects_extra_or_malformed_yaml_values() -> None:
    valid: dict[str, object] = {
        "citekey": "doeUseful2026",
        "zotero_key": "ABCD1234",
        "attachment_key": "PDFX5678",
        "source_mtime": 123.5,
        "source_size": 100,
        "extracted_at": "2026-08-06T12:00:00+02:00",
        "extractor": "pymupdf",
        "extractor_version": "1.28.0",
        "pages": 2,
    }
    assert ExtractionMetadata.model_validate(valid).pages == 2

    with pytest.raises(ValidationError):
        ExtractionMetadata.model_validate({**valid, "unknown": True})
    with pytest.raises(ValidationError):
        ExtractionMetadata.model_validate({**valid, "attachment_key": "../unsafe"})
    with pytest.raises(ValidationError):
        ExtractionMetadata.model_validate({**valid, "source_size": "100"})
    with pytest.raises(ValidationError):
        ExtractionMetadata.model_validate({**valid, "pages": 0})
    with pytest.raises(ValidationError):
        ExtractionMetadata.model_validate({**valid, "extracted_at": "2026-08-06T12:00:00"})
    with pytest.raises(ValidationError, match="failed_pages must not exceed pages"):
        ExtractionMetadata.model_validate({**valid, "failed_pages": [3]})
