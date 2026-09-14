"""Create, inspect, migrate, and export bounded research workspaces."""

from __future__ import annotations

import json
import shlex
import shutil
import sys
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from hashlib import sha256
from importlib import metadata, resources
from importlib.resources.abc import Traversable
from pathlib import Path
from uuid import uuid4

from research_kb import __version__

MANIFEST_NAME = ".research-workspace.json"
MANIFEST_SCHEMA_VERSION = 1
PUBLIC_SOURCE_FILES = frozenset(
    {
        "LICENSE",
        "README.md",
        "pyproject.toml",
        "uv.lock",
        "AGENTS.md",
        "CLAUDE.md",
        ".gitignore",
        ".env.example",
    }
)
PUBLIC_SOURCE_DIRS = frozenset({"src", "tests", ".agents", ".github", "docs"})
PRIVATE_NAMES = frozenset(
    {
        ".env",
        ".git",
        ".research",
        ".cache",
        "references.bib",
        "vault",
        "Projects",
        "Papers",
        "credentials.json",
    }
)
IGNORED_BUILD_NAMES = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})
PRIVATE_REPORT_NAMES = frozenset(
    {"REPOSITORY_ASSESSMENT.md", "SKILL_RECOMMENDATIONS.md", "IMPLEMENTATION.md"}
)


class WorkspaceError(ValueError):
    """A workspace operation would cross a safety boundary."""


@dataclass(frozen=True)
class CopyResult:
    copied: tuple[str, ...]
    skipped: tuple[str, ...]


@dataclass(frozen=True)
class WorkspaceStatus:
    path: str
    initialized: bool
    schema_version: int | None
    obsidian_vault: str
    papers: int
    projects: int


@dataclass(frozen=True)
class PrivacyAudit:
    path: str
    safe: bool
    findings: tuple[str, ...]


def _safe_root(path: Path | str, *, must_exist: bool) -> Path:
    candidate = Path(path).expanduser()
    current = candidate.absolute()
    for component in (current, *current.parents):
        if component.is_symlink():
            raise WorkspaceError(f"workspace path must not contain a symlink: {component}")
    resolved = candidate.resolve(strict=must_exist)
    if resolved == Path(resolved.anchor):
        raise WorkspaceError("filesystem root cannot be used as a workspace")
    return resolved


def _assert_no_symlink(path: Path, root: Path) -> None:
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise WorkspaceError(f"symlinks are not allowed in workspace operations: {current}")


def _asset_destination(relative: Path) -> tuple[Path, ...]:
    if relative.parts[0] == "agents":
        return (Path(relative.name),)
    if relative.parts[0] == "skills":
        skill_path = Path(*relative.parts[1:])
        return (
            Path(".agents/skills") / skill_path,
            Path(".codex/skills") / skill_path,
            Path(".claude/skills") / skill_path,
        )
    return (relative,)


def _copy_assets(node: Traversable, relative: Path, root: Path, copied: list[str]) -> None:
    for child in node.iterdir():
        child_relative = relative / child.name
        if child.is_dir():
            _copy_assets(child, child_relative, root, copied)
            continue
        for destination_relative in _asset_destination(child_relative):
            destination = root / destination_relative
            _assert_no_symlink(destination.parent, root)
            if destination.exists():
                raise WorkspaceError(f"initializer refuses to overwrite: {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(child.read_bytes())
            copied.append(destination_relative.as_posix())


def _write_runtime_files(root: Path, logical_root: Path, copied: list[str]) -> None:
    """Install workspace-local launch and MCP configuration for the final path."""
    interpreter = sys.executable
    launcher = root / "bin/research"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(
        "#!/bin/sh\n"
        f"exec {shlex.quote(interpreter)} -m research_kb.cli "
        f'--workspace {shlex.quote(str(logical_root))} "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    launcher_command = str(logical_root / "bin/research")
    codex_config = root / ".codex/config.toml"
    codex_config.parent.mkdir(parents=True, exist_ok=True)
    codex_config.write_text(
        f'[mcp_servers.research]\ncommand = {json.dumps(launcher_command)}\nargs = ["mcp"]\n'
        "startup_timeout_sec = 20\ntool_timeout_sec = 180\n",
        encoding="utf-8",
    )
    codex_fragment = root / "codex-mcp.toml"
    codex_fragment.write_text(codex_config.read_text(encoding="utf-8"), encoding="utf-8")
    claude_config = root / ".mcp.json"
    claude_config.write_text(
        json.dumps(
            {"mcpServers": {"research": {"command": launcher_command, "args": ["mcp"]}}},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    desktop_fragment = root / "claude-desktop-mcp.json"
    desktop_fragment.write_text(claude_config.read_text(encoding="utf-8"), encoding="utf-8")
    from research_kb.setup_service import SetupRequest

    setup_schema = root / "setup-schema.json"
    setup_schema.write_text(
        json.dumps(SetupRequest.model_json_schema(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_cowork_plugin(root, logical_root)
    start_here = root / "START_HERE.md"
    start_here.write_text(
        "# Start here\n\n"
        "For a terminal-based client, open a terminal in this workspace, launch Codex or "
        "Claude Code, and prompt:\n\n"
        "> Initialize this research workspace.\n\n"
        "The research agent will guide you through your reading interests, controlled tags, "
        "and initial projects. It will preview the setup and, once you accept the concrete "
        "choices, run the one-time setup command for you. Later curation decisions remain "
        "separate.\n\n"
        "Use `./bin/research doctor` to check the workspace and `./bin/research --help` to "
        "inspect commands. Local Codex and Claude MCP configuration uses this launcher, which "
        "always selects this workspace.\n\n"
        "For Claude Code, `.mcp.json` is the project MCP configuration. For Claude Desktop "
        "Chat, `claude-desktop-mcp.json` is a host configuration fragment; copy it into the "
        "appropriate host configuration yourself. Claude Cowork does not use that Desktop "
        "configuration file. To connect Cowork, first open the Cowork tab, then choose "
        "**Customize** in the left sidebar, open **Plugins**, use the custom-plugin upload in "
        "the Browse Plugins area, and select `.research-tools/research-workspace.plugin.zip`. "
        "The plugin's bundled local MCP server runs on the host.\n\n"
        f"The launcher currently uses `{interpreter}`. Keep that Python environment available. "
        "After an editable checkout installation, deleting or moving the checkout or its `.venv` "
        "breaks the launcher. Restore that environment, then run "
        "`uv run research workspace refresh-agent-files "
        f"{shlex.quote(str(logical_root))}` from the research-kb "
        "checkout to regenerate the launcher and client files. "
        "Workspace init refuses existing directories, so do not rerun it to repair a launcher. "
        "Codex or Claude must already be installed. A client may ask you to trust the workspace "
        "or enable its local MCP server. In a native terminal, the `./bin/research` CLI works "
        "before MCP is connected; Cowork requires the plugin connection described above.\n",
        encoding="utf-8",
    )
    copied.extend(
        (
            "bin/research",
            ".codex/config.toml",
            "codex-mcp.toml",
            ".mcp.json",
            "claude-desktop-mcp.json",
            "setup-schema.json",
            ".research-tools/research-workspace.plugin.zip",
            "START_HERE.md",
        )
    )


def _write_cowork_plugin(root: Path, logical_root: Path) -> None:
    """Build a host-executed Cowork plugin containing only MCP config and skills."""
    archive = root / ".research-tools/research-workspace.plugin.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    identity = sha256(str(logical_root).encode("utf-8")).hexdigest()[:12]
    manifest = {
        "name": f"research-workspace-{identity}",
        "version": __version__,
        "description": "Research tools bound to one local workspace.",
    }
    mcp = {
        "mcpServers": {"research": {"command": str(logical_root / "bin/research"), "args": ["mcp"]}}
    }
    skills = resources.files("research_kb").joinpath("assets", "skills")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        _zip_write(bundle, ".claude-plugin/plugin.json", json.dumps(manifest, indent=2) + "\n")
        _zip_write(bundle, ".mcp.json", json.dumps(mcp, indent=2) + "\n")
        _zip_assets(bundle, skills, Path("skills"))


def _zip_assets(bundle: zipfile.ZipFile, node: Traversable, relative: Path) -> None:
    for child in sorted(node.iterdir(), key=lambda item: item.name):
        target = relative / child.name
        if child.is_dir():
            _zip_assets(bundle, child, target)
        else:
            _zip_write(bundle, target.as_posix(), child.read_bytes())


def _zip_write(bundle: zipfile.ZipFile, name: str, content: str | bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    bundle.writestr(info, content)


def _initialize_workspace(path: Path | str, *, logical_target: Path | None = None) -> CopyResult:
    """Initialize an empty directory from packaged, identity-neutral starter assets."""
    root = _safe_root(path, must_exist=False)
    logical_root = root if logical_target is None else _safe_root(logical_target, must_exist=False)
    if root.exists() and any(root.iterdir()):
        raise WorkspaceError(f"workspace directory must be empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    _copy_assets(resources.files("research_kb").joinpath("assets"), Path(), root, copied)
    for directory in (
        root / "vault/Literature/Papers",
        root / "vault/Projects",
        root / ".research/logs",
        root / ".research/recovery",
        root / ".research/review-snapshots",
        root / ".cache/research-kb/paper-text",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    try:
        package_version = metadata.version("research-kb")
    except metadata.PackageNotFoundError:
        package_version = "0+unknown"
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "workspace_type": "research-kb",
        "software_version": package_version,
    }
    manifest_path = root / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    copied.append(MANIFEST_NAME)
    _write_runtime_files(root, logical_root, copied)
    return CopyResult(tuple(sorted(copied)), ())


def initialize_workspace(path: Path | str) -> CopyResult:
    """Initialize an empty workspace with assets and local runtime entrypoints."""
    return _initialize_workspace(path)


def refresh_agent_files(path: Path | str) -> CopyResult:
    """Refresh generated agent support without touching private research content."""
    root = _safe_root(path, must_exist=True)
    manifest_path = root / MANIFEST_NAME
    _assert_no_symlink(manifest_path, root)
    if not manifest_path.is_file():
        raise WorkspaceError(f"workspace is missing {MANIFEST_NAME}: {root}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkspaceError(f"workspace has an invalid {MANIFEST_NAME}: {error}") from error
    if not isinstance(manifest, dict):
        raise WorkspaceError(f"workspace has a non-object {MANIFEST_NAME}")
    if (
        manifest.get("workspace_type") != "research-kb"
        or manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION
    ):
        raise WorkspaceError(f"workspace has an unsupported {MANIFEST_NAME}")
    with tempfile.TemporaryDirectory(prefix="research-support-") as temporary_name:
        generated = Path(temporary_name).resolve() / "workspace"
        _initialize_workspace(generated, logical_target=root)
        current_mcp = root / ".mcp.json"
        merged_mcp: bytes | None = None
        if current_mcp.exists():
            _assert_no_symlink(current_mcp, root)
            try:
                current_data = json.loads(current_mcp.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise WorkspaceError(f"cannot safely merge {current_mcp}: {error}") from error
            if not isinstance(current_data, dict):
                raise WorkspaceError(f"cannot safely merge non-object {current_mcp}")
            servers = current_data.setdefault("mcpServers", {})
            if not isinstance(servers, dict):
                raise WorkspaceError(f"cannot safely merge non-object mcpServers in {current_mcp}")
            desired_data = json.loads((generated / ".mcp.json").read_text(encoding="utf-8"))
            servers["research"] = desired_data["mcpServers"]["research"]
            merged_mcp = (json.dumps(current_data, indent=2) + "\n").encode("utf-8")
        candidates = [
            path
            for path in generated.rglob("*")
            if path.is_file()
            and (
                path.relative_to(generated).parts[0]
                in {".agents", ".claude", ".research-tools", "bin"}
                or path.relative_to(generated).as_posix()
                in {
                    "AGENTS.md",
                    "CLAUDE.md",
                    "START_HERE.md",
                    "setup-schema.json",
                    "codex-mcp.toml",
                    "claude-desktop-mcp.json",
                    ".mcp.json",
                }
                or path.relative_to(generated).parts[:2] == (".codex", "skills")
            )
        ]
        desired_codex = (generated / ".codex/config.toml").read_bytes()
        codex_config = root / ".codex/config.toml"
        if codex_config.exists():
            _assert_no_symlink(codex_config, root)
        if not codex_config.exists() or codex_config.read_bytes() == desired_codex:
            candidates.append(generated / ".codex/config.toml")
        copied: list[str] = []
        skipped: list[str] = []
        backup_root = root / ".research/support-backups" / uuid4().hex
        _assert_no_symlink(backup_root, root)
        for source in sorted(candidates):
            relative = source.relative_to(generated)
            destination = root / relative
            _assert_no_symlink(destination.parent, root)
            desired = (
                merged_mcp
                if relative == Path(".mcp.json") and merged_mcp is not None
                else source.read_bytes()
            )
            if destination.exists():
                _assert_no_symlink(destination, root)
                if destination.read_bytes() == desired:
                    if relative == Path("bin/research") and not (
                        destination.stat().st_mode & 0o111
                    ):
                        destination.chmod(0o755)
                        copied.append(relative.as_posix())
                        continue
                    skipped.append(relative.as_posix())
                    continue
                backup = backup_root / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination, backup)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(desired)
            if relative == Path("bin/research"):
                destination.chmod(0o755)
            copied.append(relative.as_posix())
        return CopyResult(tuple(copied), tuple(skipped))


def migrate_workspace(source: Path | str, destination: Path | str) -> CopyResult:
    """Stage a verified private-data copy, leaving the legacy workspace intact.

    Software, Git history, virtual environments and old agent instructions are
    never copied. New role-specific instructions come from packaged assets.
    Credentials/configuration are copied opaquely and never printed.
    """
    from research_kb.config import Settings

    source_root = _safe_root(source, must_exist=True)
    destination_root = _safe_root(destination, must_exist=False)
    if (
        source_root == destination_root
        or source_root in destination_root.parents
        or destination_root in source_root.parents
    ):
        raise WorkspaceError("migration destination must be separate from the source workspace")
    if destination_root.exists() and any(destination_root.iterdir()):
        raise WorkspaceError(
            "migration destination must be empty; existing files are never skipped"
        )
    settings = Settings.for_workspace(source_root)
    destination_root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".research-migration-", dir=destination_root.parent))
    copied: list[str] = []
    try:
        _initialize_workspace(staging, logical_target=destination_root)
        roots = [
            (settings.obsidian_vault_path, Path("vault")),
            (settings.research_dir, Path(".research")),
            (settings.cache_dir, Path(".cache/research-kb")),
        ]
        selected: list[tuple[Path, Path]] = []
        for origin, prefix in roots:
            if not origin.exists():
                continue
            _assert_no_symlink(origin, source_root)
            for path in sorted(origin.rglob("*")):
                _assert_no_symlink(path, source_root)
                if path.is_file():
                    relative = prefix / path.relative_to(origin)
                    if relative.parts[:2] == (".research", "paper-text"):
                        relative = Path(".cache/research-kb/paper-text", *relative.parts[2:])
                    selected.append((path, relative))
        for name in ("references.bib", ".env"):
            path = source_root / name
            if path.exists():
                _assert_no_symlink(path, source_root)
                selected.append((path, Path(name)))
        # Preflight every selected source before copying anything into the final destination.
        for origin, relative in selected:
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, target)
            if sha256(origin.read_bytes()).digest() != sha256(target.read_bytes()).digest():
                raise WorkspaceError(f"migration verification failed: {relative}")
            copied.append(relative.as_posix())
        # An absolute Obsidian path is rooted in the old workspace. Override only
        # workspace routing keys; retain opaque credential lines verbatim.
        env_path = staging / ".env"
        if env_path.exists():
            lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
            routed = [
                line
                for line in lines
                if not line.lstrip().startswith(("RESEARCH_VAULT_PATH=", "RESEARCH_OBSIDIAN_DIR="))
            ]
            env_path.write_text(
                "".join(routed).rstrip("\n")
                + "\nRESEARCH_VAULT_PATH=.\nRESEARCH_OBSIDIAN_DIR=vault\n",
                encoding="utf-8",
            )
            env_path.chmod(0o600)
        if destination_root.exists():
            destination_root.rmdir()  # preflight requires an empty directory
        staging.rename(destination_root)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return CopyResult(tuple(sorted(copied)), ())


def workspace_status(path: Path | str) -> WorkspaceStatus:
    root = _safe_root(path, must_exist=True)
    manifest_path = root / MANIFEST_NAME
    papers_dir = root / "vault/Literature/Papers"
    projects_dir = root / "vault/Projects"
    for inspected in (manifest_path, papers_dir, projects_dir):
        _assert_no_symlink(inspected, root)
    schema_version: int | None = None
    if manifest_path.is_file():
        try:
            value = json.loads(manifest_path.read_text(encoding="utf-8")).get("schema_version")
            schema_version = value if isinstance(value, int) else None
        except (OSError, json.JSONDecodeError):
            pass
    vault = root / "vault"
    return WorkspaceStatus(
        path=str(root),
        initialized=schema_version is not None,
        schema_version=schema_version,
        obsidian_vault=str(vault),
        papers=sum(1 for _ in papers_dir.glob("*.md")),
        projects=sum(1 for _ in projects_dir.glob("*.md")),
    )


def privacy_audit(path: Path | str, *, public_source: bool = False) -> PrivacyAudit:
    root = _safe_root(path, must_exist=True)
    findings: list[str] = []
    for entry in root.rglob("*"):
        relative = entry.relative_to(root)
        if entry.is_symlink():
            findings.append(f"symlink: {relative.as_posix()}")
        private_public_path = (
            relative.parts[0] in PRIVATE_NAMES
            or relative.name
            in {
                ".env",
                "credentials.json",
                "references.bib",
            }
            | PRIVATE_REPORT_NAMES
        )
        if public_source and private_public_path:
            findings.append(f"private path: {relative.as_posix()}")
    if not public_source and not (root / MANIFEST_NAME).is_file():
        findings.append(f"missing {MANIFEST_NAME}")
    return PrivacyAudit(str(root), not findings, tuple(sorted(set(findings))))


def export_public_source(source: Path | str, destination: Path | str) -> CopyResult:
    """Copy only release-allowlisted source files into a new empty directory."""
    source_root = _safe_root(source, must_exist=True)
    destination_root = _safe_root(destination, must_exist=False)
    if source_root == destination_root or source_root in destination_root.parents:
        raise WorkspaceError("export destination must be outside the source tree")
    if destination_root.exists() and any(destination_root.iterdir()):
        raise WorkspaceError(f"export destination must be empty: {destination_root}")
    destination_root.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    candidates = [source_root / name for name in sorted(PUBLIC_SOURCE_FILES)]
    for dirname in sorted(PUBLIC_SOURCE_DIRS):
        directory = source_root / dirname
        if directory.exists():
            candidates.extend(path for path in directory.rglob("*") if path.is_file())
    for path in candidates:
        if not path.exists():
            continue
        # A checkout may use AGENTS.md -> CLAUDE.md to share development
        # instructions. Materialize only this exact, internal alias in exports.
        if path.name == "AGENTS.md" and path.is_symlink():
            if path.resolve() != source_root / "CLAUDE.md":
                raise WorkspaceError("AGENTS.md must resolve to the checkout CLAUDE.md")
        else:
            _assert_no_symlink(path, source_root)
        relative = path.relative_to(source_root)
        if (
            relative.name in PRIVATE_REPORT_NAMES
            or relative.parts[0] in PRIVATE_NAMES
            or any(part in IGNORED_BUILD_NAMES for part in relative.parts)
        ):
            continue
        target = destination_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied.append(relative.as_posix())
    audit = privacy_audit(destination_root, public_source=True)
    if not audit.safe:
        raise WorkspaceError(
            "public source export failed privacy audit: " + "; ".join(audit.findings)
        )
    return CopyResult(tuple(sorted(copied)), ())


def result_json(result: CopyResult | WorkspaceStatus | PrivacyAudit) -> str:
    """Return deterministic JSON for CLI and automation clients."""
    return json.dumps(asdict(result), indent=2, sort_keys=True)
