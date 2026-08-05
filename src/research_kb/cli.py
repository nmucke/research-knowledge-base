"""Command-line entry point for the research knowledge base."""

import logging
from typing import Annotated, cast

import typer
from rich.console import Console

from research_kb import __version__
from research_kb.better_bibtex import BetterBibTeXClient
from research_kb.config import Settings
from research_kb.doctor_service import CheckStatus, DoctorService
from research_kb.logging_config import LOGGER_NAME, configure_logging
from research_kb.zotero_client import ZoteroClient

app = typer.Typer(
    name="research",
    help="Manage the local Zotero-Obsidian literature workflow.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
console = Console()
CHECK_LABELS = {
    "better_bibtex": "Better BibTeX",
    "references_bib": "references.bib",
    "vault_paths": "Vault paths",
    "write_authorization": "Write authorization",
}


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
def doctor(ctx: typer.Context) -> None:
    """Check required services and local configuration."""
    settings = cast(Settings, ctx.obj["settings"])
    with (
        ZoteroClient(settings.zotero_local_api) as zotero_client,
        BetterBibTeXClient(settings.better_bibtex_rpc) as better_bibtex_client,
    ):
        report = DoctorService(settings, zotero_client, better_bibtex_client).run()

    styles = {
        CheckStatus.PASS: "green",
        CheckStatus.WARN: "yellow",
        CheckStatus.FAIL: "red",
    }
    logger = logging.getLogger(LOGGER_NAME)
    for check in report.checks:
        logger.info(
            "doctor_check name=%s status=%s message=%s",
            check.name,
            check.status,
            check.message,
        )
        label = CHECK_LABELS.get(check.name, check.name.replace("_", " ").title())
        console.print(
            f"[{styles[check.status]}]{check.status.upper():<4}[/] {label}: {check.message}"
        )

    if not report.ok:
        raise typer.Exit(code=1)


if __name__ == "__main__":  # pragma: no cover
    app()
