"""Service checks used by the ``research doctor`` command."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from research_kb.config import Settings
from research_kb.credential_store import CredentialStore
from research_kb.exceptions import (
    BetterBibTeXUnavailableError,
    CredentialStoreError,
    ZoteroUnavailableError,
)


class CheckStatus(StrEnum):
    """The outcome severity of one doctor check."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class DoctorCheck:
    """An immutable, user-facing result for one diagnostic check."""

    name: str
    status: CheckStatus
    message: str


@dataclass(frozen=True)
class DoctorReport:
    """The complete result of a doctor run."""

    checks: tuple[DoctorCheck, ...]

    @property
    def ok(self) -> bool:
        """Whether no required check failed."""
        return all(check.status is not CheckStatus.FAIL for check in self.checks)


class ZoteroServerInfoLike(Protocol):
    """The discovery data needed by the doctor service."""

    @property
    def api_version(self) -> int: ...

    @property
    def server_id(self) -> str | None: ...

    @property
    def schema_version(self) -> int: ...


class ZoteroClientLike(Protocol):
    """The small portion of a Zotero client used by doctor."""

    def discover(self) -> ZoteroServerInfoLike: ...


class BetterBibTeXClientLike(Protocol):
    """The small portion of a Better BibTeX client used by doctor."""

    def check_ready(self) -> None: ...


class DoctorService:
    """Run local diagnostics using clients supplied by the application layer."""

    def __init__(
        self,
        settings: Settings,
        zotero_client: ZoteroClientLike,
        better_bibtex_client: BetterBibTeXClientLike,
    ) -> None:
        self._settings = settings
        self._zotero_client = zotero_client
        self._better_bibtex_client = better_bibtex_client
        self._zotero_info: ZoteroServerInfoLike | None = None

    def run(self) -> DoctorReport:
        """Run all checks, retaining results even when earlier checks fail."""
        return DoctorReport(
            checks=(
                self._check_zotero(),
                self._check_better_bibtex(),
                self._check_references_bib(),
                self._check_vault_paths(),
                self._check_configuration(),
                self._check_write_authorization(),
            )
        )

    def _check_zotero(self) -> DoctorCheck:
        try:
            info = self._zotero_client.discover()
        except ZoteroUnavailableError:
            return DoctorCheck(
                "zotero",
                CheckStatus.FAIL,
                "Zotero local API is unavailable; start Zotero and enable its local API.",
            )
        except Exception:
            return DoctorCheck(
                "zotero",
                CheckStatus.FAIL,
                "Zotero discovery failed; verify the local API configuration and retry.",
            )

        self._zotero_info = info

        missing = [
            label
            for label, value in (
                ("API version", info.api_version),
                ("schema version", info.schema_version),
            )
            if not value
        ]
        if missing:
            return DoctorCheck(
                "zotero",
                CheckStatus.FAIL,
                f"Zotero did not provide {', '.join(missing)}; check the local API response.",
            )
        if info.server_id is None:
            return DoctorCheck(
                "zotero",
                CheckStatus.WARN,
                f"Zotero ready: API {info.api_version}, schema {info.schema_version}; "
                "this Zotero version does not provide a server ID.",
            )
        return DoctorCheck(
            "zotero",
            CheckStatus.PASS,
            "Zotero ready: "
            f"API {info.api_version}, server {info.server_id}, schema {info.schema_version}.",
        )

    def _check_better_bibtex(self) -> DoctorCheck:
        try:
            self._better_bibtex_client.check_ready()
        except BetterBibTeXUnavailableError:
            return DoctorCheck(
                "better_bibtex",
                CheckStatus.FAIL,
                "Better BibTeX is unavailable; install or enable it and restart Zotero.",
            )
        except Exception:
            return DoctorCheck(
                "better_bibtex",
                CheckStatus.FAIL,
                "Better BibTeX readiness check failed; restart Zotero and verify the extension.",
            )
        return DoctorCheck("better_bibtex", CheckStatus.PASS, "Better BibTeX is ready.")

    def _check_references_bib(self) -> DoctorCheck:
        path = self._settings.vault_path / "references.bib"
        if path.is_file():
            return DoctorCheck("references_bib", CheckStatus.PASS, "references.bib is present.")
        return DoctorCheck(
            "references_bib",
            CheckStatus.WARN,
            "references.bib is missing; configure a Better BibTeX keep-updated export.",
        )

    def _check_vault_paths(self) -> DoctorCheck:
        required_paths = (
            ("vault root", self._settings.vault_path, True),
            ("papers directory", self._settings.papers_dir, True),
            ("templates directory", self._settings.templates_dir, True),
            ("reading profile", self._settings.reading_profile_path, False),
            ("tag registry", self._settings.tag_registry_path, False),
        )
        missing = [
            f"{label} ({path})"
            for label, path, requires_directory in required_paths
            if not _matches_expected_path(path, requires_directory)
        ]
        if missing:
            return DoctorCheck(
                "vault_paths",
                CheckStatus.FAIL,
                f"Missing required vault path: {'; '.join(missing)}.",
            )
        return DoctorCheck("vault_paths", CheckStatus.PASS, "Required vault paths are present.")

    def _check_configuration(self) -> DoctorCheck:
        return DoctorCheck(
            "configuration",
            CheckStatus.PASS,
            "Required configuration is valid.",
        )

    def _check_write_authorization(self) -> DoctorCheck:
        path = self._settings.credentials_path
        server_id = self._zotero_info.server_id if self._zotero_info is not None else None
        if server_id is None:
            if self._settings.web_write_configured:
                return DoctorCheck(
                    "write_authorization",
                    CheckStatus.WARN,
                    "Local writes are unavailable; Web API fallback credentials are configured. "
                    "Run `uv run research authorize` to verify them.",
                )
            return DoctorCheck(
                "write_authorization",
                CheckStatus.WARN,
                "This Zotero build does not support local writes. Configure "
                "ZOTERO_WEB_API_KEY and ZOTERO_WEB_LIBRARY_ID for write operations.",
            )
        if not path.exists() and not path.is_symlink():
            if self._settings.web_write_configured:
                return DoctorCheck(
                    "write_authorization",
                    CheckStatus.WARN,
                    "Web API fallback credentials are configured; run `uv run research "
                    "authorize` to verify them.",
                )
            return DoctorCheck(
                "write_authorization",
                CheckStatus.WARN,
                "Local write credentials are not configured; they are only needed for write "
                "operations.",
            )
        try:
            key = CredentialStore(path).get(server_id)
        except CredentialStoreError:
            return DoctorCheck(
                "write_authorization",
                CheckStatus.FAIL,
                "Local write credentials are malformed or unsafe; run `uv run research "
                "authorize` to replace them.",
            )
        if key is None:
            return DoctorCheck(
                "write_authorization",
                CheckStatus.WARN,
                "No local write credential matches this Zotero server; run `uv run research "
                "authorize` before a write.",
            )
        return DoctorCheck(
            "write_authorization",
            CheckStatus.PASS,
            "A local write credential matches this Zotero server; no write was attempted.",
        )


def _matches_expected_path(path: Path, requires_directory: bool) -> bool:
    """Return whether a required path exists with the expected kind."""
    return path.is_dir() if requires_directory else path.is_file()
