"""Tests for the step-1 CLI contract."""

from pathlib import Path

from typer.testing import CliRunner

from research_kb.cli import app

runner = CliRunner()


def test_help_starts_successfully() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Manage the local Zotero-Obsidian literature workflow" in result.stdout
    assert "doctor" in result.stdout


def test_version_starts_successfully() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.startswith("research ")


def test_doctor_marks_next_implementation_boundary(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["doctor"],
        env={"RESEARCH_VAULT_PATH": str(tmp_path)},
    )

    assert result.exit_code == 2
    assert "implementation step 2" in " ".join(result.output.split())

    log_text = (tmp_path / ".research" / "logs" / "research.log").read_text(
        encoding="utf-8"
    )
    assert "command_start command=doctor" in log_text
