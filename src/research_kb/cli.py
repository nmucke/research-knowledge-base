"""Command-line entry point for the research knowledge base."""

from typing import Annotated

import typer
from rich.console import Console

from research_kb import __version__
from research_kb.config import Settings
from research_kb.logging_config import configure_logging

app = typer.Typer(
    name="research",
    help="Manage the local Zotero-Obsidian literature workflow.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
console = Console()
error_console = Console(stderr=True)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"research {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Show detailed console diagnostics."),
    ] = False,
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the installed version and exit.",
        ),
    ] = None,
) -> None:
    """Initialize settings and command logging."""
    del version
    settings = Settings()
    logger = configure_logging(settings, verbose=verbose)
    logger.info("command_start command=%s", ctx.invoked_subcommand or "research")
    ctx.ensure_object(dict)
    ctx.obj["settings"] = settings
    ctx.obj["verbose"] = verbose


@app.command()
def doctor() -> None:
    """Check required services and local configuration (implementation step 2)."""
    error_console.print(
        "[yellow]research doctor is registered but not implemented yet; "
        "it is implementation step 2.[/yellow]"
    )
    raise typer.Exit(code=2)


if __name__ == "__main__":  # pragma: no cover
    app()
