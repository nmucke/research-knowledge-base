"""Create immutable AI-owned analyses without altering papers or project briefs."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal

import yaml  # type: ignore[import-untyped]
from pydantic import Field, HttpUrl, field_validator

from research_kb.config import Settings
from research_kb.exceptions import ValidationError
from research_kb.extraction_service import ExtractionService
from research_kb.markdown_store import MarkdownStore
from research_kb.models import DomainModel
from research_kb.search_service import safe_file


class EvidenceSource(DomainModel):
    citekey: str | None = None
    url: HttpUrl | None = None
    location: str = Field(min_length=1, max_length=1000)
    evidence_scope: Literal["metadata-only", "abstract-only", "partial-text", "full-text"]


class ArtifactRequest(DomainModel):
    artifact_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)
    title: str = Field(min_length=1, max_length=500)
    kind: Literal["synthesis", "detailed-review", "discovery"]
    question: str = Field(min_length=1, max_length=4000)
    content: str = Field(min_length=1, max_length=200_000)
    sources: tuple[EvidenceSource, ...] = Field(min_length=1, max_length=200)
    agent: str = Field(min_length=1, max_length=200)
    model: str | None = Field(default=None, max_length=200)

    @field_validator("sources")
    @classmethod
    def each_source_needs_an_identity(
        cls, sources: tuple[EvidenceSource, ...]
    ) -> tuple[EvidenceSource, ...]:
        if any(source.citekey is None and source.url is None for source in sources):
            raise ValueError("Each evidence source needs a citation key or source URL.")
        return sources


class ArtifactService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def save(self, request: ArtifactRequest) -> dict[str, object]:
        root = self.settings.obsidian_vault_path / "Literature" / "Analyses"
        for parent in (root, *root.parents):
            if parent.is_symlink():
                raise ValueError("Analysis output must not traverse symlinks.")
            if parent == self.settings.vault_path:
                break
        if not root.resolve().is_relative_to(self.settings.vault_path):
            raise ValueError("Analysis output must stay in the selected workspace.")
        sources: list[dict[str, object]] = []
        store = MarkdownStore(self.settings.papers_dir)
        for source in request.sources:
            entry = source.model_dump(mode="json")
            if source.citekey:
                path = safe_file(store.note_path(source.citekey), self.settings.vault_path)
                note = store.parse(path).note
                if source.evidence_scope == "abstract-only" and not note.abstract:
                    raise ValidationError("Abstract-only evidence requires an available abstract.")
                if source.evidence_scope in {"partial-text", "full-text"}:
                    cache = self.settings.paper_text_dir / f"{source.citekey}.md"
                    if not cache.is_file():
                        raise ValidationError(
                            f"{source.evidence_scope} evidence needs a matching extraction cache."
                        )
                    safe_file(cache, self.settings.vault_path)
                    raw = cache.read_text(encoding="utf-8")
                    extraction_metadata, body = ExtractionService._parse_cache(cache, raw)
                    pages = ExtractionService._cached_page_texts(body, extraction_metadata.pages)
                    if (
                        extraction_metadata.zotero_key != note.zotero_key
                        or extraction_metadata.attachment_key != note.pdf_attachment_key
                        or pages is None
                    ):
                        raise ValidationError("Evidence cache does not match this paper.")
                    if source.evidence_scope == "full-text" and extraction_metadata.failed_pages:
                        raise ValidationError(
                            "Full-text evidence cannot rely on failed extraction pages."
                        )
                    cited_pages = [
                        int(value)
                        for value in re.findall(r"(?:pages?|pp?\.)\s*(\d+)", source.location, re.I)
                    ]
                    if any(
                        page < 1 or page > extraction_metadata.pages or not pages[page - 1].strip()
                        for page in cited_pages
                    ):
                        raise ValidationError("Cited page is outside available extracted evidence.")
                    entry["extraction_revision"] = sha256(raw.encode()).hexdigest()
                    entry["extraction_pages"] = extraction_metadata.pages
                entry["zotero_key"] = note.zotero_key
                entry["note_revision"] = sha256(path.read_bytes()).hexdigest()
            sources.append(entry)
        metadata = {
            "type": "ai-analysis",
            "schema_version": 1,
            "ai_owned": True,
            "artifact_id": request.artifact_id,
            "title": request.title,
            "kind": request.kind,
            "question": request.question,
            "agent": request.agent,
            "model": request.model,
            "created": datetime.now(UTC).isoformat(),
            "human_verified": False,
            "sources": sources,
        }
        text = (
            "---\n"
            + yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True)
            + "---\n\n# "
            + " ".join(request.title.splitlines())
            + "\n\n"
            + request.content.rstrip()
            + "\n"
        )
        path = root / f"{request.artifact_id}.md"
        MarkdownStore._atomic_write(path, text, must_not_exist=True)
        return {
            "artifact_id": request.artifact_id,
            "path": str(path.relative_to(self.settings.vault_path)),
            "revision": sha256(text.encode()).hexdigest(),
        }
