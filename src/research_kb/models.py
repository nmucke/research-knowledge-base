"""Typed domain-model skeletons expanded by later implementation steps."""

from pydantic import BaseModel, ConfigDict


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
    """Validated paper-note state (fields added in step 5)."""


class AIReviewMetadata(DomainModel):
    """Validated AI-review provenance (fields added with review validation)."""


class SyncState(DomainModel):
    """Validated incremental-sync state (fields added in step 7)."""


class ExtractionMetadata(DomainModel):
    """Validated extraction cache metadata (fields added in step 9)."""


class TagRegistry(DomainModel):
    """Validated controlled-tag registry (fields added with tag support)."""
