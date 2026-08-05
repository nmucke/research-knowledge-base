"""Tests for step-2 doctor orchestration."""

from pathlib import Path

from research_kb.config import Settings
from research_kb.doctor_service import CheckStatus, DoctorCheck, DoctorReport, DoctorService
from research_kb.exceptions import BetterBibTeXUnavailableError, ZoteroUnavailableError
from research_kb.zotero_client import ZoteroServerInfo


class FakeZoteroClient:
    def __init__(self, result: ZoteroServerInfo | Exception) -> None:
        self.result = result
        self.calls = 0

    def discover(self) -> ZoteroServerInfo:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeBetterBibTeXClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    def check_ready(self) -> None:
        self.calls += 1
        if self.error is not None:
            raise self.error


def test_report_passes_with_expected_optional_warnings(tmp_path: Path) -> None:
    _create_required_vault_paths(tmp_path)
    service = _service(tmp_path)

    report = service.run()

    assert report.ok
    assert _statuses(report) == {
        "zotero": CheckStatus.PASS,
        "better_bibtex": CheckStatus.PASS,
        "references_bib": CheckStatus.WARN,
        "vault_paths": CheckStatus.PASS,
        "configuration": CheckStatus.PASS,
        "write_authorization": CheckStatus.WARN,
    }


def test_service_failures_do_not_short_circuit_other_checks(tmp_path: Path) -> None:
    _create_required_vault_paths(tmp_path)
    zotero = FakeZoteroClient(ZoteroUnavailableError())
    better_bibtex = FakeBetterBibTeXClient(BetterBibTeXUnavailableError())
    service = _service(tmp_path, zotero, better_bibtex)

    report = service.run()

    assert not report.ok
    assert zotero.calls == 1
    assert better_bibtex.calls == 1
    assert _statuses(report)["zotero"] is CheckStatus.FAIL
    assert _statuses(report)["better_bibtex"] is CheckStatus.FAIL
    assert _statuses(report)["references_bib"] is CheckStatus.WARN


def test_missing_server_id_is_a_healthy_compatibility_warning(tmp_path: Path) -> None:
    _create_required_vault_paths(tmp_path)
    service = _service(tmp_path, FakeZoteroClient(ZoteroServerInfo(3, None, 42)))

    report = service.run()

    check = _checks(report)["zotero"]
    assert report.ok
    assert check.status is CheckStatus.WARN
    assert "API 3, schema 42" in check.message
    assert "does not provide a server ID" in check.message


def test_missing_required_vault_path_fails(tmp_path: Path) -> None:
    _create_required_vault_paths(tmp_path)
    (tmp_path / "System" / "tag-registry.md").unlink()

    report = _service(tmp_path).run()

    check = _checks(report)["vault_paths"]
    assert not report.ok
    assert check.status is CheckStatus.FAIL
    assert "tag registry" in check.message


def test_references_and_credential_file_states(tmp_path: Path) -> None:
    _create_required_vault_paths(tmp_path)
    (tmp_path / "references.bib").write_text("% bibliography\n", encoding="utf-8")
    credentials = tmp_path / ".research" / "credentials.json"
    credentials.parent.mkdir()
    credentials.write_text('{"token":"secret"}', encoding="utf-8")

    report = _service(tmp_path).run()

    checks = _checks(report)
    assert checks["references_bib"].status is CheckStatus.PASS
    assert checks["write_authorization"].status is CheckStatus.WARN
    assert "secret" not in checks["write_authorization"].message
    assert "not tested" in checks["write_authorization"].message


def _service(
    tmp_path: Path,
    zotero: FakeZoteroClient | None = None,
    better_bibtex: FakeBetterBibTeXClient | None = None,
) -> DoctorService:
    return DoctorService(
        Settings(_env_file=None, research_vault_path=tmp_path),
        zotero or FakeZoteroClient(ZoteroServerInfo(3, "server-1", 1)),
        better_bibtex or FakeBetterBibTeXClient(),
    )


def _create_required_vault_paths(vault_path: Path) -> None:
    (vault_path / "Literature" / "Papers").mkdir(parents=True)
    (vault_path / "System" / "Templates").mkdir(parents=True)
    (vault_path / "System" / "reading-profile.md").write_text("profile\n", encoding="utf-8")
    (vault_path / "System" / "tag-registry.md").write_text("tags\n", encoding="utf-8")


def _checks(report: DoctorReport) -> dict[str, DoctorCheck]:
    return {check.name: check for check in report.checks}


def _statuses(report: DoctorReport) -> dict[str, CheckStatus]:
    return {check.name: check.status for check in report.checks}
