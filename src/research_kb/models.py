"""Typed domain models for Zotero data and paper-note frontmatter."""

import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, field_validator


class DomainModel(BaseModel):
    """Strict immutable base for validated external and frontmatter data."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ZoteroItem(DomainModel):
    """Validated Zotero bibliographic item returned by the local API."""

    key: str
    version: int
    library_id: int
    item_type: str
    title: str
    creators: tuple["ZoteroCreator", ...]
    date: str | None = None
    publication: str | None = None
    doi: str | None = None
    url: str | None = None
    abstract: str | None = None
    tags: tuple[str, ...] = ()
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    collections: tuple[str, ...] = ()
    date_added: str | None = None
    date_modified: str | None = None

    @field_validator("date_added", "date_modified")
    @classmethod
    def timestamps_must_be_iso_dates_or_timestamps(cls, value: str | None) -> str | None:
        """Keep synced timestamps parseable instead of clearing them on malformed data."""
        if value is None:
            return None
        try:
            if "T" in value:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            else:
                date.fromisoformat(value)
        except ValueError:
            raise ValueError("must be an ISO date or timestamp") from None
        return value

    @property
    def authors(self) -> tuple["ZoteroCreator", ...]:
        """Creators Zotero identifies as authors."""
        return tuple(creator for creator in self.creators if creator.creator_type == "author")


class ZoteroCreator(DomainModel):
    """A Zotero item creator in either personal or single-name form."""

    first_name: str = ""
    last_name: str = ""
    name: str = ""
    creator_type: str

    @property
    def display_name(self) -> str:
        """Human-readable creator name."""
        return self.name or " ".join(part for part in (self.first_name, self.last_name) if part)


class ZoteroAttachment(DomainModel):
    """Validated Zotero attachment (fields added in step 8)."""


class PaperNote(DomainModel):
    """Validated, complete frontmatter for a single paper note."""

    schema_version: Literal[1] = 1
    type: Literal["paper"] = "paper"
    zotero_key: str
    zotero_version: int | None = None
    zotero_server_id: str | None = None
    citekey: str
    title: str
    authors: tuple[str, ...] = ()
    year: int | None = None
    publication: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    doi: str | None = None
    url: str | None = None
    abstract: str | None = None
    zotero_collections: tuple[str, ...] = ()
    zotero_tags: tuple[str, ...] = ()
    zotero_uri: str | None = None
    pdf_attachment_key: str | None = None
    pdf_uri: str | None = None
    date_added: date | None = None
    date_modified: date | None = None
    human_read_status: Literal["unread", "queued", "skimming", "reading", "read"] = "unread"
    human_read_date: date | None = None
    human_rating: int | None = None
    human_priority: int | None = None
    human_relevance: int | None = None
    ai_review_status: Literal["not-reviewed", "queued", "reviewed", "failed", "outdated"] = (
        "not-reviewed"
    )
    ai_review_scope: (
        Literal["metadata-only", "abstract-only", "partial-text", "full-text"] | None
    ) = None
    ai_review_coverage: Literal["complete", "partial", "unknown"] | None = None
    ai_review_agent: str | None = None
    ai_review_model: str | None = None
    ai_review_date: date | None = None
    ai_review_version: int = 0
    ai_review_human_verified: StrictBool = False
    ai_recommendation: Literal["must-read", "read", "skim", "skip", "uncertain"] | None = None
    ai_recommendation_reason: str | None = None
    ai_relevance: int | None = None
    ai_recommendation_confidence: Literal["low", "medium", "high"] | None = None
    tags: tuple[str, ...] = ()
    ai_applied_tags: tuple[str, ...] = ()
    ai_suggested_tags: tuple[str, ...] = ()
    zotero_tag_sync: Literal["not-synced", "synced"] = "not-synced"
    zotero_tag_sync_date: date | None = None
    zotero_missing: StrictBool = False

    @field_validator("zotero_key")
    @classmethod
    def zotero_key_must_be_an_eight_character_key(cls, value: str) -> str:
        """Keep Zotero identity keys safe to use in note and URI paths."""
        if re.fullmatch(r"[A-Z0-9]{8}", value) is None:
            msg = "zotero_key must be exactly eight uppercase alphanumeric characters"
            raise ValueError(msg)
        return value

    @field_validator("citekey")
    @classmethod
    def citekey_must_be_a_safe_filename_component(cls, value: str) -> str:
        """Reject empty and path-like Better BibTeX citation keys."""
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._$^-]*", value) is None:
            msg = "citekey must be a nonempty safe filename component"
            raise ValueError(msg)
        return value

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        """Require the title identity field to contain visible text."""
        if not value.strip():
            raise ValueError("title must not be blank")
        return value

    @classmethod
    def from_zotero(
        cls,
        item: ZoteroItem,
        citekey: str,
        zotero_server_id: str | None,
        *,
        library_type: Literal["user", "group"] = "user",
    ) -> "PaperNote":
        """Build a safe new note from the Zotero data currently available."""
        if library_type == "user":
            zotero_uri = f"zotero://select/library/items/{item.key}"
        else:
            zotero_uri = f"zotero://select/groups/{item.library_id}/items/{item.key}"

        year_match = re.match(r"\s*(\d{4})", item.date or "")
        return cls(
            zotero_key=item.key,
            zotero_version=item.version,
            zotero_server_id=zotero_server_id,
            citekey=citekey,
            title=item.title,
            authors=tuple(author.display_name for author in item.authors),
            year=int(year_match.group(1)) if year_match else None,
            publication=item.publication,
            volume=item.volume,
            issue=item.issue,
            pages=item.pages,
            doi=item.doi,
            url=item.url,
            abstract=item.abstract,
            zotero_collections=item.collections,
            zotero_tags=item.tags,
            zotero_uri=zotero_uri,
            date_added=cls._zotero_date(item.date_added),
            date_modified=cls._zotero_date(item.date_modified),
        )

    @staticmethod
    def _zotero_date(value: str | None) -> date | None:
        """Extract the calendar date from Zotero's ISO-like timestamps."""
        if value is None:
            return None
        return date.fromisoformat(value[:10])


class AIReviewMetadata(DomainModel):
    """Validated AI-review provenance (fields added with review validation)."""


class SyncState(DomainModel):
    """Validated incremental-sync state (fields added in step 7)."""

    schema_version: Literal[1] = 1
    server_id: str
    library_type: Literal["user", "group"]
    library_id: StrictInt
    library_version: StrictInt

    @field_validator("server_id")
    @classmethod
    def server_id_must_not_be_blank(cls, value: str) -> str:
        """A sync cursor is meaningful only for a concrete Zotero server."""
        if not value.strip():
            raise ValueError("server_id must not be blank")
        return value

    @field_validator("library_id", "library_version")
    @classmethod
    def nonnegative_integer(cls, value: int) -> int:
        """Reject negative cursors and library identifiers."""
        if value < 0:
            raise ValueError("must be a non-negative integer")
        return value


class ZoteroItemBatch(DomainModel):
    """A consistent page sequence of Zotero items and its library version."""

    items: tuple[ZoteroItem, ...]
    library_version: StrictInt
    removed_item_keys: tuple[str, ...] = ()

    @field_validator("library_version")
    @classmethod
    def library_version_must_be_nonnegative(cls, value: int) -> int:
        """Zotero library versions are non-negative integer cursors."""
        if value < 0:
            raise ValueError("library_version must be a non-negative integer")
        return value

    @field_validator("removed_item_keys")
    @classmethod
    def removed_item_keys_must_be_zotero_keys(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Retain only valid Zotero identities in a deletion batch."""
        if any(re.fullmatch(r"[A-Z0-9]{8}", key) is None for key in value):
            raise ValueError("removed_item_keys must contain eight-character Zotero keys")
        return value


class ExtractionMetadata(DomainModel):
    """Validated extraction cache metadata (fields added in step 9)."""


class TagRegistry(DomainModel):
    """Validated controlled-tag registry (fields added with tag support)."""
