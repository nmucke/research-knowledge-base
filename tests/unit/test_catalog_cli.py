"""Trusted catalog CLI lifecycle tests with a synthetic workspace."""

import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from research_kb.catalog_operations import CatalogOperations, ProjectCreateRequest
from research_kb.cli import app
from research_kb.config import Settings

ASSETS = Path(__file__).parents[2] / "src/research_kb/assets/vault"


def test_catalog_reject_persists_decision_without_creating_project(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    shutil.copytree(ASSETS, workspace / "vault")
    settings = Settings(_env_file=None, research_vault_path=workspace)
    settings.papers_dir.mkdir(parents=True, exist_ok=True)
    proposal = CatalogOperations(settings).propose(
        ProjectCreateRequest(
            project_id="rejected-project",
            title="Rejected project",
            description="Synthetic project.",
            goals=("Test rejection",),
            scope_in=("Synthetic data",),
            scope_out=("Private data",),
            rationale="Exercise the trusted rejection command.",
        )
    )

    result = CliRunner().invoke(
        app,
        ["--workspace", str(workspace), "catalog", "reject", proposal.proposal_id],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["files"]
    assert CatalogOperations(settings)._load(proposal.proposal_id).status == "rejected"
    assert not (settings.projects_dir / "rejected-project.md").exists()
    mirror = settings.obsidian_vault_path / f"System/Proposals/{proposal.proposal_id}.md"
    assert "pending_count: 0" in mirror.read_text(encoding="utf-8")
