"""Privacy and portability contract for a public source export."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from research_kb.workspace_service import export_public_source, privacy_audit

ROOT = Path(__file__).parents[2]
PRIVATE_TOP_LEVEL = {
    ".env",
    ".git",
    ".research",
    "vault",
    "references.bib",
    "REPOSITORY_ASSESSMENT.md",
    "SKILL_RECOMMENDATIONS.md",
}
RESEARCH_SKILLS = {
    "paper-review",
    "project-curation",
    "research-synthesis",
    "curation-approval",
    "literature-discovery",
    "workspace-initialization",
}


@pytest.fixture
def public_export(tmp_path: Path) -> Path:
    destination = tmp_path / "public-export"
    export_public_source(ROOT, destination)
    return destination


def test_public_export_has_no_private_checkout_state(public_export: Path) -> None:
    assert privacy_audit(public_export, public_source=True).safe
    assert not PRIVATE_TOP_LEVEL & {path.name for path in public_export.iterdir()}
    assert not any(path.is_symlink() for path in public_export.rglob("*"))
    assert not any(path.name == "credentials.json" for path in public_export.rglob("*"))


def test_public_export_contains_development_and_packaged_research_roles(
    public_export: Path,
) -> None:
    assert (public_export / "AGENTS.md").is_file()
    assert (public_export / "CLAUDE.md").is_file()
    assert (public_export / ".github" / "workflows" / "test.yml").is_file()
    assert (public_export / ".agents" / "skills" / "workflow-evaluation" / "SKILL.md").is_file()

    packaged = public_export / "src" / "research_kb" / "assets" / "skills"
    assert RESEARCH_SKILLS <= {path.name for path in packaged.iterdir() if path.is_dir()}
    for name in RESEARCH_SKILLS:
        assert (packaged / name / "SKILL.md").is_file()
    for client in (".codex", ".claude"):
        assert not any(
            (public_export / client / "skills" / name / "SKILL.md").exists()
            for name in RESEARCH_SKILLS
        )


def test_exported_checkout_imports_from_outside_its_directory(public_export: Path) -> None:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(public_export / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import sys; "
                f"sys.path.insert(0, {str(public_export / 'src')!r}); "
                "import research_kb; "
                "from research_kb.agent_api import ResearchAPI; "
                "print(research_kb.__version__, ResearchAPI.__name__)"
            ),
        ],
        cwd=public_export.parent,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "ResearchAPI" in result.stdout
