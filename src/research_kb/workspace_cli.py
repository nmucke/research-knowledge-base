"""CLI commands for bounded workspace lifecycle operations."""

from pathlib import Path
from typing import Annotated, cast

import typer

from research_kb.config import Settings
from research_kb.setup_cli import setup_app
from research_kb.workspace_service import (
    CopyResult,
    PrivacyAudit,
    WorkspaceError,
    WorkspaceStatus,
    export_public_source,
    initialize_workspace,
    migrate_workspace,
    privacy_audit,
    refresh_agent_files,
    result_json,
    workspace_status,
)

workspace_app = typer.Typer(help="Create, migrate, inspect, and export research workspaces.")
workspace_app.add_typer(setup_app, name="setup")


def _emit(operation: CopyResult | WorkspaceStatus | PrivacyAudit) -> None:
    typer.echo(result_json(operation))


def _fail(error: WorkspaceError | OSError) -> None:
    typer.echo(str(error), err=True)
    raise typer.Exit(code=2) from error


@workspace_app.command("init")
def init(path: Path) -> None:
    """Create a workspace from packaged neutral starter assets."""
    try:
        _emit(initialize_workspace(path))
    except (WorkspaceError, OSError) as error:
        _fail(error)


@workspace_app.command()
def migrate(source: Path, destination: Path) -> None:
    """Safely copy a legacy workspace to a separate location."""
    try:
        _emit(migrate_workspace(source, destination))
    except (WorkspaceError, OSError) as error:
        _fail(error)


@workspace_app.command("refresh-agent-files")
def refresh_agent_support(
    ctx: typer.Context, path: Annotated[Path | None, typer.Argument()] = None
) -> None:
    """Refresh workspace-local agent instructions, skills, launchers, and client configs."""
    try:
        selected = path or cast(Settings, ctx.obj["settings"]).workspace_path
        _emit(refresh_agent_files(selected))
    except (WorkspaceError, OSError) as error:
        _fail(error)


@workspace_app.command()
def status(ctx: typer.Context, path: Annotated[Path | None, typer.Argument()] = None) -> None:
    """Show non-sensitive workspace health and content counts."""
    try:
        selected = path or cast(Settings, ctx.obj["settings"]).workspace_path
        _emit(workspace_status(selected))
    except (WorkspaceError, OSError) as error:
        _fail(error)


@workspace_app.command()
def audit(
    ctx: typer.Context,
    path: Annotated[Path | None, typer.Argument()] = None,
    public_source: bool = False,
) -> None:
    """Check a workspace or public export for privacy boundary violations."""
    try:
        selected = path or cast(Settings, ctx.obj["settings"]).workspace_path
        result = privacy_audit(selected, public_source=public_source)
        _emit(result)
        if not result.safe:
            raise typer.Exit(code=1)
    except (WorkspaceError, OSError) as error:
        _fail(error)


@workspace_app.command("export-source")
def export_source(destination: Path, source: Path = Path(".")) -> None:
    """Create an allowlisted public source tree without private workspace data."""
    try:
        _emit(export_public_source(source, destination))
    except (WorkspaceError, OSError) as error:
        _fail(error)
