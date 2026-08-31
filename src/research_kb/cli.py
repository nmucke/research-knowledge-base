"""Command-line entry point for the research knowledge base."""

import logging
from pathlib import Path
from typing import Annotated, NoReturn, cast

import typer
from rich.console import Console

from research_kb import __version__
from research_kb.better_bibtex import BetterBibTeXClient
from research_kb.config import ZOTERO_WEB_API_URL, Settings
from research_kb.credential_store import CredentialStore
from research_kb.doctor_service import CheckStatus, DoctorService
from research_kb.exceptions import (
    ResearchKBError,
    ZoteroAuthorizationError,
    ZoteroLocalWriteUnsupportedError,
)
from research_kb.extraction_service import ExtractionResult, ExtractionService, ReviewContext
from research_kb.logging_config import LOGGER_NAME, configure_logging
from research_kb.markdown_store import MarkdownStore
from research_kb.models import TagPushPlan, TagPushReport
from research_kb.project_service import ProjectService
from research_kb.sync_service import SyncAction, SyncReport, SyncService
from research_kb.tag_service import TagService
from research_kb.validation_service import ValidationReport, ValidationService
from research_kb.zotero_client import ZoteroClient

app = typer.Typer(
    name="research",
    help="Manage the local Zotero-Obsidian literature workflow.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
projects_app = typer.Typer(
    name="projects",
    help="Inspect project notes and their derived paper links.",
    no_args_is_help=True,
)
app.add_typer(projects_app)
console = Console()
CHECK_LABELS = {
    "better_bibtex": "Better BibTeX",
    "references_bib": "references.bib",
    "vault_paths": "Vault paths",
    "write_authorization": "Write authorization",
}
_AUTHORIZATION_APP_NAME = "Research Literature Manager"


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


@app.command()
def extract(
    ctx: typer.Context,
    citekey: Annotated[str, typer.Argument(help="Citation key of the paper to extract.")],
    force: Annotated[
        bool, typer.Option("--force", help="Regenerate text even when the cache is current.")
    ] = False,
) -> None:
    """Extract one paper's PDF into a page-aware Markdown cache."""
    settings = cast(Settings, ctx.obj["settings"])
    logger = logging.getLogger(LOGGER_NAME)
    try:
        with ZoteroClient(settings.zotero_local_api) as zotero_client:
            result = ExtractionService(
                settings,
                zotero_client,
                MarkdownStore(settings.papers_dir),
            ).extract(citekey, force=force)
    except ResearchKBError as error:
        logger.error("extract_failed citekey=%s error=%s", citekey, error)
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from None

    _log_extraction_result(logger, result)
    _print_extraction_result(result)


@app.command("review-context")
def review_context(
    ctx: typer.Context,
    citekey: Annotated[str, typer.Argument(help="Citation key of the paper to review.")],
) -> None:
    """Ensure text is extracted and print the four files needed for review."""
    settings = cast(Settings, ctx.obj["settings"])
    logger = logging.getLogger(LOGGER_NAME)
    try:
        markdown_store = MarkdownStore(settings.papers_dir)
        with ZoteroClient(settings.zotero_local_api) as zotero_client:
            context = ExtractionService(
                settings,
                zotero_client,
                markdown_store,
            ).review_context(citekey)
        snapshot = ValidationService(settings, markdown_store).capture_workflow_snapshot(citekey)
    except ResearchKBError as error:
        logger.error("review_context_failed citekey=%s error=%s", citekey, error)
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from None

    logger.info(
        "review_context_ready citekey=%s extracted_paper=%s snapshot=%s",
        citekey,
        context.extracted_paper,
        snapshot,
    )
    _print_review_context(context, settings.vault_path)


@app.command()
def validate(
    ctx: typer.Context,
    citekey: Annotated[
        str | None,
        typer.Argument(help="Optional citation key; omit it to validate the whole vault."),
    ] = None,
) -> None:
    """Validate one paper note or the complete literature vault."""
    settings = cast(Settings, ctx.obj["settings"])
    logger = logging.getLogger(LOGGER_NAME)
    try:
        service = ValidationService(
            settings,
            MarkdownStore(settings.papers_dir),
        )
        report = service.run(citekey)
        if citekey is not None and not report.errors:
            service.consume_workflow_snapshot(citekey)
    except ResearchKBError as error:
        logger.error("validate_failed citekey=%s error=%s", citekey, error)
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from None

    _log_validation_report(logger, report)
    _print_validation_report(report)
    if report.errors:
        raise typer.Exit(code=1)


@app.command()
def authorize(ctx: typer.Context) -> None:
    """Request and locally store a Zotero key for controlled tag writes."""
    settings = cast(Settings, ctx.obj["settings"])
    logger = logging.getLogger(LOGGER_NAME)
    try:
        try:
            with ZoteroClient(settings.zotero_local_api) as zotero_client:
                server_id, key = zotero_client.authorize(_AUTHORIZATION_APP_NAME)
        except ZoteroLocalWriteUnsupportedError:
            _verify_web_write_access(settings)
            logger.info("authorize_complete mode=web-api")
            typer.echo("Zotero Web API write access verified; no local credential was stored.")
            return
        CredentialStore(settings.credentials_path).save(server_id, key)
    except ResearchKBError as error:
        logger.error("authorize_failed error=%s", error)
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from None

    logger.info("authorize_complete server_id=%s", server_id)
    typer.echo("Authorization saved for this local Zotero instance.")


@app.command()
def tags(
    ctx: typer.Context,
    citekey: Annotated[str, typer.Argument(help="Citation key of the paper to compare.")],
) -> None:
    """Show the deterministic, add-only tag state for one paper note."""
    settings = cast(Settings, ctx.obj["settings"])
    logger = logging.getLogger(LOGGER_NAME)
    try:
        store = MarkdownStore(settings.papers_dir)
        with ZoteroClient(settings.zotero_local_api) as zotero_client:
            server = zotero_client.discover()
            plan = TagService(
                settings, store, zotero_client, server_id=server.server_id
            ).plan(store.note_path(citekey))
    except (ResearchKBError, ValueError) as error:
        logger.error("tags_failed citekey=%s error=%s", citekey, error)
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from None

    _print_tag_plan(plan)


@app.command("push-tags")
def push_tags(
    ctx: typer.Context,
    citekey: str = typer.Argument("", help="Citation key of the paper to push."),
    all_notes: bool = typer.Option(
        False, "--all", help="Preview every paper note (requires --dry-run)."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report additions without writing Zotero."
    ),
) -> None:
    """Push approved tags to Zotero, preserving every existing Zotero tag."""
    logger = logging.getLogger(LOGGER_NAME)
    if not citekey and not all_notes:
        _tag_error(logger, "Provide exactly one citekey or --all.")
    if citekey and all_notes:
        _tag_error(logger, "Provide exactly one citekey or --all.")
    if all_notes and not dry_run:
        _tag_error(logger, "--all is only supported with --dry-run for safety.")

    settings = cast(Settings, ctx.obj["settings"])
    store = MarkdownStore(settings.papers_dir)
    paths = (
        tuple(sorted(settings.papers_dir.glob("*.md")))
        if all_notes
        else (store.note_path(citekey),)
    )
    try:
        if dry_run:
            with ZoteroClient(settings.zotero_local_api) as zotero_client:
                server = zotero_client.discover()
                service = TagService(
                    settings, store, zotero_client, server_id=server.server_id
                )
                reports = tuple(service.push(path, dry_run=True) for path in paths)
        else:
            reports = (_push_one_with_authorization(settings, store, paths[0]),)
    except (ResearchKBError, ValueError) as error:
        logger.error("push_tags_failed error=%s", error)
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from None

    for report in reports:
        _log_tag_report(logger, report)
        _print_tag_report(report)


@projects_app.command("list")
def projects_list(ctx: typer.Context) -> None:
    """List every project note and how many papers are linked to it."""
    service, logger = _project_service(ctx)
    try:
        projects = service.projects()
        papers = service.papers()
    except ResearchKBError as error:
        _projects_error(logger, error)
    if not projects:
        typer.echo("No project notes exist yet.")
        return
    for project in projects:
        linked = sum(1 for _path, note in papers if project.project_id in note.projects)
        typer.echo(f"{project.project_id}  [{project.status}]  {linked} paper(s)  {project.title}")


@projects_app.command("index")
def projects_index(
    ctx: typer.Context,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report stale links without writing.")
    ] = False,
) -> None:
    """Regenerate the derived project links in paper and project notes."""
    service, logger = _project_service(ctx)
    try:
        report = service.index(dry_run=dry_run)
    except ResearchKBError as error:
        _projects_error(logger, error)
    logger.info(
        "projects_index dry_run=%s changed=%d unknown=%d skipped=%d",
        dry_run,
        len(report.changed),
        len(report.unknown),
        len(report.skipped),
    )
    verb = "would update" if dry_run else "updated"
    typer.echo(f"{verb} {len(report.changed)} note(s).")
    for path in report.changed:
        typer.echo(f"  {path.name}")
    for citekey, project_id in report.unknown:
        typer.echo(f"Warning: {citekey} references unknown project {project_id!r}.", err=True)
    for path in report.skipped:
        typer.echo(
            f"Warning: skipped unreadable note {path.name}; its links are not indexed.",
            err=True,
        )


@projects_app.command("candidates")
def projects_candidates(
    ctx: typer.Context,
    project_id: Annotated[str, typer.Argument(help="Identifier of the project note.")],
    limit: Annotated[int, typer.Option(help="Maximum candidates to print.")] = 20,
) -> None:
    """Rank unlinked papers by controlled-tag overlap with a project."""
    service, logger = _project_service(ctx)
    try:
        candidates = service.candidates(project_id, limit=limit)
    except (ResearchKBError, ValueError) as error:
        _projects_error(logger, error)
    for candidate in candidates:
        shared = ", ".join(candidate.shared_tags) or "no shared tags"
        relevance = candidate.ai_relevance if candidate.ai_relevance is not None else "-"
        typer.echo(f"{candidate.citekey}  (relevance {relevance}; {shared})  {candidate.title}")


def _projects_error(logger: logging.Logger, error: Exception) -> NoReturn:
    """Report a projects failure as a readable CLI error."""
    logger.error("projects_failed error=%s", error)
    typer.echo(f"Error: {error}", err=True)
    raise typer.Exit(code=1)


def _project_service(ctx: typer.Context) -> tuple[ProjectService, logging.Logger]:
    settings = cast(Settings, ctx.obj["settings"])
    return (
        ProjectService(settings, MarkdownStore(settings.papers_dir)),
        logging.getLogger(LOGGER_NAME),
    )


def _push_one_with_authorization(
    settings: Settings, store: MarkdownStore, path: Path
) -> TagPushReport:
    """Push once with a stored key, then authorize and retry exactly once on 401/403."""
    credentials = CredentialStore(settings.credentials_path)
    with ZoteroClient(settings.zotero_local_api) as zotero_client:
        server = zotero_client.discover()
        if server.server_id is None:
            return _push_one_with_web_api(settings, store, path)
        key = credentials.get(server.server_id)
        if key is None:
            if settings.web_write_configured:
                return _push_one_with_web_api(settings, store, path)
            raise ZoteroAuthorizationError(
                "No local Zotero authorization is stored; run `uv run research authorize` first."
            )
        service = TagService(settings, store, zotero_client, server_id=server.server_id)
        try:
            return service.push(path, api_key=key)
        except ZoteroLocalWriteUnsupportedError:
            return _push_one_with_web_api(settings, store, path)
        except ZoteroAuthorizationError:
            try:
                server_id, replacement_key = zotero_client.authorize(
                    _AUTHORIZATION_APP_NAME
                )
            except ZoteroLocalWriteUnsupportedError:
                return _push_one_with_web_api(settings, store, path)
            if server_id != server.server_id:
                raise ZoteroAuthorizationError(
                    "The Zotero server changed during authorization; no tags were written."
                ) from None
            credentials.save(server_id, replacement_key)
            return TagService(
                settings, store, zotero_client, server_id=server_id
            ).push(path, api_key=replacement_key)


def _push_one_with_web_api(
    settings: Settings, store: MarkdownStore, path: Path
) -> TagPushReport:
    """Use the explicit Web API fallback when local writes are unavailable."""
    web_settings, api_key = _web_write_settings(settings)
    with ZoteroClient(ZOTERO_WEB_API_URL, api_key=api_key) as web_client:
        web_client.verify_web_api_key(
            library_type=web_settings.zotero_library_type,
            library_id=web_settings.zotero_library_id,
        )
        return TagService(
            web_settings,
            store,
            web_client,
            enforce_server_identity=False,
        ).push(path, api_key=api_key)


def _verify_web_write_access(settings: Settings) -> None:
    """Verify configured Web API fallback credentials without displaying them."""
    web_settings, api_key = _web_write_settings(settings)
    with ZoteroClient(ZOTERO_WEB_API_URL, api_key=api_key) as web_client:
        web_client.verify_web_api_key(
            library_type=web_settings.zotero_library_type,
            library_id=web_settings.zotero_library_id,
        )


def _web_write_settings(settings: Settings) -> tuple[Settings, str]:
    """Return settings targeted at the configured Web library or explain setup."""
    if not settings.web_write_configured:
        raise ZoteroLocalWriteUnsupportedError(
            "This Zotero build does not support local writes. Configure "
            "ZOTERO_WEB_API_KEY and ZOTERO_WEB_LIBRARY_ID in .env for the Web API "
            "fallback, then run `uv run research authorize`."
        )
    assert settings.zotero_web_api_key is not None
    assert settings.zotero_web_library_id is not None
    return (
        settings.model_copy(update={"zotero_library_id": settings.zotero_web_library_id}),
        settings.zotero_web_api_key,
    )


def _tag_error(logger: logging.Logger, message: str) -> None:
    """Emit a usage error before opening a client or reading credentials."""
    logger.error("push_tags_failed error=%s", message)
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=1)


def _print_tag_plan(plan: TagPushPlan) -> None:
    """Render an auditable, key-free tag plan in a fixed field order."""
    typer.echo(f"Citation key: {plan.citekey}")
    typer.echo(f"Zotero key: {plan.zotero_key}")
    typer.echo(f"Existing Zotero tags: {_format_tags(plan.existing_zotero_tags)}")
    typer.echo(f"Approved tags: {_format_tags(plan.approved_curated_tags)}")
    typer.echo(f"AI applied tags: {_format_tags(plan.ai_applied_tags)}")
    typer.echo(f"Suggested tags (not pushed): {_format_tags(plan.suggested_tags)}")
    typer.echo(f"Pending additions: {_format_tags(plan.pending_tags)}")


def _print_tag_report(report: TagPushReport) -> None:
    """Render the plan and outcome without exposing authorization material."""
    _print_tag_plan(report.plan)
    if report.dry_run:
        outcome = (
            f"would add {_format_tags(report.plan.pending_tags)}"
            if report.plan.pending_tags
            else "no additions required"
        )
        typer.echo(f"DRY-RUN: {outcome}")
    elif report.pushed:
        typer.echo(
            f"PUSH: added {_format_tags(report.plan.pending_tags)} "
            f"(attempts={report.attempts})"
        )
    else:
        typer.echo("PUSH: already synchronized (no Zotero write required)")


def _format_tags(tags: tuple[str, ...]) -> str:
    return ", ".join(tags) if tags else "none"


def _log_tag_report(logger: logging.Logger, report: TagPushReport) -> None:
    """Log identities and additions without authorization material."""
    logger.info(
        "tag_reconcile_complete citekey=%s zotero_key=%s dry_run=%s pushed=%s "
        "attempts=%s tags_added=%s note_updated=%s",
        report.plan.citekey,
        report.plan.zotero_key,
        report.dry_run,
        report.pushed,
        report.attempts,
        ",".join(report.plan.pending_tags) or "none",
        report.note_updated,
    )


def _sync_error(logger: logging.Logger, message: str) -> None:
    """Emit one consistent CLI error before constructing service clients."""
    logger.error("sync_failed error=%s", message)
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=1)


def _log_extraction_result(logger: logging.Logger, result: ExtractionResult) -> None:
    """Record cache and quality details in a machine-searchable form."""
    diagnostics = result.diagnostics
    logger.info(
        "extract_complete citekey=%s status=%s cache_hit=%s pages=%s total_characters=%s "
        "empty_pages=%s low_text_fraction=%.3f output=%s",
        result.citekey,
        result.status,
        result.cache_hit,
        diagnostics.pages,
        diagnostics.total_characters,
        len(diagnostics.empty_pages),
        diagnostics.low_text_fraction,
        result.output_path,
    )


def _print_extraction_result(result: ExtractionResult) -> None:
    """Render all extraction-quality measures without leaking paper contents."""
    diagnostics = result.diagnostics
    action = "CACHED" if result.cache_hit else "EXTRACTED"
    typer.echo(f"{action} {result.citekey} -> {result.output_path}")
    typer.echo(f"Status: {result.status}")
    typer.echo(f"Pages: {diagnostics.pages}")
    per_page = ", ".join(
        f"{page}:{characters}"
        for page, characters in enumerate(diagnostics.characters_per_page, 1)
    )
    typer.echo(f"Characters per page: {per_page or 'none'}")
    empty = ", ".join(str(page) for page in diagnostics.empty_pages)
    typer.echo(f"Empty pages: {empty or 'none'}")
    typer.echo(f"Total characters: {diagnostics.total_characters}")
    typer.echo(f"Low-text fraction: {diagnostics.low_text_fraction:.1%}")
    for warning in diagnostics.warnings:
        typer.echo(f"Warning: {warning}")


def _print_review_context(context: ReviewContext, vault_path: Path) -> None:
    """Print stable vault-relative paths when possible."""
    fields = (
        ("Paper note", context.paper_note),
        ("Extracted paper", context.extracted_paper),
        ("Reading profile", context.reading_profile),
        ("Tag registry", context.tag_registry),
    )
    for index, (label, path) in enumerate(fields):
        if index:
            typer.echo()
        typer.echo(f"{label}:")
        typer.echo(str(_vault_relative(path, vault_path)))
    typer.echo()
    typer.echo("Active projects:")
    if not context.active_projects:
        typer.echo("(none)")
    for path in context.active_projects:
        typer.echo(str(_vault_relative(path, vault_path)))


def _vault_relative(path: Path, vault_path: Path) -> Path:
    """Prefer a stable vault-relative path when the file lives in the vault."""
    try:
        return path.relative_to(vault_path)
    except ValueError:
        return path


def _log_validation_report(logger: logging.Logger, report: ValidationReport) -> None:
    """Record every validation result in stable order for troubleshooting."""
    for issue in sorted(
        report.issues,
        key=lambda item: (item.severity.value, str(item.path), item.code, item.message),
    ):
        logger.info(
            "validation_issue severity=%s path=%s code=%s message=%s",
            issue.severity.value,
            issue.path,
            issue.code,
            issue.message,
        )
    logger.info(
        "validation_complete checked=%s errors=%s warnings=%s ok=%s",
        report.checked_count,
        len(report.errors),
        len(report.warnings),
        report.ok,
    )


def _print_validation_report(report: ValidationReport) -> None:
    """Render deterministic single-line issues and a compact summary."""
    for issue in sorted(
        report.issues,
        key=lambda item: (item.severity.value, str(item.path), item.code, item.message),
    ):
        label = "ERROR" if issue.severity.value == "error" else "WARN"
        typer.echo(f"{label} {issue.path}: [{issue.code}] {issue.message}")
    typer.echo(
        f"Summary: checked={report.checked_count}, errors={len(report.errors)}, "
        f"warnings={len(report.warnings)}"
    )


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
