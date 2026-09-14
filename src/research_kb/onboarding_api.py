"""Workspace-scoped onboarding operations usable without an agent shell."""

from research_kb.better_bibtex import BetterBibTeXClient
from research_kb.config import Settings
from research_kb.doctor_service import DoctorService
from research_kb.markdown_store import MarkdownStore
from research_kb.models import DomainModel
from research_kb.project_service import ProjectService
from research_kb.setup_service import SetupRequest, SetupService
from research_kb.sync_service import SyncService
from research_kb.validation_service import ValidationService
from research_kb.zotero_client import ZoteroClient


class EmptyRequest(DomainModel):
    """No paths, commands, or credentials are accepted by maintenance tools."""


class OnboardingAPI:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def status(self, request: EmptyRequest) -> dict[str, object]:
        return SetupService(self.settings).status()

    def schema(self, request: EmptyRequest) -> dict[str, object]:
        return SetupRequest.model_json_schema()

    def preview(self, request: SetupRequest) -> dict[str, object]:
        return SetupService(self.settings).preview(request)

    def apply(self, request: SetupRequest) -> dict[str, object]:
        result = SetupService(self.settings).apply(request)
        if request.library is not None:
            # A stdio server outlives the .env creation. Carry the accepted library
            # into subsequent calls without losing the caller's endpoint overrides.
            self.settings = self.settings.model_copy(
                update={
                    "zotero_library_type": request.library.library_type,
                    "zotero_library_id": request.library.library_id,
                }
            )
        return result

    def doctor(self, request: EmptyRequest) -> dict[str, object]:
        with (
            ZoteroClient(self.settings.zotero_local_api) as zotero,
            BetterBibTeXClient(self.settings.better_bibtex_rpc) as bibtex,
        ):
            report = DoctorService(self.settings, zotero, bibtex).run()
        return {"ok": report.ok, "checks": report.checks}

    def validate(self, request: EmptyRequest) -> dict[str, object]:
        report = ValidationService(self.settings, MarkdownStore(self.settings.papers_dir)).run()
        return {"ok": report.ok, "checked_count": report.checked_count, "issues": report.issues}

    def sync(self, request: EmptyRequest) -> dict[str, object]:
        store = MarkdownStore(self.settings.papers_dir)
        with (
            ZoteroClient(self.settings.zotero_local_api) as zotero,
            BetterBibTeXClient(self.settings.better_bibtex_rpc) as bibtex,
        ):
            report = SyncService(self.settings, zotero, bibtex, store).run()
        index = ProjectService(self.settings, store).index()
        return {
            "sync": report,
            "counts": report.counts,
            "project_index": index,
            "validation": self.validate(request),
        }
