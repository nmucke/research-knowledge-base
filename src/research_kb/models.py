"""Typed domain models for Zotero data and paper-note frontmatter."""

import re
from datetime import date, datetime
from hashlib import sha256
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    field_validator,
    model_validator,
)


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
    tag_entries: tuple["ZoteroTag", ...] = ()
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


class ZoteroTag(DomainModel):
    """One Zotero tag, retaining its optional automatic/manual type marker."""

    tag: str
    type: Literal[0, 1] | None = 0

    @field_validator("tag")
    @classmethod
    def tag_must_not_be_blank(cls, value: str) -> str:
        """Reject unusable empty tags while retaining Zotero's type information."""
        if not value.strip():
            raise ValueError("tag must not be blank")
        return value


class ZoteroAttachment(DomainModel):
    """A non-deleted PDF child attachment returned by Zotero."""

    key: str
    version: StrictInt
    parent_item: str
    content_type: Literal["application/pdf"]
    filename: str | None = None
    link_mode: str
    title: str | None = None
    date_modified: str
    mtime: StrictInt | None = None

    @field_validator("key", "parent_item")
    @classmethod
    def attachment_keys_must_be_valid(cls, value: str) -> str:
        """Keep attachment and parent identities valid for Zotero API paths."""
        if re.fullmatch(r"[A-Z0-9]{8}", value) is None:
            raise ValueError("must be exactly eight uppercase alphanumeric characters")
        return value

    @field_validator("version", "mtime")
    @classmethod
    def attachment_numbers_must_be_nonnegative(cls, value: int | None) -> int | None:
        """Reject impossible Zotero versions and file modification times."""
        if value is not None and value < 0:
            raise ValueError("must be non-negative")
        return value

    @field_validator("link_mode")
    @classmethod
    def link_mode_must_not_be_blank(cls, value: str) -> str:
        """Require the attachment storage mode used by Zotero."""
        if not value.strip():
            raise ValueError("link_mode must not be blank")
        return value

    @field_validator("date_modified")
    @classmethod
    def attachment_date_modified_must_be_an_iso_timestamp(cls, value: str) -> str:
        """Keep attachment preference ordering based on a valid timestamp."""
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("date_modified must be an ISO timestamp") from None
        if parsed.tzinfo is None:
            raise ValueError("date_modified must include a timezone")
        return value


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
    projects: tuple[str, ...] = ()
    ai_suggested_projects: tuple[str, ...] = ()
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
    """Validated provenance stored in an extracted-paper cache file."""

    citekey: str
    zotero_key: str
    attachment_key: str
    source_mtime: StrictFloat
    source_size: StrictInt
    extracted_at: datetime
    extractor: Literal["pymupdf"] = "pymupdf"
    extractor_version: str
    pages: StrictInt
    failed_pages: tuple[StrictInt, ...] = ()

    @field_validator("citekey")
    @classmethod
    def citekey_must_be_a_safe_filename_component(cls, value: str) -> str:
        """Use the same deliberately narrow filename alphabet as paper notes."""
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._$^-]*", value) is None:
            raise ValueError("citekey must be a nonempty safe filename component")
        return value

    @field_validator("zotero_key", "attachment_key")
    @classmethod
    def keys_must_be_eight_character_zotero_keys(cls, value: str) -> str:
        """Reject path-like or otherwise malformed Zotero identities."""
        if re.fullmatch(r"[A-Z0-9]{8}", value) is None:
            raise ValueError("must be exactly eight uppercase alphanumeric characters")
        return value

    @field_validator("source_mtime")
    @classmethod
    def source_mtime_must_be_nonnegative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("source_mtime must be non-negative")
        return value

    @field_validator("source_size")
    @classmethod
    def source_size_must_be_nonnegative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("source_size must be non-negative")
        return value

    @field_validator("pages")
    @classmethod
    def pages_must_be_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("pages must be positive")
        return value

    @field_validator("failed_pages")
    @classmethod
    def failed_pages_must_be_positive_and_unique(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(page <= 0 for page in value):
            raise ValueError("failed_pages must contain positive page numbers")
        if len(set(value)) != len(value):
            raise ValueError("failed_pages must not contain duplicates")
        return value

    @field_validator("extractor_version")
    @classmethod
    def extractor_version_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("extractor_version must not be blank")
        return value

    @field_validator("extracted_at")
    @classmethod
    def extracted_at_must_include_a_timezone(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("extracted_at must include a timezone")
        return value

    @model_validator(mode="after")
    def failed_pages_must_exist_in_document(self) -> Self:
        if any(page > self.pages for page in self.failed_pages):
            raise ValueError("failed_pages must not exceed pages")
        return self


TAG_NAMESPACES = ("domain", "method", "task", "property", "model", "data")
PROJECT_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class ProjectNote(DomainModel):
    """Validated frontmatter for a single project note."""

    schema_version: Literal[1] = 1
    type: Literal["project"] = "project"
    project_id: str
    title: str
    status: Literal["active", "paused", "done", "archived"] = "active"
    started: date | None = None
    target: date | None = None
    tags: tuple[str, ...] = ()

    @field_validator("project_id")
    @classmethod
    def project_id_must_be_a_slug(cls, value: str) -> str:
        """Keep the identifier safe as both a filename stem and a frontmatter value."""
        if PROJECT_ID.fullmatch(value) is None:
            raise ValueError("project_id must be lowercase kebab-case")
        return value

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        """Require the title identity field to contain visible text."""
        if not value.strip():
            raise ValueError("title must not be blank")
        return value


class TagRegistryEntry(DomainModel):
    """One controlled tag and its human-readable definition."""

    name: str
    definition: str


class TagRegistry(DomainModel):
    """Validated ordered controlled-tag registry."""

    entries: tuple[TagRegistryEntry, ...]

    @property
    def names(self) -> tuple[str, ...]:
        """Registered tag names in document order."""
        return tuple(entry.name for entry in self.entries)

    @property
    def namespaces(self) -> tuple[str, ...]:
        """Represented namespaces in canonical order."""
        present = {entry.name.partition("/")[0] for entry in self.entries}
        return tuple(namespace for namespace in TAG_NAMESPACES if namespace in present)


class TagPushPlan(DomainModel):
    """An immutable, auditable proposal for reconciling one paper's tags."""

    zotero_key: str
    citekey: str
    existing_zotero_tags: tuple[str, ...]
    existing_zotero_tag_entries: tuple[ZoteroTag, ...]
    approved_curated_tags: tuple[str, ...]
    ai_applied_tags: tuple[str, ...]
    suggested_tags: tuple[str, ...]
    pending_tags: tuple[str, ...]
    merged_zotero_tags: tuple[str, ...]
    merged_zotero_tag_entries: tuple[ZoteroTag, ...]

    @property
    def requires_push(self) -> bool:
        """Whether the plan adds a controlled, human-approved tag."""
        return bool(self.pending_tags)


class TagPushReport(DomainModel):
    """Immutable outcome of a tag reconciliation attempt."""

    plan: TagPushPlan
    dry_run: StrictBool
    pushed: StrictBool
    note_updated: StrictBool
    attempts: StrictInt


class ReviewSnapshot(DomainModel):
    """Protected user-owned state captured before an agent review workflow."""

    schema_version: Literal[1] = 1
    zotero_key: str
    citekey: str
    human_read_status: Literal["unread", "queued", "skimming", "reading", "read"]
    human_read_date: date | None
    human_rating: int | None
    human_priority: int | None
    human_relevance: int | None
    human_notes_sha256: str
    protected_frontmatter_sha256: str | None = None

    @classmethod
    def capture(cls, note: PaperNote, human_notes: str) -> "ReviewSnapshot":
        """Capture fields and a non-reversible hash of the exact Human notes section."""
        ai_owned = {
            "ai_review_status",
            "ai_review_scope",
            "ai_review_coverage",
            "ai_review_agent",
            "ai_review_model",
            "ai_review_date",
            "ai_review_version",
            "ai_recommendation",
            "ai_recommendation_reason",
            "ai_relevance",
            "ai_recommendation_confidence",
            "ai_applied_tags",
            "ai_suggested_tags",
            "ai_suggested_projects",
        }
        protected = {
            key: value for key, value in note.model_dump(mode="json").items() if key not in ai_owned
        }
        return cls(
            zotero_key=note.zotero_key,
            citekey=note.citekey,
            human_read_status=note.human_read_status,
            human_read_date=note.human_read_date,
            human_rating=note.human_rating,
            human_priority=note.human_priority,
            human_relevance=note.human_relevance,
            human_notes_sha256=sha256(human_notes.encode("utf-8")).hexdigest(),
            protected_frontmatter_sha256=sha256(
                repr(sorted(protected.items())).encode("utf-8")
            ).hexdigest(),
        )


class ReviewProvenance(DomainModel):
    """Fingerprints of every input used to produce a review."""

    note_sha256: str
    source_sha256: str
    reading_profile_sha256: str
    projects_sha256: str
    tag_registry_sha256: str


class ReviewSession(DomainModel):
    """Immutable, durable authorization boundary for one review attempt."""

    session_id: str
    citekey: str
    expected_revision: str
    context_scope: Literal["abstract-only", "partial-text", "full-text"]
    provenance: ReviewProvenance
    created_at: datetime


class ReviewSubmission(DomainModel):
    """The complete AI-owned mutation accepted by the review writer."""

    session_id: str
    citekey: str
    expected_revision: str
    review_scope: Literal["metadata-only", "abstract-only", "partial-text", "full-text"]
    review_coverage: Literal["complete", "partial", "unknown"]
    agent: str
    model: str | None = None
    review_date: date
    recommendation: Literal["must-read", "read", "skim", "skip", "uncertain"]
    recommendation_reason: str
    relevance: int | None = None
    recommendation_confidence: Literal["low", "medium", "high"]
    applied_tags: tuple[str, ...] = ()
    suggested_tags: tuple[str, ...] = ()
    suggested_tag_definitions: dict[str, str] = Field(default_factory=dict)
    suggested_projects: tuple[str, ...] = ()
    suggested_project_rationales: dict[str, str] = Field(default_factory=dict)
    managed_review: str

    @field_validator("agent", "recommendation_reason", "managed_review")
    @classmethod
    def review_text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def suggested_tag_definitions_must_be_complete(self) -> Self:
        if set(self.suggested_tag_definitions) != set(self.suggested_tags):
            raise ValueError("suggested_tag_definitions must define every suggested tag exactly")
        if any(not definition.strip() for definition in self.suggested_tag_definitions.values()):
            raise ValueError("suggested tag definitions must not be blank")
        if set(self.suggested_project_rationales) != set(self.suggested_projects):
            raise ValueError(
                "suggested_project_rationales must explain every suggested project exactly"
            )
        if any(not rationale.strip() for rationale in self.suggested_project_rationales.values()):
            raise ValueError("suggested project rationales must not be blank")
        return self


class ReviewReceipt(DomainModel):
    citekey: str
    previous_revision: str
    revision: str
    review_version: int
    proposal_id: str | None = None


class ReviewProvenanceStatus(DomainModel):
    """Current freshness of the latest durably recorded review inputs."""

    citekey: str
    review_version: int
    review_revision: str
    provenance: ReviewProvenance
    current: ReviewProvenance
    source_check: Literal["abstract", "local-cache"]
    source_limitation: str
    stale: StrictBool
    stale_reasons: tuple[
        Literal[
            "note-changed",
            "source-changed",
            "reading-profile-changed",
            "projects-changed",
            "tag-registry-changed",
        ],
        ...,
    ] = ()


class CurationItem(DomainModel):
    item_id: str
    kind: Literal["existing-tag", "new-tag", "project-link"]
    value: str
    rationale: str
    definition: str | None = None


class CurationProposalRequest(DomainModel):
    citekey: str
    expected_revision: str
    items: tuple[CurationItem, ...]


class CurationDecision(DomainModel):
    item_id: str
    decision: Literal["accepted", "rejected"]


class ApprovedCurationRequest(DomainModel):
    proposal_id: str
    decisions: tuple[CurationDecision, ...]
    expected_revision: str


class CurationProposal(DomainModel):
    proposal_id: str
    citekey: str
    expected_revision: str
    status: Literal["pending", "decided", "applied", "superseded"] = "pending"
    items: tuple[CurationItem, ...]
    decisions: tuple[CurationDecision, ...] = ()
    created_at: datetime


class OperationReceipt(DomainModel):
    operation_id: str
    kind: str
    affected_paths: tuple[str, ...]
    previous_revisions: dict[str, str]
    revisions: dict[str, str]
    created_at: datetime
