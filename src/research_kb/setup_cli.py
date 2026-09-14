"""Agent-assisted initial personalization; separate from recurring curation."""

from pathlib import Path
from typing import Annotated, cast

import typer

from research_kb.api_cli import emit, fail
from research_kb.config import Settings
from research_kb.exceptions import ResearchKBError
from research_kb.setup_service import SetupRequest, SetupService

setup_app = typer.Typer(help="Inspect, preview, and apply one-time workspace personalization.")


@setup_app.command("status")
def status(ctx: typer.Context) -> None:
    try:
        emit(SetupService(cast(Settings, ctx.obj["settings"])).status())
    except (ValueError, OSError, ResearchKBError) as error:
        fail(error)


@setup_app.command("schema")
def schema() -> None:
    emit(SetupRequest.model_json_schema())


def _run(ctx: typer.Context, path: Path, *, apply: bool) -> None:
    try:
        request = SetupRequest.model_validate_json(path.read_text(encoding="utf-8"))
        service = SetupService(cast(Settings, ctx.obj["settings"]))
        emit(service.apply(request) if apply else service.preview(request))
    except (ValueError, OSError, ResearchKBError) as error:
        fail(error)


@setup_app.command("preview")
def preview(ctx: typer.Context, input: Annotated[Path, typer.Option("--input")]) -> None:
    """Show the concrete setup diff without applying it."""
    _run(ctx, input, apply=False)


@setup_app.command("apply")
def apply(ctx: typer.Context, input: Annotated[Path, typer.Option("--input")]) -> None:
    """Apply the initial choices the user accepted during workspace initialization."""
    _run(ctx, input, apply=True)
