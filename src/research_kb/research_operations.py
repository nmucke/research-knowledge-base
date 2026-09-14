"""Validated, auditable operations shared by trusted adapters.

Read/review/proposal methods are safe for agent-facing adapters.  Applying a
decision lives on :class:`CurationApprovalService` so an agent cannot turn its
own proposal into user approval.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Mapping
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal, TypeVar
from uuid import uuid4

from pydantic import ValidationError as PydanticValidationError

from research_kb.config import Settings
from research_kb.exceptions import ValidationError
from research_kb.extraction_service import ExtractionService, ReviewContext
from research_kb.markdown_store import MarkdownStore
from research_kb.models import (
    ApprovedCurationRequest,
    CurationDecision,
    CurationItem,
    CurationProposal,
    CurationProposalRequest,
    OperationReceipt,
    ReviewProvenance,
    ReviewProvenanceStatus,
    ReviewReceipt,
    ReviewSession,
    ReviewSnapshot,
    ReviewSubmission,
)
from research_kb.project_registry import load_projects
from research_kb.project_service import ProjectService
from research_kb.review_contract import validate_managed_review
from research_kb.tag_registry import parse_tag_registry
from research_kb.validation_service import ValidationService
from research_kb.vault_transaction import (
    FileDiff,
    PlannedFileChange,
    TransactionResult,
    VaultTransaction,
)


class ResearchOperations:
    """Agent-safe context, validated review submission, and proposal operations."""

    def __init__(
        self,
        settings: Settings,
        store: MarkdownStore | None = None,
        extraction: ExtractionService | None = None,
    ) -> None:
        self.settings = settings
        self.store = store or MarkdownStore(settings.papers_dir)
        self.extraction = extraction
        self.validator = ValidationService(settings, self.store)
        state = getattr(settings, "state_dir", settings.research_dir)
        self._sessions = state / "operations" / "review-sessions"
        self._proposals = state / "operations" / "curation-proposals"
        self._history = state / "history" / "reviews"
        self.transactions = VaultTransaction(settings.vault_path, settings.recovery_dir)

    def get_context(
        self,
        citekey: str,
        *,
        scope: Literal["auto", "abstract-only", "full-text"] = "auto",
    ) -> ReviewContext:
        note_path = self.store.note_path(citekey)
        _safe_configured_hash(note_path, self.settings.vault_path)
        if self.extraction is not None:
            return self.extraction.review_context(citekey, scope=scope)
        if scope == "full-text":
            raise ValidationError("full-text context requires an extraction service")
        path = note_path
        note = self.store.parse(path).note
        if not note.abstract:
            raise ValidationError(f"Paper {citekey!r} has no abstract-only review context.")
        return ReviewContext(
            paper_note=path,
            extracted_paper=None,
            reading_profile=self.settings.reading_profile_path,
            tag_registry=self.settings.tag_registry_path,
            active_projects=tuple(
                self.settings.projects_dir / f"{project.project_id}.md"
                for project in load_projects(self.settings.projects_dir)
                if project.status == "active"
            ),
            scope="abstract-only",
            abstract=note.abstract,
        )

    def start_review(
        self,
        citekey: str,
        *,
        scope: Literal["auto", "abstract-only", "full-text"] = "auto",
    ) -> tuple[ReviewContext, ReviewSession]:
        context = self.get_context(citekey, scope=scope)
        revision = self.store.revision(context.paper_note)
        provenance = ReviewProvenance(
            note_sha256=revision,
            source_sha256=self._context_source_hash(context),
            reading_profile_sha256=_file_hash(context.reading_profile),
            projects_sha256=_combined_hash(context.active_projects),
            tag_registry_sha256=_file_hash(context.tag_registry),
        )
        session = ReviewSession(
            session_id=uuid4().hex,
            citekey=citekey,
            expected_revision=revision,
            context_scope=context.scope,
            provenance=provenance,
            created_at=datetime.now().astimezone(),
        )
        _write_json(self._sessions / f"{session.session_id}.json", session.model_dump(mode="json"))
        return context, session

    def submit_review(self, submission: ReviewSubmission) -> ReviewReceipt:
        session = self._load_session(submission.session_id)
        if session.citekey != submission.citekey:
            raise ValidationError("review session belongs to a different citation key")
        if session.expected_revision != submission.expected_revision:
            raise ValidationError("submission revision does not match its immutable session")
        if submission.review_scope != session.context_scope:
            raise ValidationError("submitted review scope exceeds or differs from reviewed context")
        context = self.get_context(
            submission.citekey, scope=_requested_scope(session.context_scope)
        )
        current_provenance = ReviewProvenance(
            note_sha256=self.store.revision(context.paper_note),
            source_sha256=self._context_source_hash(context),
            reading_profile_sha256=_file_hash(context.reading_profile),
            projects_sha256=_combined_hash(context.active_projects),
            tag_registry_sha256=_file_hash(context.tag_registry),
        )
        if current_provenance != session.provenance:
            raise ValidationError("review inputs changed after the session started")
        path = self.store.note_path(submission.citekey)
        current = self.store.parse(path).note
        if current.ai_review_human_verified:
            raise ValidationError("a human-verified review cannot be replaced by agent submission")
        updates = {
            "ai_review_status": "reviewed",
            "ai_review_scope": submission.review_scope,
            "ai_review_coverage": submission.review_coverage,
            "ai_review_agent": submission.agent,
            "ai_review_model": submission.model,
            "ai_review_date": submission.review_date,
            "ai_review_version": current.ai_review_version + 1,
            "ai_recommendation": submission.recommendation,
            "ai_recommendation_reason": submission.recommendation_reason,
            "ai_relevance": submission.relevance,
            "ai_recommendation_confidence": submission.recommendation_confidence,
            "ai_applied_tags": submission.applied_tags,
            "ai_suggested_tags": submission.suggested_tags,
            "ai_suggested_projects": submission.suggested_projects,
        }
        candidate = self.store.render_review_update(path, updates, submission.managed_review)
        candidate_note = current.model_copy(update=updates)
        violations = validate_managed_review(candidate_note, submission.managed_review)
        if violations:
            detail = "; ".join(f"{item.code}: {item.message}" for item in violations)
            raise ValidationError(f"review contract failed: {detail}")
        self._validate_curation_values(
            candidate_note.ai_applied_tags, candidate_note.ai_suggested_projects
        )
        report = self.validator.validate_candidate(submission.citekey, candidate)
        if not report.ok:
            detail = "; ".join(f"{issue.code}: {issue.message}" for issue in report.errors)
            raise ValidationError(f"proposed review failed validation: {detail}")
        anticipated_revision = sha256(candidate.encode("utf-8")).hexdigest()
        proposal_id = None
        items: list[CurationItem] = []
        for index, tag in enumerate(submission.applied_tags):
            items.append(
                CurationItem(
                    item_id=f"tag-existing-{index + 1}",
                    kind="existing-tag",
                    value=tag,
                    rationale="Proposed by review.",
                )
            )
        for index, tag in enumerate(submission.suggested_tags):
            items.append(
                CurationItem(
                    item_id=f"tag-new-{index + 1}",
                    kind="new-tag",
                    value=tag,
                    rationale="Proposed by review.",
                    definition=submission.suggested_tag_definitions[tag],
                )
            )
        for index, project in enumerate(submission.suggested_projects):
            items.append(
                CurationItem(
                    item_id=f"project-{index + 1}",
                    kind="project-link",
                    value=project,
                    rationale=submission.suggested_project_rationales[project],
                )
            )
        proposal = None
        if items:
            proposal = CurationProposal(
                proposal_id=uuid4().hex,
                citekey=submission.citekey,
                expected_revision=anticipated_revision,
                items=tuple(items),
                created_at=datetime.now().astimezone(),
            )
            proposal_id = proposal.proposal_id
        receipt = ReviewReceipt(
            citekey=submission.citekey,
            previous_revision=submission.expected_revision,
            revision=anticipated_revision,
            review_version=current.ai_review_version + 1,
            proposal_id=proposal_id,
        )
        session_path = self._sessions / f"{session.session_id}.json"
        history_path = self._history / f"review-{session.session_id}.json"
        changes = [
            PlannedFileChange(path, path.read_text(encoding="utf-8"), candidate),
            PlannedFileChange(
                history_path,
                None,
                _json_text(
                    {
                        "receipt": receipt.model_dump(mode="json"),
                        "provenance": session.provenance.model_dump(mode="json"),
                    }
                ),
            ),
            PlannedFileChange(session_path, session_path.read_text(encoding="utf-8"), None),
        ]
        if proposal is not None:
            proposal_path = self._proposals / f"{proposal.proposal_id}.json"
            view_path = _proposal_view_path(self.settings, proposal.proposal_id)
            _validate_proposal_view_parent(self.settings, view_path)
            changes[1:1] = [
                PlannedFileChange(
                    proposal_path, None, _json_text(proposal.model_dump(mode="json"))
                ),
                PlannedFileChange(view_path, None, _render_proposal_view(proposal)),
            ]
        self.transactions.apply(
            tuple(changes),
            metadata={
                "kind": "review-submission",
                "citekey": submission.citekey,
                "session_id": submission.session_id,
                "proposal_id": proposal_id,
            },
        )
        return receipt

    def propose_curation(self, request: CurationProposalRequest) -> CurationProposal:
        if self.store.revision(self.store.note_path(request.citekey)) != request.expected_revision:
            raise ValidationError("cannot propose curation against a stale paper revision")
        if not request.items or len({item.item_id for item in request.items}) != len(request.items):
            raise ValidationError("proposal items must be nonempty and have unique identifiers")
        proposal = CurationProposal(
            proposal_id=uuid4().hex,
            citekey=request.citekey,
            expected_revision=request.expected_revision,
            items=request.items,
            created_at=datetime.now().astimezone(),
        )
        proposal_path = self._proposals / f"{proposal.proposal_id}.json"
        view_path = _proposal_view_path(self.settings, proposal.proposal_id)
        _validate_proposal_view_parent(self.settings, view_path)
        self.transactions.apply(
            (
                PlannedFileChange(
                    proposal_path, None, _json_text(proposal.model_dump(mode="json"))
                ),
                PlannedFileChange(view_path, None, _render_proposal_view(proposal)),
            ),
            metadata={
                "kind": "curation-proposal",
                "citekey": request.citekey,
                "proposal_id": proposal.proposal_id,
            },
        )
        return proposal

    def list_proposals(
        self, citekey: str | None = None, status: str | None = None
    ) -> tuple[CurationProposal, ...]:
        found: list[CurationProposal] = []
        for path in sorted(self._proposals.glob("*.json")) if self._proposals.is_dir() else ():
            proposal = self._load_proposal(path.stem)
            if (citekey is None or proposal.citekey == citekey) and (
                status is None or proposal.status == status
            ):
                found.append(proposal)
        return tuple(found)

    def rebuild_proposal_views(self) -> tuple[Path, ...]:
        """Rebuild derived Obsidian proposal notes from durable JSON state."""
        written = []
        for proposal in self.list_proposals():
            path = _proposal_view_path(self.settings, proposal.proposal_id)
            _validate_proposal_view_parent(self.settings, path)
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_text(path, _render_proposal_view(proposal))
            written.append(path)
        return tuple(written)

    def review_provenance_status(self, citekey: str) -> ReviewProvenanceStatus | None:
        """Compare the latest durable review provenance with safe current inputs."""
        path = self.store.note_path(citekey)
        _safe_configured_hash(path, self.settings.vault_path)
        document = self.store.parse(path)
        latest: tuple[ReviewReceipt, ReviewProvenance] | None = None
        if self._history.is_dir():
            for history_path in sorted(self._history.glob("review-*.json")):
                _require_safe_state_file(history_path, self._history, self.settings.vault_path)
                try:
                    raw = json.loads(history_path.read_text(encoding="utf-8"))
                    receipt = ReviewReceipt.model_validate(raw["receipt"])
                    provenance = ReviewProvenance.model_validate(raw["provenance"])
                except (OSError, json.JSONDecodeError, KeyError, PydanticValidationError) as error:
                    raise ValidationError(
                        f"invalid review history {history_path}: {error}"
                    ) from error
                if receipt.citekey != citekey:
                    continue
                if latest is None or receipt.review_version > latest[0].review_version:
                    latest = (receipt, provenance)
        if latest is None:
            return None
        receipt, provenance = latest
        source_check: Literal["abstract", "local-cache"] = (
            "abstract" if document.note.ai_review_scope == "abstract-only" else "local-cache"
        )
        source_hash = self._current_review_source_hash(citekey, document.note.ai_review_scope)
        _reject_symlinks_for_copy(
            self.settings.projects_dir, self.settings.workspace_path, "project directory"
        )
        active_projects = tuple(
            self.settings.projects_dir / f"{project.project_id}.md"
            for project in load_projects(self.settings.projects_dir)
            if project.status == "active"
        )
        current = ReviewProvenance(
            note_sha256=self.store.revision(path),
            source_sha256=source_hash,
            reading_profile_sha256=_safe_configured_hash(
                self.settings.reading_profile_path, self.settings.vault_path
            ),
            projects_sha256=_safe_combined_hash(active_projects, self.settings.vault_path),
            tag_registry_sha256=_safe_configured_hash(
                self.settings.tag_registry_path, self.settings.vault_path
            ),
        )
        reasons: list[
            Literal[
                "note-changed",
                "source-changed",
                "reading-profile-changed",
                "projects-changed",
                "tag-registry-changed",
            ]
        ] = []
        if current.note_sha256 != receipt.revision:
            reasons.append("note-changed")
        if current.source_sha256 != provenance.source_sha256:
            reasons.append("source-changed")
        if current.reading_profile_sha256 != provenance.reading_profile_sha256:
            reasons.append("reading-profile-changed")
        if current.projects_sha256 != provenance.projects_sha256:
            reasons.append("projects-changed")
        if current.tag_registry_sha256 != provenance.tag_registry_sha256:
            reasons.append("tag-registry-changed")
        return ReviewProvenanceStatus(
            citekey=citekey,
            review_version=receipt.review_version,
            review_revision=receipt.revision,
            provenance=provenance,
            current=current,
            source_check=source_check,
            source_limitation=(
                "Current abstract text was fingerprinted; no PDF was checked."
                if source_check == "abstract"
                else "The configured local extraction cache was fingerprinted; the Zotero PDF "
                "and recorded attachment paths were not checked."
            ),
            stale=bool(reasons),
            stale_reasons=tuple(reasons),
        )

    def _current_review_source_hash(self, citekey: str, scope: str | None) -> str:
        if scope == "abstract-only":
            abstract = self.store.parse(self.store.note_path(citekey)).note.abstract or ""
            return sha256(abstract.encode("utf-8")).hexdigest()
        cache = self.settings.paper_text_dir / f"{citekey}.md"
        return _safe_configured_hash(cache, self.settings.vault_path)

    def preview_proposal(
        self, proposal_id: str, decisions: tuple[CurationDecision, ...]
    ) -> tuple[FileDiff, ...]:
        proposal = self._load_proposal(proposal_id)
        _validate_decisions(proposal, decisions)
        return CurationApprovalService(self).preview(
            ApprovedCurationRequest(
                proposal_id=proposal_id,
                decisions=decisions,
                expected_revision=proposal.expected_revision,
            )
        )

    def _load_session(self, session_id: str) -> ReviewSession:
        return _load_model(self._sessions / f"{_safe_id(session_id)}.json", ReviewSession)

    def _load_proposal(self, proposal_id: str) -> CurationProposal:
        return _load_model(self._proposals / f"{_safe_id(proposal_id)}.json", CurationProposal)

    def _validate_curation_values(
        self, existing_tags: tuple[str, ...], projects: tuple[str, ...]
    ) -> None:
        registry = set(parse_tag_registry(self.settings.tag_registry_path).names)
        unknown_tags = set(existing_tags) - registry
        known_projects = {item.project_id for item in load_projects(self.settings.projects_dir)}
        unknown_projects = set(projects) - known_projects
        if unknown_tags:
            names = ", ".join(sorted(unknown_tags))
            raise ValidationError(f"existing tag proposals are absent from registry: {names}")
        if unknown_projects:
            raise ValidationError(
                f"project proposals do not exist: {', '.join(sorted(unknown_projects))}"
            )

    @staticmethod
    def _context_source_hash(context: ReviewContext) -> str:
        if context.extracted_paper is not None:
            return _file_hash(context.extracted_paper)
        return sha256((context.abstract or "").encode("utf-8")).hexdigest()


class CurationApprovalService:
    """Trusted user-facing application of explicit proposal decisions."""

    def __init__(self, operations: ResearchOperations) -> None:
        self.operations = operations
        self.transactions = VaultTransaction(
            operations.settings.vault_path, operations.settings.recovery_dir
        )

    def preview(self, request: ApprovedCurationRequest) -> tuple[FileDiff, ...]:
        """Return the exact validated multi-file diff without changing the vault."""
        proposal = self._pending_proposal(request)
        return self.transactions.preview(self._plan(proposal, request.decisions))

    def apply(self, request: ApprovedCurationRequest) -> OperationReceipt:
        proposal = self._pending_proposal(request)
        result = self.transactions.apply(
            self._plan(proposal, request.decisions),
            metadata={
                "kind": "curation-approval",
                "proposal_id": proposal.proposal_id,
                "citekey": proposal.citekey,
                "decisions": [item.model_dump(mode="json") for item in request.decisions],
            },
        )
        receipt = _operation_receipt("curation-approval", result)
        return receipt

    def history(self, citekey: str | None = None) -> tuple[OperationReceipt, ...]:
        receipts = []
        for operation_id in self.transactions.completed():
            if self.transactions.metadata(operation_id).get("kind") != "curation-approval":
                continue
            result = self.transactions.result(operation_id)
            receipt = _operation_receipt("curation-approval", result)
            if citekey is None or any(
                Path(path).stem == citekey for path in receipt.affected_paths
            ):
                receipts.append(receipt)
        return tuple(receipts)

    def undo(self, operation_id: str, expected_revisions: dict[str, str]) -> OperationReceipt:
        current = self.transactions.result(operation_id)
        current_revisions = {item.path: item.after_revision or "deleted" for item in current.files}
        if expected_revisions != current_revisions:
            raise ValidationError("undo requires the exact current revisions from the operation")
        return _operation_receipt(f"undo:{operation_id}", self.transactions.undo(operation_id))

    def recover(self, operation_id: str) -> OperationReceipt | None:
        result = self.transactions.recover(operation_id)
        return None if result is None else _operation_receipt("recovery", result)

    def pending_recovery(self) -> tuple[str, ...]:
        return self.transactions.pending()

    def _pending_proposal(self, request: ApprovedCurationRequest) -> CurationProposal:
        proposal = self.operations._load_proposal(request.proposal_id)
        if proposal.status != "pending":
            raise ValidationError(f"proposal is already {proposal.status} and cannot be replayed")
        _validate_decisions(proposal, request.decisions)
        actual = self.operations.store.revision(self.operations.store.note_path(proposal.citekey))
        if request.expected_revision != actual or proposal.expected_revision != actual:
            raise ValidationError("curation proposal is stale; preview it against the current note")
        return proposal

    def _plan(
        self, proposal: CurationProposal, decisions: tuple[CurationDecision, ...]
    ) -> tuple[PlannedFileChange, ...]:
        path = self.operations.store.note_path(proposal.citekey)
        accepted = {item.item_id for item in decisions if item.decision == "accepted"}
        note = self.operations.store.parse(path).note
        tags = set(note.tags)
        projects = set(note.projects)
        new_entries: list[tuple[str, str]] = []
        for item in proposal.items:
            if item.item_id not in accepted:
                continue
            if item.kind == "existing-tag":
                tags.add(item.value)
            elif item.kind == "project-link":
                projects.add(item.value)
            else:
                if not item.definition or not item.definition.strip():
                    raise ValidationError("an accepted new tag requires a nonblank definition")
                tags.add(item.value)
                new_entries.append((item.value, item.definition.strip()))
        pending_tags = tuple(
            item.value
            for item in proposal.items
            if item.kind == "new-tag" and item.item_id not in {d.item_id for d in decisions}
        )
        pending_projects = tuple(
            item.value
            for item in proposal.items
            if item.kind == "project-link" and item.item_id not in {d.item_id for d in decisions}
        )
        updates = {
            "tags": tuple(sorted(tags)),
            "projects": tuple(sorted(projects)),
            "ai_suggested_tags": pending_tags,
            "ai_suggested_projects": pending_projects,
            "zotero_tag_sync": "not-synced" if tags != set(note.tags) else note.zotero_tag_sync,
            "zotero_tag_sync_date": None if tags != set(note.tags) else note.zotero_tag_sync_date,
        }
        before = path.read_text(encoding="utf-8")
        candidate = _render_curated_note(self.operations.store, path, updates)
        registry_path = self.operations.settings.tag_registry_path
        registry_before = registry_path.read_text(encoding="utf-8")
        registry_candidate = _render_registry_additions(registry_before, new_entries)
        proposal_path = self.operations._proposals / f"{proposal.proposal_id}.json"
        proposal_before = proposal_path.read_text(encoding="utf-8")
        changes = list(
            self._stage_indexed_changes(
                proposal,
                decisions,
                path,
                before,
                candidate,
                registry_path,
                registry_before,
                registry_candidate,
                proposal_path,
                proposal_before,
            )
        )
        snapshot_store = self.operations.validator.snapshot_store
        baseline = snapshot_store.load(proposal.citekey)
        if baseline is not None:
            document = self.operations.store.parse(path)
            current_snapshot = ReviewSnapshot.capture(
                document.note, snapshot_store.human_notes(document.body)
            )
            if current_snapshot != baseline:
                raise ValidationError(
                    "active review snapshot differs from current protected content"
                )
            snapshot_path = snapshot_store.path(proposal.citekey)
            changes.append(
                PlannedFileChange(snapshot_path, snapshot_path.read_text(encoding="utf-8"), None)
            )
        return tuple(changes)

    def _stage_indexed_changes(
        self,
        proposal: CurationProposal,
        decisions: tuple[CurationDecision, ...],
        paper_path: Path,
        paper_before: str,
        paper_candidate: str,
        registry_path: Path,
        registry_before: str,
        registry_candidate: str,
        proposal_path: Path,
        proposal_before: str,
    ) -> tuple[PlannedFileChange, ...]:
        settings = self.operations.settings
        with tempfile.TemporaryDirectory(prefix="research-curation-") as temporary_name:
            temporary = Path(temporary_name)
            symlinks = [
                path for path in settings.obsidian_vault_path.rglob("*") if path.is_symlink()
            ]
            if symlinks:
                raise ValidationError(f"staging refuses vault symlink: {symlinks[0]}")
            live_before = {
                staged_path.relative_to(settings.obsidian_vault_path): staged_path.read_text(
                    encoding="utf-8"
                )
                for staged_path in settings.obsidian_vault_path.rglob("*.md")
            }
            paper_relative = paper_path.relative_to(settings.obsidian_vault_path)
            if live_before.get(paper_relative) != paper_before:
                raise ValidationError("paper changed before curation staging began")
            registry_relative = registry_path.relative_to(settings.obsidian_vault_path)
            if live_before.get(registry_relative) != registry_before:
                raise ValidationError("tag registry changed while curation staging began")
            if proposal_path.read_text(encoding="utf-8") != proposal_before:
                raise ValidationError("proposal state changed while curation staging began")
            shutil.copytree(settings.obsidian_vault_path, temporary / "vault")
            staged_settings = settings.model_copy(
                update={"research_vault_path": temporary, "research_obsidian_dir": Path("vault")}
            )
            staged_store = MarkdownStore(staged_settings.papers_dir)
            staged_paper = staged_store.note_path(paper_path.stem)
            staged_paper.write_text(paper_candidate, encoding="utf-8")
            staged_settings.tag_registry_path.write_text(registry_candidate, encoding="utf-8")
            if settings.paper_text_dir.is_dir():
                _reject_symlinks_for_copy(
                    settings.paper_text_dir, settings.workspace_path, "extraction cache"
                )
                shutil.copytree(
                    settings.paper_text_dir,
                    staged_settings.paper_text_dir,
                    dirs_exist_ok=True,
                )
            report = ProjectService(staged_settings, staged_store).index()
            if report.skipped or report.unknown:
                raise ValidationError("project indexing found invalid or unknown paper links")
            validation = ValidationService(staged_settings, staged_store).run()
            if not validation.ok:
                details = "; ".join(f"{issue.code}: {issue.message}" for issue in validation.errors)
                raise ValidationError(f"approved curation would invalidate the vault: {details}")
            changes: list[PlannedFileChange] = []
            for staged_path in sorted((temporary / "vault").rglob("*.md")):
                relative = staged_path.relative_to(temporary / "vault")
                live = settings.obsidian_vault_path / relative
                after = staged_path.read_text(encoding="utf-8")
                before = live_before.get(relative)
                if before != after:
                    changes.append(PlannedFileChange(live, before, after))
            view_path = _proposal_view_path(settings, proposal.proposal_id)
            if view_path.is_file():
                view_relative = view_path.relative_to(settings.obsidian_vault_path)
                view_before = live_before[view_relative]
                view_after = _render_proposal_view(
                    proposal.model_copy(update={"status": "applied", "decisions": decisions})
                )
                if view_before != view_after:
                    changes.append(PlannedFileChange(view_path, view_before, view_after))
            decided = proposal.model_copy(update={"status": "applied", "decisions": decisions})
            proposal_after = (
                json.dumps(decided.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
            )
            changes.append(PlannedFileChange(proposal_path, proposal_before, proposal_after))
            return tuple(changes)


def _render_curated_note(store: MarkdownStore, path: Path, updates: Mapping[str, object]) -> str:
    text = path.read_text(encoding="utf-8")
    metadata, _body, start, closing = store._split_frontmatter(path, text)
    merged = {**metadata, **updates}
    from research_kb.models import PaperNote

    PaperNote.model_validate(merged)
    frontmatter = store._patch_frontmatter(metadata, updates, text[start:closing])
    return f"{text[:start]}{frontmatter}{text[closing:]}"


def _operation_receipt(kind: str, result: TransactionResult) -> OperationReceipt:
    return OperationReceipt(
        operation_id=result.operation_id,
        kind=kind,
        affected_paths=tuple(item.path for item in result.files),
        previous_revisions={item.path: item.before_revision or "new" for item in result.files},
        revisions={item.path: item.after_revision or "deleted" for item in result.files},
        created_at=result.created_at,
    )


def _validate_decisions(
    proposal: CurationProposal, decisions: tuple[CurationDecision, ...]
) -> None:
    expected = {item.item_id for item in proposal.items}
    actual = [item.item_id for item in decisions]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValidationError("decisions must explicitly accept or reject every proposal item once")


def _render_registry_additions(text: str, additions: list[tuple[str, str]]) -> str:
    """Insert approved entries into namespace sections while preserving all existing text."""
    if not additions:
        return text
    # Validate names and duplicates using the normal parser after each insertion.
    result = text
    for name, definition in sorted(additions):
        namespace = name.partition("/")[0]
        heading = f"## {namespace.title()}"
        entry = f"### `{name}`\n\n{definition}\n"
        lines = result.rstrip("\n").splitlines()
        try:
            section = lines.index(heading)
        except ValueError:
            lines.extend(["", heading, "", *entry.rstrip().splitlines()])
        else:
            end = next(
                (
                    index
                    for index in range(section + 1, len(lines))
                    if lines[index].startswith("## ")
                ),
                len(lines),
            )
            entries = [
                (index, line[5:-1])
                for index, line in enumerate(lines[section + 1 : end], section + 1)
                if line.startswith("### `") and line.endswith("`")
            ]
            insertion = next((index for index, existing in entries if existing > name), end)
            payload = [*entry.rstrip().splitlines(), ""]
            lines[insertion:insertion] = payload
        result = "\n".join(lines).rstrip() + "\n"
    temporary = Path(tempfile.mkstemp(suffix=".md")[1])
    try:
        temporary.write_text(result, encoding="utf-8")
        parse_tag_registry(temporary)
    finally:
        temporary.unlink(missing_ok=True)
    return result


def _proposal_view_path(settings: Settings, proposal_id: str) -> Path:
    _safe_id(proposal_id)
    return settings.obsidian_vault_path / "System" / "Proposals" / f"{proposal_id}.md"


def _reject_symlinks_for_copy(path: Path, workspace: Path, label: str) -> None:
    try:
        relative = path.absolute().relative_to(workspace.absolute())
    except ValueError as error:
        raise ValidationError(f"{label} is outside the workspace: {path}") from error
    cursor = workspace.absolute()
    for part in relative.parts:
        cursor /= part
        if cursor.exists() and cursor.is_symlink():
            raise ValidationError(f"{label} path contains symlink: {cursor}")
    if path.exists():
        symlinks = [candidate for candidate in path.rglob("*") if candidate.is_symlink()]
        if symlinks:
            raise ValidationError(f"{label} contains symlink: {symlinks[0]}")


def _write_proposal_view(settings: Settings, proposal: CurationProposal) -> None:
    path = _proposal_view_path(settings, proposal.proposal_id)
    _validate_proposal_view_parent(settings, path)
    if path.exists():
        raise ValidationError(f"proposal view already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_text(path, _render_proposal_view(proposal), must_not_exist=True)


def _validate_proposal_view_parent(settings: Settings, path: Path) -> None:
    root = settings.obsidian_vault_path.resolve()
    cursor = settings.obsidian_vault_path
    for part in path.relative_to(settings.obsidian_vault_path).parts[:-1]:
        cursor /= part
        if cursor.exists() and cursor.is_symlink():
            raise ValidationError(f"proposal view path contains symlink: {cursor}")
    if not path.resolve(strict=False).is_relative_to(root):
        raise ValidationError(f"proposal view resolves outside the vault: {path}")


def _atomic_text(path: Path, content: str, *, must_not_exist: bool = False) -> None:
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if must_not_exist:
            try:
                os.link(temporary, path)
            except FileExistsError as error:
                raise ValidationError(f"proposal view already exists: {path}") from error
        else:
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _render_proposal_view(proposal: CurationProposal) -> str:
    decision_by_id = {decision.item_id: decision.decision for decision in proposal.decisions}
    lines = [
        "---",
        "type: curation-proposal",
        f"proposal_id: {proposal.proposal_id}",
        f"citekey: {proposal.citekey}",
        f"status: {proposal.status}",
        f"pending_count: {len(proposal.items) - len(proposal.decisions)}",
        "---",
        "",
        f"# Curation proposal for {proposal.citekey}",
        "",
    ]
    for item in proposal.items:
        decision = decision_by_id.get(item.item_id, "pending")
        lines.extend(
            [
                f"- item_id: `{item.item_id}`",
                f"  - {item.kind}: `{item.value}`",
                f"  - Rationale: {item.rationale}",
                f"  - Decision: {decision}",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _commit_file_if_revision(path: Path, content: str, expected_revision: str) -> str:
    current = path.read_text(encoding="utf-8")
    actual = sha256(current.encode("utf-8")).hexdigest()
    if actual != expected_revision:
        raise ValidationError(f"{path}: revision conflict")
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return sha256(content.encode("utf-8")).hexdigest()


def _safe_id(value: str) -> str:
    if not value or any(character not in "0123456789abcdef" for character in value):
        raise ValidationError("invalid operation identifier")
    return value


def _requested_scope(
    scope: Literal["abstract-only", "partial-text", "full-text"],
) -> Literal["abstract-only", "full-text"]:
    return "abstract-only" if scope == "abstract-only" else "full-text"


def _file_hash(path: Path) -> str:
    try:
        return sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise ValidationError(f"unable to fingerprint {path}: {error}") from error


def _require_safe_state_file(path: Path, root: Path, workspace: Path) -> None:
    if root.is_symlink() or path.is_symlink():
        raise ValidationError(f"unsafe symlinked review history path: {path}")
    resolved_root = root.resolve()
    resolved_workspace = workspace.resolve()
    resolved = path.resolve()
    if (
        not resolved_root.is_relative_to(resolved_workspace)
        or resolved.parent != resolved_root
        or not resolved.is_file()
    ):
        raise ValidationError(f"unsafe review history path: {path}")


def _safe_configured_hash(path: Path, workspace: Path) -> str:
    try:
        relative = path.absolute().relative_to(workspace.absolute())
    except ValueError as error:
        raise ValidationError(f"configured provenance path is outside workspace: {path}") from error
    cursor = workspace.absolute()
    for part in relative.parts:
        cursor /= part
        if cursor.exists() and cursor.is_symlink():
            raise ValidationError(f"configured provenance path contains symlink: {cursor}")
    if not path.is_file():
        return sha256(b"<missing>").hexdigest()
    return _file_hash(path)


def _safe_combined_hash(paths: tuple[Path, ...], workspace: Path) -> str:
    digest = sha256()
    for path in sorted(paths):
        digest.update(path.name.encode("utf-8"))
        digest.update(bytes.fromhex(_safe_configured_hash(path, workspace)))
    return digest.hexdigest()


def _combined_hash(paths: tuple[Path, ...]) -> str:
    digest = sha256()
    for path in sorted(paths):
        digest.update(path.name.encode("utf-8"))
        digest.update(bytes.fromhex(_file_hash(path)))
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _json_text(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


_StoredModel = TypeVar("_StoredModel", ReviewSession, CurationProposal)


def _load_model(path: Path, model: type[_StoredModel]) -> _StoredModel:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return model.model_validate(raw)
    except (OSError, json.JSONDecodeError, PydanticValidationError) as error:
        raise ValidationError(f"invalid or missing operation state {path}: {error}") from error
