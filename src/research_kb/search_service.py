"""Deterministic, file-backed literature retrieval without a secondary database."""

from __future__ import annotations

import re
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import Field

from research_kb.config import Settings
from research_kb.exceptions import ResearchKBError
from research_kb.markdown_store import MarkdownStore, block_content, read_frontmatter
from research_kb.models import DomainModel, PaperNote
from research_kb.project_registry import load_projects

_STOP = frozenset("a an and are as at be by for from in is it of on or the to with".split())


def terms(text: str) -> set[str]:
    return set(re.findall(r"[\w]+", text.casefold())) - _STOP


def safe_file(path: Path, root: Path) -> Path:
    """Reject redirected roots, ancestors, or files before reading private content."""
    if root.is_symlink() or path.is_symlink():
        raise ValueError("Symlinks are not supported for research content.")
    resolved_root = root.resolve()
    if not path.resolve().is_relative_to(resolved_root):
        raise ValueError("Requested content is outside the selected workspace.")
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError("Research content must not traverse symlinks.")
        if parent == root:
            break
    if not path.is_file():
        raise ValueError(f"Research content does not exist: {path.name}")
    return path


class SearchRequest(DomainModel):
    query: str = Field(default="", max_length=2000)
    tags: tuple[str, ...] = ()
    project: str | None = None
    author: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    review_status: Literal["not-reviewed", "queued", "reviewed", "failed", "outdated"] | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class SearchHit(DomainModel):
    citekey: str
    title: str
    authors: tuple[str, ...]
    year: int | None
    doi: str | None
    url: str | None
    tags: tuple[str, ...]
    projects: tuple[str, ...]
    review_status: str
    score: int
    reasons: tuple[str, ...]
    snippet: str
    identity: str


class SearchResult(DomainModel):
    hits: tuple[SearchHit, ...]
    total: int
    scanned: int
    skipped: tuple[str, ...]
    next_offset: int | None


def bibliographic_identity(note: PaperNote) -> str:
    """Prefer durable identifiers for discovery deduplication across citation keys."""
    if note.doi:
        doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", note.doi.strip(), flags=re.I)
        return f"doi:{doi.casefold()}"
    match = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?", note.url or "")
    if match:
        return f"arxiv:{match.group(1)}"
    title = " ".join(re.findall(r"\w+", note.title.casefold()))
    return f"title:{title}|year:{note.year}"


class SearchService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = MarkdownStore(settings.papers_dir)

    def search(self, request: SearchRequest) -> SearchResult:
        if request.year_from and request.year_to and request.year_from > request.year_to:
            raise ValueError("year_from must not be after year_to")
        if request.tags:
            from research_kb.catalog_operations import resolve_tag_alias

            request = request.model_copy(
                update={
                    "tags": tuple(resolve_tag_alias(self.settings, tag) for tag in request.tags)
                }
            )
        query_terms = terms(request.query)
        hits: list[SearchHit] = []
        skipped: list[str] = []
        paths = sorted(self.settings.papers_dir.glob("*.md"))
        for path in paths:
            try:
                safe_file(path, self.settings.vault_path)
                document = self.store.parse(path)
                note = document.note
                if path.stem != note.citekey:
                    raise ValueError("Citation key does not match filename")
                review = block_content(path, "AI_REVIEW") or ""
            except (ResearchKBError, ValueError, OSError):
                skipped.append(path.name)
                continue
            if not self._matches(note, request):
                continue
            fields = {
                "title": (terms(note.title), 5),
                "abstract": (terms(note.abstract or ""), 2),
                "tags": (terms(" ".join(note.tags)), 4),
                "review": (terms(review), 1),
                "authors": (terms(" ".join(note.authors)), 2),
                "identifier": (terms(f"{note.citekey} {note.doi or ''}"), 5),
            }
            reasons = tuple(
                f"{name}: {', '.join(sorted(query_terms & values))}"
                for name, (values, _weight) in fields.items()
                if query_terms & values
            )
            score = sum(len(query_terms & values) * weight for values, weight in fields.values())
            if query_terms and not score:
                continue
            hits.append(
                SearchHit(
                    citekey=note.citekey,
                    title=note.title,
                    authors=note.authors,
                    year=note.year,
                    doi=note.doi,
                    url=note.url,
                    tags=note.tags,
                    projects=note.projects,
                    review_status=note.ai_review_status,
                    score=score,
                    reasons=reasons or ("Matches requested filters",),
                    snippet=(note.abstract or "No abstract available.")[:450],
                    identity=bibliographic_identity(note),
                )
            )
        hits.sort(key=lambda hit: (-hit.score, -(hit.year or 0), hit.citekey))
        end = request.offset + request.limit
        return SearchResult(
            hits=tuple(hits[request.offset : end]),
            total=len(hits),
            scanned=len(paths),
            skipped=tuple(skipped),
            next_offset=end if end < len(hits) else None,
        )

    @staticmethod
    def _matches(note: PaperNote, request: SearchRequest) -> bool:
        return not (
            (request.tags and not set(request.tags).issubset(note.tags))
            or (request.project is not None and request.project not in note.projects)
            or (
                request.author
                and request.author.casefold() not in " ".join(note.authors).casefold()
            )
            or (
                request.year_from is not None
                and (note.year is None or note.year < request.year_from)
            )
            or (request.year_to is not None and (note.year is None or note.year > request.year_to))
            or (request.review_status and note.ai_review_status != request.review_status)
        )

    def paper(self, citekey: str) -> dict[str, object]:
        path = safe_file(self.store.note_path(citekey), self.settings.vault_path)
        note = self.store.parse(path).note
        return {
            "metadata": note.model_dump(mode="json"),
            "review": block_content(path, "AI_REVIEW"),
            "revision": sha256(path.read_bytes()).hexdigest(),
            "identity": bibliographic_identity(note),
        }

    def projects(self, project_id: str | None = None) -> dict[str, object]:
        for path in self.settings.projects_dir.glob("*.md"):
            safe_file(path, self.settings.vault_path)
        projects = load_projects(self.settings.projects_dir)
        result: list[dict[str, object]] = []
        for project in projects:
            if project_id is not None and project.project_id != project_id:
                continue
            path = safe_file(
                self.settings.projects_dir / f"{project.project_id}.md", self.settings.vault_path
            )
            _, body = read_frontmatter(path)
            result.append(
                {
                    "metadata": project.model_dump(mode="json"),
                    "brief": body if project_id else body.split("## Related papers", 1)[0],
                    "revision": sha256(path.read_bytes()).hexdigest(),
                }
            )
        if project_id is not None and not result:
            raise ValueError(f"Unknown project: {project_id}")
        return {"projects": result}

    def text(self, citekey: str, start_page: int = 1, end_page: int = 10) -> dict[str, object]:
        if start_page < 1 or end_page < start_page or end_page - start_page >= 20:
            raise ValueError("Request between one and twenty pages, starting at page one or later.")
        note = self.store.parse(
            safe_file(self.store.note_path(citekey), self.settings.vault_path)
        ).note
        path = safe_file(self.settings.paper_text_dir / f"{citekey}.md", self.settings.vault_path)
        from research_kb.extraction_service import ExtractionService

        metadata, body = ExtractionService._parse_cache(path, path.read_text(encoding="utf-8"))
        if (
            metadata.zotero_key != note.zotero_key
            or metadata.attachment_key != note.pdf_attachment_key
        ):
            raise ValueError("Extraction cache does not match the paper; refresh review context.")
        pages = ExtractionService._cached_page_texts(body, metadata.pages)
        if pages is None:
            raise ValueError("Extraction page markers are invalid; refresh review context.")
        return {
            "citekey": citekey,
            "total_pages": metadata.pages,
            "provenance": metadata.model_dump(mode="json"),
            "pages": [
                {"page": index + 1, "text": pages[index]}
                for index in range(start_page - 1, min(end_page, metadata.pages))
            ],
            "next_page": end_page + 1 if end_page < metadata.pages else None,
        }
