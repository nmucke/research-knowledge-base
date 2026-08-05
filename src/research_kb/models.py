"""Typed domain-model skeletons expanded by later implementation steps."""

from pydantic import BaseModel, ConfigDict


class DomainModel(BaseModel):
    """Strict immutable base for validated external and frontmatter data."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ZoteroItem(DomainModel):
    """Validated Zotero bibliographic item (fields added in step 3)."""


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
