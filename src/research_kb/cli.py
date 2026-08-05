"""Command-line entry point for the research knowledge base."""

import logging
from typing import Annotated, cast

import typer
from rich.console import Console

from research_kb import __version__
from research_kb.better_bibtex import BetterBibTeXClient
from research_kb.config import Settings
from research_kb.doctor_service import CheckStatus, DoctorService
from research_kb.exceptions import ResearchKBError
from research_kb.logging_config import LOGGER_NAME, configure_logging
from research_kb.markdown_store import MarkdownStore
from research_kb.sync_service import SyncAction, SyncReport, SyncService
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


@app.command()
def show(
    ctx: typer.Context,
    zotero_key: Annotated[
        str,
        typer.Option("--zotero-key", help="Zotero item key to display."),
    ],
) -> None:
    """Display the metadata and citation key for one Zotero item."""
    settings = cast(Settings, ctx.obj["settings"])
    logger = logging.getLogger(LOGGER_NAME)

    try:
        with (
            ZoteroClient(settings.zotero_local_api) as zotero_client,
            BetterBibTeXClient(settings.better_bibtex_rpc) as better_bibtex_client,
        ):
            item = zotero_client.get_item(
                zotero_key,
                library_type=settings.zotero_library_type,
                library_id=settings.zotero_library_id,
            )
            citation_key = better_bibtex_client.get_citation_key(
                item.key,
                library_id=settings.zotero_library_id,
            )
    except ResearchKBError as error:
        logger.error("show_failed zotero_key=%s error=%s", zotero_key, error)
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from None

    fields = (
        ("Title", item.title),
        ("Zotero key", item.key),
        ("Citation key", citation_key),
        ("Item type", item.item_type),
        ("Version", str(item.version)),
        ("Authors", ", ".join(author.display_name for author in item.authors)),
        ("Date", item.date),
        ("Publication", item.publication),
        ("DOI", item.doi),
        ("URL", item.url),
    )
    for label, value in fields:
        console.print(f"{label}: {value or '-'}", markup=False)


@app.command()
def sync(
    ctx: typer.Context,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report changes without writing them.")
    ] = False,
    full: Annotated[
        bool, typer.Option("--full", help="Ignore the saved incremental-sync cursor.")
    ] = False,
    item: Annotated[
        str | None, typer.Option("--item", help="Synchronize one Zotero item key.")
    ] = None,
    citekey: Annotated[
        str | None, typer.Option("--citekey", help="Synchronize the note with this citation key.")
    ] = None,
) -> None:
    """Synchronize Zotero metadata into paper notes."""
    logger = logging.getLogger(LOGGER_NAME)
    if item is not None and citekey is not None:
        _sync_error(logger, "--item and --citekey cannot be used together.")
    if full and (item is not None or citekey is not None):
        _sync_error(logger, "--full cannot be used with --item or --citekey.")

    settings = cast(Settings, ctx.obj["settings"])
    try:
        with (
            ZoteroClient(settings.zotero_local_api) as zotero_client,
            BetterBibTeXClient(settings.better_bibtex_rpc) as better_bibtex_client,
        ):
            report = SyncService(
                settings,
                zotero_client,
                better_bibtex_client,
                MarkdownStore(settings.papers_dir),
            ).run(dry_run=dry_run, full=full, item_key=item, citekey=citekey)
    except ResearchKBError as error:
        logger.error("sync_failed error=%s", error)
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from None

    _log_sync_report(logger, report)
    _print_sync_report(report)


def _sync_error(logger: logging.Logger, message: str) -> None:
    """Emit one consistent CLI error before constructing service clients."""
    logger.error("sync_failed error=%s", message)
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=1)


def _log_sync_report(logger: logging.Logger, report: SyncReport) -> None:
    """Record machine-searchable details without changing terminal output."""
    for item in sorted(report.items, key=lambda result: (result.zotero_key, result.citekey)):
        logger.info(
            "sync_item action=%s zotero_key=%s citekey=%s path=%s",
            item.action,
            item.zotero_key,
            item.citekey,
            item.path,
        )
    logger.info(
        "sync_complete mode=%s dry_run=%s previous_version=%s library_version=%s "
        "state_updated=%s",
        report.mode,
        report.dry_run,
        report.previous_version,
        report.library_version,
        report.state_updated,
    )


def _print_sync_report(report: SyncReport) -> None:
    """Render a stable, intentionally metadata-only sync summary."""
    labels = {
        SyncAction.CREATED: "CREATE",
        SyncAction.UPDATED: "UPDATE",
        SyncAction.RENAMED: "RENAME",
        SyncAction.UNCHANGED: "UNCHANGED",
        SyncAction.MISSING: "MISSING",
    }
    prefix = "DRY-RUN " if report.dry_run else ""
    for item in sorted(report.items, key=lambda result: (result.zotero_key, result.citekey)):
        detail = item.citekey
        if item.action is SyncAction.RENAMED and item.previous_citekey is not None:
            detail = f"{item.previous_citekey} -> {item.citekey}"
        typer.echo(f"{prefix}{labels[item.action]} {item.zotero_key} {detail}")

    fallback = "no"
    if report.fallback_reason is not None:
        fallback = f"yes ({report.fallback_reason})"
    version = "n/a" if report.library_version is None else str(report.library_version)
    typer.echo(
        f"Summary: mode={report.mode}, count={len(report.items)}, "
        f"version={version}, incremental-fallback={fallback}"
    )


if __name__ == "__main__":  # pragma: no cover
    app()
