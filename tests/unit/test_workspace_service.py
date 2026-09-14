"""Workspace lifecycle tests use only synthetic temporary directories."""

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from research_kb import __version__
from research_kb.cli import app
from research_kb.config import Settings
from research_kb.workspace_service import (
    MANIFEST_NAME,
    WorkspaceError,
    export_public_source,
    initialize_workspace,
    migrate_workspace,
    privacy_audit,
    refresh_agent_files,
    workspace_status,
)


def test_explicit_settings_load_only_workspace_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "private"
    workspace.mkdir()
    (workspace / ".research-workspace.json").write_text("{}\n", encoding="utf-8")
    (workspace / ".env").write_text("RESEARCH_LOG_LEVEL=debug\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("RESEARCH_LOG_LEVEL=critical\n", encoding="utf-8")

    settings = Settings.for_workspace(workspace)

    assert settings.workspace_path == workspace
    assert settings.research_log_level == "DEBUG"
    assert settings.cache_dir == workspace / ".cache/research-kb"
    assert settings.paper_text_dir == workspace / ".cache/research-kb/paper-text"
    assert settings.state_dir == workspace / ".research"


def test_initializer_uses_packaged_neutral_assets(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"

    result = initialize_workspace(workspace)

    manifest = json.loads((workspace / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert (workspace / "vault/System/Templates/Paper.md").is_file()
    assert (workspace / "vault/Literature/Dashboards/Inbox.base").is_file()
    assert (workspace / "AGENTS.md").is_file()
    assert (workspace / ".codex/skills/paper-review/SKILL.md").is_file()
    assert (workspace / ".claude/skills/paper-review/SKILL.md").is_file()
    assert (workspace / ".agents/skills/paper-review/SKILL.md").is_file()
    launcher = workspace / "bin/research"
    launcher_text = launcher.read_text(encoding="utf-8")
    assert sys.executable in launcher_text
    assert f"--workspace {workspace}" in launcher_text
    assert launcher.stat().st_mode & 0o111
    assert str(workspace / "bin/research") in (workspace / ".codex/config.toml").read_text()
    claude_mcp = json.loads((workspace / ".mcp.json").read_text(encoding="utf-8"))
    assert claude_mcp["mcpServers"]["research"]["command"] == str(launcher)
    assert "Initialize this research workspace" in (workspace / "START_HERE.md").read_text()
    schema = json.loads((workspace / "setup-schema.json").read_text(encoding="utf-8"))
    assert schema["title"] == "SetupRequest"
    assert "expected_revision" in schema["properties"]
    plugin = workspace / ".research-tools/research-workspace.plugin.zip"
    with zipfile.ZipFile(plugin) as bundle:
        names = set(bundle.namelist())
        manifest = json.loads(bundle.read(".claude-plugin/plugin.json"))
        plugin_mcp = json.loads(bundle.read(".mcp.json"))
    assert manifest["name"].startswith("research-workspace-")
    assert manifest["version"] == __version__
    assert "skills/workspace-initialization/SKILL.md" in names
    start_here = (workspace / "START_HERE.md").read_text(encoding="utf-8")
    assert "Claude Code" in start_here
    assert "Cowork requires the plugin connection" in start_here
    assert "workspace refresh-agent-files" in start_here
    assert plugin_mcp["mcpServers"]["research"]["command"] == str(launcher)
    assert not any(name.startswith(("vault/", ".research/", ".env")) for name in names)
    assert list((workspace / "vault/Projects").glob("*.md")) == []
    assert MANIFEST_NAME in result.copied


def test_initializer_refuses_nonempty_destination_without_overwrite(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    marker = workspace / "keep.txt"
    marker.write_text("mine", encoding="utf-8")

    with pytest.raises(WorkspaceError, match="must be empty"):
        initialize_workspace(workspace)

    assert marker.read_text(encoding="utf-8") == "mine"


def test_workspace_cli_does_not_create_private_state_in_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.chdir(checkout)
    result = CliRunner().invoke(app, ["workspace", "init", str(tmp_path / "private")])
    assert result.exit_code == 0, result.output
    assert list(checkout.iterdir()) == []


def test_workspace_status_uses_explicit_selection_and_accepts_path_argument(tmp_path: Path) -> None:
    initialize_workspace(tmp_path)
    runner = CliRunner()
    implicit = runner.invoke(app, ["--workspace", str(tmp_path), "workspace", "status"])
    explicit = runner.invoke(app, ["workspace", "status", str(tmp_path)])
    assert implicit.exit_code == explicit.exit_code == 0
    assert json.loads(implicit.output) == json.loads(explicit.output)
    missing = runner.invoke(app, ["workspace", "audit", str(tmp_path / "missing")])
    assert missing.exit_code == 2


def test_clean_software_checkout_cannot_recreate_private_state(tmp_path: Path) -> None:
    (tmp_path / "src/research_kb").mkdir(parents=True)
    result = CliRunner().invoke(app, ["--workspace", str(tmp_path), "sync"])
    assert result.exit_code == 1
    assert "Select a private workspace" in result.output
    assert not (tmp_path / "vault").exists()
    assert not (tmp_path / ".research").exists()


def test_private_reports_remain_excluded_when_moved_under_docs(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "docs").mkdir(parents=True)
    (source / "docs/REPOSITORY_ASSESSMENT.md").write_text("Private library details.")
    (source / "docs/IMPLEMENTATION.md").write_text("Private workspace paths.")
    (source / "docs/public-guide.md").write_text("Generic public documentation.")
    assert not privacy_audit(source, public_source=True).safe
    export = tmp_path / "export"
    export_public_source(source, export)
    assert not (export / "docs/REPOSITORY_ASSESSMENT.md").exists()
    assert not (export / "docs/IMPLEMENTATION.md").exists()
    assert (export / "docs/public-guide.md").is_file()
    assert privacy_audit(export, public_source=True).safe


def test_migration_only_copies_private_content_and_installs_current_agents(tmp_path: Path) -> None:
    source = tmp_path / "legacy"
    destination = tmp_path / "new"
    (source / "vault/Projects").mkdir(parents=True)
    (source / "vault/Projects/topic.md").write_text("legacy", encoding="utf-8")
    (source / ".env").write_text("SECRET=private\n", encoding="utf-8")
    (source / ".research/paper-text").mkdir(parents=True)
    (source / ".research/paper-text/paper.txt").write_text("cache", encoding="utf-8")
    (source / ".git").mkdir()
    (source / ".git/private-history").write_text("private history", encoding="utf-8")
    (source / "src").mkdir()
    (source / "src/code.py").write_text("code", encoding="utf-8")
    (source / "CLAUDE.md").write_text("old instructions", encoding="utf-8")
    (source / "AGENTS.md").symlink_to("CLAUDE.md")

    result = migrate_workspace(source, destination)

    assert (destination / "vault/Projects/topic.md").read_text() == "legacy"
    assert "SECRET=private" in (destination / ".env").read_text()
    assert (destination / ".cache/research-kb/paper-text/paper.txt").is_file()
    assert not (destination / ".research/paper-text/paper.txt").exists()
    assert not (destination / ".git").exists()
    assert not (destination / "src").exists()
    assert "Research workspace" in (destination / "AGENTS.md").read_text()
    assert result.skipped == ()
    assert (destination / MANIFEST_NAME).is_file()
    assert (source / "vault/Projects/topic.md").read_text() == "legacy"
    runtime_text = (destination / "bin/research").read_text(encoding="utf-8")
    assert str(destination) in runtime_text
    assert ".research-migration-" not in runtime_text
    assert str(destination / "bin/research") in (destination / ".mcp.json").read_text()


def test_workspace_launcher_runs_module_outside_checkout_cwd(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace with spaces"
    initialize_workspace(workspace)

    completed = subprocess.run(
        [workspace / "bin/research", "--help"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Manage the local Zotero-Obsidian literature workflow" in completed.stdout


def test_refresh_agent_files_is_idempotent_and_preserves_private_content(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    initialize_workspace(workspace)
    private_note = workspace / "vault/Literature/Papers/private.md"
    private_note.write_text("private paper content", encoding="utf-8")
    env = workspace / ".env"
    env.write_text("PRIVATE=value\n", encoding="utf-8")
    state = workspace / ".research/private-state.json"
    state.write_text('{"private": true}\n', encoding="utf-8")
    mcp = json.loads((workspace / ".mcp.json").read_text(encoding="utf-8"))
    mcp["mcpServers"]["unrelated"] = {"command": "example"}
    (workspace / ".mcp.json").write_text(json.dumps(mcp), encoding="utf-8")
    (workspace / "AGENTS.md").write_text("outdated support", encoding="utf-8")

    first = refresh_agent_files(workspace)
    second = refresh_agent_files(workspace)

    assert "AGENTS.md" in first.copied
    assert second.copied == ()
    assert private_note.read_text(encoding="utf-8") == "private paper content"
    assert env.read_text(encoding="utf-8") == "PRIVATE=value\n"
    assert state.read_text(encoding="utf-8") == '{"private": true}\n'
    refreshed_mcp = json.loads((workspace / ".mcp.json").read_text(encoding="utf-8"))
    assert refreshed_mcp["mcpServers"]["unrelated"] == {"command": "example"}
    backups = list((workspace / ".research/support-backups").rglob("AGENTS.md"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "outdated support"


def test_refresh_cli_refuses_symlinked_support_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    initialize_workspace(workspace)
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil_target = workspace / ".agents"
    for path in sorted(shutil_target.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        else:
            path.rmdir()
    shutil_target.rmdir()
    shutil_target.symlink_to(outside, target_is_directory=True)

    result = CliRunner().invoke(
        app, ["--workspace", str(workspace), "workspace", "refresh-agent-files"]
    )

    assert result.exit_code == 2
    assert "symlinks" in result.output


def test_refresh_refuses_symlinked_manifest_and_backup_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()

    manifest_workspace = tmp_path / "manifest-workspace"
    initialize_workspace(manifest_workspace)
    (manifest_workspace / MANIFEST_NAME).unlink()
    (manifest_workspace / MANIFEST_NAME).symlink_to(outside / "manifest.json")
    with pytest.raises(WorkspaceError, match="symlinks"):
        refresh_agent_files(manifest_workspace)

    backup_workspace = tmp_path / "backup-workspace"
    initialize_workspace(backup_workspace)
    research = backup_workspace / ".research"
    for child in sorted(research.rglob("*"), reverse=True):
        if child.is_dir():
            child.rmdir()
        else:
            child.unlink()
    research.rmdir()
    research.symlink_to(outside, target_is_directory=True)
    with pytest.raises(WorkspaceError, match="symlinks"):
        refresh_agent_files(backup_workspace)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize(
    "manifest",
    [[], {"schema_version": 1}, {"schema_version": 999, "workspace_type": "research-kb"}],
)
def test_refresh_rejects_invalid_manifest(tmp_path: Path, manifest: object) -> None:
    workspace = tmp_path / "workspace"
    initialize_workspace(workspace)
    (workspace / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(WorkspaceError, match=MANIFEST_NAME):
        refresh_agent_files(workspace)


def test_refresh_repairs_launcher_executable_mode(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    initialize_workspace(workspace)
    launcher = workspace / "bin/research"
    original = launcher.read_bytes()
    launcher.chmod(0o644)

    result = refresh_agent_files(workspace)

    assert result.copied == ("bin/research",)
    assert launcher.read_bytes() == original
    assert launcher.stat().st_mode & 0o111


def test_migration_refuses_existing_destination_without_partial_copy(tmp_path: Path) -> None:
    source = tmp_path / "legacy"
    source.mkdir()
    destination = tmp_path / "new"
    destination.mkdir()
    (destination / "keep.txt").write_text("mine")
    with pytest.raises(WorkspaceError, match="must be empty"):
        migrate_workspace(source, destination)
    assert list(destination.iterdir()) == [destination / "keep.txt"]


def test_workspace_operations_refuse_symlinks(tmp_path: Path) -> None:
    source = tmp_path / "legacy"
    source.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    (source / "vault").mkdir()
    (source / "vault/linked").symlink_to(outside)

    with pytest.raises(WorkspaceError, match="symlinks"):
        migrate_workspace(source, tmp_path / "new")


def test_status_counts_only_notes_without_reading_them(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    initialize_workspace(workspace)
    (workspace / "vault/Literature/Papers/example.md").write_text("secret", encoding="utf-8")
    (workspace / "vault/Projects/example.md").write_text("secret", encoding="utf-8")

    status = workspace_status(workspace)

    assert status.initialized
    assert status.papers == 1
    assert status.projects == 1


def test_public_export_is_allowlisted_and_passes_privacy_audit(tmp_path: Path) -> None:
    source = tmp_path / "repository"
    (source / "src/package/assets/vault").mkdir(parents=True)
    (source / "src/package/module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "src/package/assets/vault/template.md").write_text("neutral\n", encoding="utf-8")
    (source / "vault/Projects").mkdir(parents=True)
    (source / "vault/Projects/private.md").write_text("private\n", encoding="utf-8")
    (source / ".env").write_text("SECRET=private\n", encoding="utf-8")
    (source / "README.md").write_text("public\n", encoding="utf-8")

    destination = tmp_path / "release"
    export_public_source(source, destination)

    assert (destination / "src/package/module.py").is_file()
    assert (destination / "src/package/assets/vault/template.md").is_file()
    assert not (destination / "vault").exists()
    assert not (destination / ".env").exists()
    assert privacy_audit(destination, public_source=True).safe
