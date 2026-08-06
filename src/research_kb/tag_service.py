"""Add-only, controlled tag reconciliation from paper notes to Zotero."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Protocol

from research_kb.config import Settings
from research_kb.exceptions import ValidationError, ZoteroConflictError
from research_kb.markdown_store import MarkdownStore
from research_kb.models import PaperNote, TagPushPlan, TagPushReport, ZoteroItem, ZoteroTag
from research_kb.tag_registry import parse_tag_registry


class ZoteroTagClient(Protocol):
    """The deliberately small Zotero surface needed for tag pushes."""

    def get_item(self, item_key: str, *, library_type: str, library_id: int) -> ZoteroItem: ...

    def patch_tags(
        self,
        item_key: str,
        tags: Sequence[ZoteroTag],
        version: int,
        *,
        api_key: str,
        library_type: str,
        library_id: int,
    ) -> int: ...


class TagService:
    """Push only valid human-curated tags, never AI tag fields, to Zotero."""

    def __init__(
        self,
        settings: Settings,
        store: MarkdownStore,
        client: ZoteroTagClient,
        *,
        server_id: str | None = None,
        enforce_server_identity: bool = True,
    ) -> None:
        self._settings = settings
        self._store = store
        self._client = client
        self._server_id = server_id
        self._enforce_server_identity = enforce_server_identity

    def build_plan(self, note: PaperNote, latest: ZoteroItem) -> TagPushPlan:
        """Build a deterministic add-only plan from the latest Zotero item."""
        if (
            self._enforce_server_identity
            and note.zotero_server_id is not None
            and note.zotero_server_id != self._server_id
        ):
            raise ValidationError(
                "The paper note belongs to a different Zotero server; run `uv run research "
                "sync` against the intended Zotero profile before reconciling tags."
            )
        local_user_alias = (
            self._settings.zotero_library_type == "user"
            and self._settings.zotero_library_id == 0
        )
        if latest.key != note.zotero_key or (
            not local_user_alias and latest.library_id != self._settings.zotero_library_id
        ):
            raise ValidationError("Zotero returned an item with an unexpected library identity.")
        duplicate_approved = tuple(
            sorted({tag for tag in note.tags if note.tags.count(tag) > 1})
        )
        if duplicate_approved:
            names = ", ".join(f"`{tag}`" for tag in duplicate_approved)
            raise ValidationError(f"Approved tag(s) are duplicated: {names}.")
        registry = parse_tag_registry(self._settings.tag_registry_path)
        registered = set(registry.names)
        invalid = tuple(sorted(set(note.tags) - registered))
        if invalid:
            names = ", ".join(f"`{tag}`" for tag in invalid)
            raise ValidationError(f"Approved tag(s) absent from the tag registry: {names}.")
        disallowed = tuple(
            tag
            for tag in registry.names
            if tag in set(note.tags)
            and tag.partition("/")[0] not in self._settings.allowed_tag_namespaces
        )
        if disallowed:
            names = ", ".join(f"`{tag}`" for tag in disallowed)
            raise ValidationError(f"Approved tag(s) use a disallowed namespace: {names}.")

        # Registry order makes the output stable even when frontmatter was manually reordered.
        approved = tuple(tag for tag in registry.names if tag in set(note.tags))
        # `tag_entries` retains Zotero's tag type (for example automatic tags).
        # Empty is a compatibility fallback for old clients and simple test doubles.
        existing_entries = latest.tag_entries or tuple(ZoteroTag(tag=tag) for tag in latest.tags)
        existing = tuple(entry.tag for entry in existing_entries)
        pending = tuple(tag for tag in approved if tag not in existing)
        merged_entries = existing_entries + tuple(ZoteroTag(tag=tag) for tag in pending)
        return TagPushPlan(
            zotero_key=note.zotero_key,
            citekey=note.citekey,
            existing_zotero_tags=existing,
            existing_zotero_tag_entries=existing_entries,
            approved_curated_tags=approved,
            ai_applied_tags=note.ai_applied_tags,
            suggested_tags=note.ai_suggested_tags,
            pending_tags=pending,
            merged_zotero_tags=existing + pending,
            merged_zotero_tag_entries=merged_entries,
        )

    def plan(self, path: Path) -> TagPushPlan:
        """Read a note and its current Zotero item to produce a push plan."""
        note = self._store.parse(path).note
        latest = self._get_item(note.zotero_key)
        return self.build_plan(note, latest)

    def push(
        self, path: Path, *, dry_run: bool = False, api_key: str | None = None
    ) -> TagPushReport:
        """Apply the plan, retrying one version conflict against freshly fetched tags."""
        note = self._store.parse(path).note
        latest = self._get_item(note.zotero_key)
        plan = self.build_plan(note, latest)
        if dry_run:
            return TagPushReport(
                plan=plan, dry_run=dry_run, pushed=False, note_updated=False, attempts=0
            )
        if not plan.requires_push:
            changed = self._store.update_tag_sync_fields(
                path,
                {
                    "zotero_version": latest.version,
                    "zotero_tags": latest.tags or tuple(entry.tag for entry in latest.tag_entries),
                    "zotero_tag_sync": "synced",
                    "zotero_tag_sync_date": date.today(),
                },
            )
            return TagPushReport(
                plan=plan, dry_run=False, pushed=False, note_updated=changed, attempts=0
            )
        if api_key is None or not api_key.strip():
            raise ValidationError("A nonblank Zotero API key is required to push tags.")

        attempts = 1
        try:
            updated_version = self._patch_tags(
                note.zotero_key, plan.merged_zotero_tag_entries, latest.version, api_key
            )
        except ZoteroConflictError:
            latest = self._get_item(note.zotero_key)
            plan = self.build_plan(note, latest)
            if not plan.requires_push:
                changed = self._store.update_tag_sync_fields(
                    path,
                    {
                        "zotero_version": latest.version,
                        "zotero_tags": latest.tags
                        or tuple(entry.tag for entry in latest.tag_entries),
                        "zotero_tag_sync": "synced",
                        "zotero_tag_sync_date": date.today(),
                    },
                )
                return TagPushReport(
                    plan=plan, dry_run=False, pushed=False, note_updated=changed, attempts=attempts
                )
            attempts += 1
            updated_version = self._patch_tags(
                note.zotero_key, plan.merged_zotero_tag_entries, latest.version, api_key
            )

        changed = self._store.update_tag_sync_fields(
            path,
            {
                "zotero_version": updated_version,
                "zotero_tags": plan.merged_zotero_tags,
                "zotero_tag_sync": "synced",
                "zotero_tag_sync_date": date.today(),
            },
        )
        return TagPushReport(
            plan=plan, dry_run=False, pushed=True, note_updated=changed, attempts=attempts
        )

    def _get_item(self, key: str) -> ZoteroItem:
        return self._client.get_item(
            key,
            library_type=self._settings.zotero_library_type,
            library_id=self._settings.zotero_library_id,
        )

    def _patch_tags(
        self, key: str, tags: tuple[ZoteroTag, ...], version: int, api_key: str
    ) -> int:
        return self._client.patch_tags(
            key,
            tags,
            version,
            api_key=api_key,
            library_type=self._settings.zotero_library_type,
            library_id=self._settings.zotero_library_id,
        )
