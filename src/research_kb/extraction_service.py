"""Page-aware, cached PDF text extraction for synchronized paper notes."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

import pymupdf
import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError as PydanticValidationError

from research_kb.config import LibraryType, Settings
from research_kb.exceptions import PDFExtractionError, PDFNotFoundError
from research_kb.markdown_store import MarkdownStore, PaperDocument
from research_kb.models import ExtractionMetadata

_PAGE_MARKER = re.compile(r"(?m)^<!-- PAGE ([1-9][0-9]*) -->$")
_LOW_TEXT_CHARACTERS = 100
_SUSPICIOUS_AVERAGE_CHARACTERS = 500
_SCANNED_LOW_TEXT_FRACTION = 0.8


class AttachmentPathResolver(Protocol):
    """The narrow Zotero capability needed by extraction."""

    def resolve_attachment_path(
        self,
        attachment_key: str,
        *,
        library_type: LibraryType,
        library_id: int,
    ) -> Path: ...


@dataclass(frozen=True)
class PageFailure:
    """One page PyMuPDF could not read while other pages remained usable."""

    page: int
    error: str


@dataclass(frozen=True)
class ExtractedPages:
    """Raw per-page output from a PDF text extractor."""

    texts: tuple[str, ...]
    failures: tuple[PageFailure, ...] = ()


class PDFTextExtractor(Protocol):
    """Injectable PDF boundary used to test cache and failure behavior."""

    @property
    def version(self) -> str: ...

    def extract(self, path: Path) -> ExtractedPages: ...


class PyMuPDFTextExtractor:
    """Extract each PDF page independently so isolated failures stay partial."""

    @property
    def version(self) -> str:
        return pymupdf.__version__

    def extract(self, path: Path) -> ExtractedPages:
        try:
            document = pymupdf.open(path)  # type: ignore[no-untyped-call]
        except Exception as error:
            raise PDFExtractionError(f"Could not open PDF {path}: {error}") from error

        try:
            if document.needs_pass:
                raise PDFExtractionError(
                    f"PDF {path} is encrypted and cannot be extracted without a password."
                )
            texts: list[str] = []
            failures: list[PageFailure] = []
            for index in range(document.page_count):
                try:
                    text = document.load_page(index).get_text("text")  # type: ignore[no-untyped-call]
                except Exception as error:
                    texts.append("")
                    failures.append(PageFailure(index + 1, str(error) or type(error).__name__))
                else:
                    texts.append(text.strip())
            return ExtractedPages(tuple(texts), tuple(failures))
        finally:
            document.close()  # type: ignore[no-untyped-call]


@dataclass(frozen=True)
class ExtractionDiagnostics:
    """Quality measures reported after both fresh and cached extraction."""

    pages: int
    characters_per_page: tuple[int, ...]
    empty_pages: tuple[int, ...]
    total_characters: int
    low_text_fraction: float
    average_characters_per_page: float
    page_failures: tuple[PageFailure, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ExtractionResult:
    """One successful extraction or cache lookup."""

    citekey: str
    output_path: Path
    attachment_path: Path
    cache_hit: bool
    status: Literal["complete", "partial"]
    diagnostics: ExtractionDiagnostics


@dataclass(frozen=True)
class ReviewContext:
    """The complete, explicit set of local files an agent should inspect."""

    paper_note: Path
    extracted_paper: Path
    reading_profile: Path
    tag_registry: Path


class ExtractionService:
    """Resolve a synchronized note, extract its PDF, and maintain its cache."""

    def __init__(
        self,
        settings: Settings,
        path_resolver: AttachmentPathResolver,
        markdown_store: MarkdownStore,
        *,
        extractor: PDFTextExtractor | None = None,
    ) -> None:
        self.settings = settings
        self.path_resolver = path_resolver
        self.markdown_store = markdown_store
        self.extractor = extractor or PyMuPDFTextExtractor()

    def extract(self, citekey: str, *, force: bool = False) -> ExtractionResult:
        """Return a valid cache, regenerating it atomically when required."""
        document, _note_path = self._paper_document(citekey)
        note = document.note
        attachment_key = note.pdf_attachment_key
        if attachment_key is None:
            raise PDFNotFoundError(
                f"Paper {citekey!r} has no PDF attachment; run research sync after attaching one."
            )
        if re.fullmatch(r"[A-Z0-9]{8}", attachment_key) is None:
            raise PDFNotFoundError(
                f"Paper {citekey!r} has an invalid PDF attachment key {attachment_key!r}."
            )

        attachment_path = self.path_resolver.resolve_attachment_path(
            attachment_key,
            library_type=self.settings.zotero_library_type,
            library_id=self.settings.zotero_library_id,
        )
        try:
            source = attachment_path.stat()
        except OSError as error:
            raise PDFNotFoundError(
                f"PDF attachment {attachment_key!r} is unavailable at {attachment_path}: {error}"
            ) from error
        if not attachment_path.is_file():
            raise PDFNotFoundError(
                f"PDF attachment {attachment_key!r} is not a file: {attachment_path}"
            )

        output_path = self.cache_path(citekey)
        if not force:
            cached = self._read_valid_cache(
                output_path,
                citekey=citekey,
                zotero_key=note.zotero_key,
                attachment_key=attachment_key,
                source_mtime=source.st_mtime,
                source_size=source.st_size,
            )
            if cached is not None:
                metadata, texts = cached
                failures = tuple(
                    PageFailure(page, "failure recorded in extraction cache")
                    for page in metadata.failed_pages
                )
                diagnostics = self._diagnostics(texts, failures)
                return ExtractionResult(
                    citekey,
                    output_path,
                    attachment_path,
                    True,
                    self._status(diagnostics),
                    diagnostics,
                )

        pages = self.extractor.extract(attachment_path)
        normalized_texts = tuple(self._normalize_page_text(text) for text in pages.texts)
        diagnostics = self._diagnostics(normalized_texts, pages.failures)
        if diagnostics.total_characters == 0:
            detail = ""
            if pages.failures:
                detail = f" ({len(pages.failures)} page extraction failure(s))"
            raise PDFExtractionError(
                f"PDF {attachment_path} contains no usable extractable text{detail}; OCR is not "
                "supported."
            )
        metadata = ExtractionMetadata(
            citekey=citekey,
            zotero_key=note.zotero_key,
            attachment_key=attachment_key,
            source_mtime=source.st_mtime,
            source_size=source.st_size,
            extracted_at=datetime.now().astimezone(),
            extractor_version=self.extractor.version,
            pages=len(normalized_texts),
            failed_pages=tuple(failure.page for failure in pages.failures),
        )
        self._atomic_write(output_path, self._render(metadata, normalized_texts))
        return ExtractionResult(
            citekey,
            output_path,
            attachment_path,
            False,
            self._status(diagnostics),
            diagnostics,
        )

    def review_context(self, citekey: str) -> ReviewContext:
        """Ensure extraction is current, then return the four review inputs."""
        extracted = self.extract(citekey)
        note_path = self.markdown_store.note_path(citekey)
        return ReviewContext(
            paper_note=note_path,
            extracted_paper=extracted.output_path,
            reading_profile=self.settings.reading_profile_path,
            tag_registry=self.settings.tag_registry_path,
        )

    def cache_path(self, citekey: str) -> Path:
        """Resolve a cache path without permitting directory traversal."""
        try:
            note_path = self.markdown_store.note_path(citekey)
        except ValueError as error:
            raise PDFExtractionError(f"Invalid citation key {citekey!r}.") from error
        return self.settings.paper_text_dir / note_path.name

    def _paper_document(self, citekey: str) -> tuple[PaperDocument, Path]:
        try:
            path = self.markdown_store.note_path(citekey)
        except ValueError as error:
            raise PDFExtractionError(f"Invalid citation key {citekey!r}.") from error
        if not path.is_file():
            raise PDFExtractionError(
                f"Paper note for {citekey!r} was not found at {path}; run research sync first."
            )
        document = self.markdown_store.parse(path)
        if document.note.citekey != citekey:
            raise PDFExtractionError(
                f"Paper note {path} declares citation key {document.note.citekey!r}, not "
                f"{citekey!r}."
            )
        return document, path

    def _read_valid_cache(
        self,
        path: Path,
        *,
        citekey: str,
        zotero_key: str,
        attachment_key: str,
        source_mtime: float,
        source_size: int,
    ) -> tuple[ExtractionMetadata, tuple[str, ...]] | None:
        try:
            raw = path.read_text(encoding="utf-8")
            metadata, body = self._parse_cache(path, raw)
        except (OSError, PDFExtractionError):
            return None
        if (
            metadata.citekey != citekey
            or metadata.zotero_key != zotero_key
            or metadata.attachment_key != attachment_key
            or metadata.source_mtime != source_mtime
            or metadata.source_size != source_size
            or metadata.extractor_version != self.extractor.version
        ):
            return None
        texts = self._cached_page_texts(body, metadata.pages)
        if texts is None or any(page > metadata.pages for page in metadata.failed_pages):
            return None
        return metadata, texts

    @staticmethod
    def _parse_cache(path: Path, raw: str) -> tuple[ExtractionMetadata, str]:
        if not raw.startswith("---\n"):
            raise PDFExtractionError(f"{path}: extraction cache has no YAML frontmatter.")
        closing = raw.find("\n---\n", 4)
        if closing < 0:
            raise PDFExtractionError(f"{path}: extraction cache has unclosed YAML frontmatter.")
        yaml_text = raw[4:closing]
        try:
            node = yaml.compose(yaml_text, Loader=yaml.SafeLoader)
            if not isinstance(node, yaml.MappingNode):
                raise PDFExtractionError(f"{path}: extraction metadata must be a mapping.")
            keys = [
                key.value
                for key, _value in node.value
                if isinstance(key, yaml.ScalarNode) and isinstance(key.value, str)
            ]
            if len(keys) != len(set(keys)):
                raise PDFExtractionError(f"{path}: extraction metadata has duplicate fields.")
            payload = yaml.safe_load(yaml_text)
            metadata = ExtractionMetadata.model_validate(payload)
        except yaml.YAMLError as error:
            raise PDFExtractionError(f"{path}: invalid extraction metadata: {error}") from error
        except PydanticValidationError as error:
            raise PDFExtractionError(f"{path}: invalid extraction metadata: {error}") from error
        return metadata, raw[closing + 5 :]

    @staticmethod
    def _cached_page_texts(body: str, expected_pages: int) -> tuple[str, ...] | None:
        markers = list(_PAGE_MARKER.finditer(body))
        if len(markers) != expected_pages:
            return None
        if tuple(int(marker.group(1)) for marker in markers) != tuple(
            range(1, expected_pages + 1)
        ):
            return None
        texts: list[str] = []
        for index, marker in enumerate(markers):
            end = markers[index + 1].start() if index + 1 < len(markers) else len(body)
            texts.append(body[marker.end() : end].strip())
        return tuple(texts)

    @staticmethod
    def _diagnostics(
        texts: tuple[str, ...], failures: tuple[PageFailure, ...]
    ) -> ExtractionDiagnostics:
        characters = tuple(len(text) for text in texts)
        pages = len(texts)
        empty_pages = tuple(index for index, count in enumerate(characters, 1) if count == 0)
        total = sum(characters)
        low_text_pages = sum(count < _LOW_TEXT_CHARACTERS for count in characters)
        low_fraction = low_text_pages / pages if pages else 1.0
        average = total / pages if pages else 0.0
        warnings: list[str] = []
        if pages and len(empty_pages) / pages > 0.2:
            warnings.append("More than 20% of pages are empty.")
        if average < _SUSPICIOUS_AVERAGE_CHARACTERS:
            warnings.append("Average extracted text per page is suspiciously low.")
        if pages and low_fraction >= _SCANNED_LOW_TEXT_FRACTION:
            warnings.append("The PDF appears scanned; OCR is not supported.")
        for failure in failures:
            warnings.append(f"Page {failure.page} extraction failed: {failure.error}")
        return ExtractionDiagnostics(
            pages=pages,
            characters_per_page=characters,
            empty_pages=empty_pages,
            total_characters=total,
            low_text_fraction=low_fraction,
            average_characters_per_page=average,
            page_failures=failures,
            warnings=tuple(warnings),
        )

    @staticmethod
    def _status(diagnostics: ExtractionDiagnostics) -> Literal["complete", "partial"]:
        """Treat every specified extraction-quality warning as partial coverage."""
        return "partial" if diagnostics.warnings else "complete"

    @staticmethod
    def _normalize_page_text(text: str) -> str:
        """Canonicalize line endings before measuring or persisting extracted text."""
        return text.replace("\r\n", "\n").replace("\r", "\n").strip()

    @staticmethod
    def _render(metadata: ExtractionMetadata, texts: tuple[str, ...]) -> str:
        frontmatter = yaml.safe_dump(
            metadata.model_dump(mode="json"),
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        ).rstrip("\n")
        sections = [f"# Extracted paper: {metadata.citekey}"]
        for page, text in enumerate(texts, 1):
            section = f"<!-- PAGE {page} -->"
            if text:
                section = f"{section}\n\n{text}"
            sections.append(section)
        return f"---\n{frontmatter}\n---\n\n" + "\n\n".join(sections) + "\n"

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
        temporary_path = Path(temporary)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)
