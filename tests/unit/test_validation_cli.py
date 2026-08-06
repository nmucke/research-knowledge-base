"""CLI contract tests for whole-vault and targeted validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

import pytest
from typer.testing import CliRunner

import research_kb.cli as cli
from research_kb.config import Settings
from research_kb.exceptions import ValidationError
from research_kb.markdown_store import MarkdownStore

runner = CliRunner()


class FakeSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class FakeIssue:
    severity: FakeSeverity
    path: Path
    code: str
    message: str


@dataclass(frozen=True)
class FakeReport:
    checked_count: int
    issues: tuple[FakeIssue, ...] = ()

    @property
    def errors(self) -> tuple[FakeIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is FakeSeverity.ERROR)

    @property
    def warnings(self) -> tuple[FakeIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is FakeSeverity.WARNING)

    @property
    def ok(self) -> bool:
        return not self.errors


class FakeValidationService:
    result: ClassVar[FakeReport | ValidationError]
    calls: ClassVar[list[tuple[Path, Path, str | None]]] = []
    consumed: ClassVar[list[str]] = []

    def __init__(self, settings: Settings, store: MarkdownStore) -> None:
        self.settings = settings
        self.store = store

    @classmethod
    def reset(cls, result: FakeReport | ValidationError) -> None:
        cls.result = result
        cls.calls = []
        cls.consumed = []

    def run(self, citekey: str | None) -> FakeReport:
        type(self).calls.append((self.settings.vault_path, self.store.papers_dir, citekey))
        if isinstance(self.result, ValidationError):
            raise self.result
        return self.result

    def consume_workflow_snapshot(self, citekey: str) -> None:
        type(self).consumed.append(citekey)


def _invoke(tmp_path: Path, *arguments: str) -> object:
    return runner.invoke(
        cli.app,
        ["validate", *arguments],
        env={"RESEARCH_VAULT_PATH": str(tmp_path)},
    )


def test_validate_whole_vault_passes_and_prints_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeValidationService.reset(FakeReport(checked_count=3))
    monkeypatch.setattr(cli, "ValidationService", FakeValidationService)

    result = _invoke(tmp_path)

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["Summary: checked=3, errors=0, warnings=0"]
    assert FakeValidationService.calls == [
        (tmp_path.resolve(), tmp_path / "Literature" / "Papers", None)
    ]


def test_validate_warning_only_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeValidationService.reset(
        FakeReport(
            checked_count=1,
            issues=(
                FakeIssue(
                    FakeSeverity.WARNING,
                    Path("Literature/Papers/paper.md"),
                    "review-outdated",
                    "AI review is outdated.",
                ),
            ),
        )
    )
    monkeypatch.setattr(cli, "ValidationService", FakeValidationService)

    result = _invoke(tmp_path)

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "WARN Literature/Papers/paper.md: [review-outdated] AI review is outdated.",
        "Summary: checked=1, errors=0, warnings=1",
    ]


def test_validate_errors_are_deterministic_and_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeValidationService.reset(
        FakeReport(
            checked_count=2,
            issues=(
                FakeIssue(FakeSeverity.WARNING, Path("z.md"), "z-warning", "Warning."),
                FakeIssue(FakeSeverity.ERROR, Path("b.md"), "b-error", "Second error."),
                FakeIssue(FakeSeverity.ERROR, Path("a.md"), "a-error", "First error."),
            ),
        )
    )
    monkeypatch.setattr(cli, "ValidationService", FakeValidationService)

    result = _invoke(tmp_path)

    assert result.exit_code == 1
    assert result.stdout.splitlines() == [
        "ERROR a.md: [a-error] First error.",
        "ERROR b.md: [b-error] Second error.",
        "WARN z.md: [z-warning] Warning.",
        "Summary: checked=2, errors=2, warnings=1",
    ]


def test_validate_forwards_optional_citekey(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeValidationService.reset(FakeReport(checked_count=1))
    monkeypatch.setattr(cli, "ValidationService", FakeValidationService)

    result = _invoke(tmp_path, "doeUseful2026")

    assert result.exit_code == 0
    assert FakeValidationService.calls[-1][2] == "doeUseful2026"
    assert FakeValidationService.consumed == ["doeUseful2026"]


def test_validate_domain_error_uses_standard_error_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeValidationService.reset(ValidationError("Malformed paper note."))
    monkeypatch.setattr(cli, "ValidationService", FakeValidationService)

    result = _invoke(tmp_path, "doeUseful2026")

    assert result.exit_code == 1
    assert result.stderr.splitlines() == ["Error: Malformed paper note."]


def test_validate_help_shows_optional_citekey() -> None:
    result = runner.invoke(cli.app, ["validate", "--help"], color=False)

    output = " ".join(re.sub(r"\x1b\[[0-9;]*m", "", result.stdout).split())
    assert result.exit_code == 0
    assert "[citekey]" in output
    assert "complete literature vault" in output
