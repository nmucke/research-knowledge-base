"""JSON research operations and a separate, user-facing approval interface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, cast

import typer
from pydantic import ValidationError as ModelValidationError
from pydantic_core import to_jsonable_python

from research_kb.agent_api import PreviewRequest, ResearchAPI
from research_kb.catalog_operations import CatalogApprovalService
from research_kb.config import Settings
from research_kb.exceptions import ResearchKBError
from research_kb.models import ApprovedCurationRequest, CurationDecision
from research_kb.research_operations import CurationApprovalService

api_app = typer.Typer(help="Schema-driven JSON operations shared with research MCP.")
curation_app = typer.Typer(help="User-only curation decisions, history and undo.")
catalog_app = typer.Typer(help="User-only project and tag taxonomy decisions.")


def emit(value: object) -> None:
    typer.echo(json.dumps(to_jsonable_python(value), ensure_ascii=False, indent=2))


def fail(error: Exception) -> None:
    if isinstance(error, ModelValidationError):
        # Do not echo raw input payloads into error logs.
        detail: object = error.errors(include_input=False, include_url=False, include_context=False)
    else:
        detail = str(error)
    typer.echo(json.dumps({"error": detail}), err=True)
    raise typer.Exit(code=1) from None


def api_for(ctx: typer.Context) -> ResearchAPI:
    return ResearchAPI(cast(Settings, ctx.obj["settings"]))


@api_app.command("schema")
def schema(
    ctx: typer.Context,
    operation: Annotated[str | None, typer.Option("--operation")] = None,
) -> None:
    """Print operation input schemas; omit --operation to list all."""
    api = api_for(ctx)
    if operation is not None and operation not in api.registry:
        fail(ValueError(f"Unknown research operation: {operation}"))
    emit(
        {
            name: {
                "description": item.description,
                "input_schema": item.schema(),
                "read_only": item.read_only,
            }
            for name, item in api.registry.items()
            if operation is None or name == operation
        }
    )


def _command(operation: str) -> Any:
    def run(
        ctx: typer.Context,
        input_file: Annotated[
            Path | None, typer.Option("--input", help="JSON request file.")
        ] = None,
    ) -> None:
        try:
            payload = json.loads(input_file.read_text(encoding="utf-8")) if input_file else {}
            if not isinstance(payload, dict):
                raise ValueError("JSON request must be an object")
            emit(api_for(ctx).call(operation, payload))
        except (ResearchKBError, ValueError, OSError) as error:
            fail(error)

    run.__name__ = operation.replace("-", "_")
    run.__doc__ = f"Run the {operation} research operation with a JSON request."
    return run


# Each command dispatches through the same schema validation as MCP.
for _name in (
    "search",
    "paper",
    "projects",
    "text",
    "context",
    "submit-review",
    "propose-curation",
    "list-proposals",
    "preview-proposal",
    "history",
    "save-artifact",
    "catalog-propose",
    "catalog-preview",
):
    api_app.command(_name)(_command(_name))


@curation_app.command("list")
def list_proposals(ctx: typer.Context, status: str = "pending") -> None:
    """List stored proposals awaiting a user decision."""
    try:
        emit(api_for(ctx).call("list-proposals", {"status": status}))
    except (ResearchKBError, ValueError, OSError) as error:
        fail(error)


def decisions_for(accept: list[str], reject: list[str]) -> tuple[CurationDecision, ...]:
    return tuple(
        [CurationDecision(item_id=item, decision="accepted") for item in accept]
        + [CurationDecision(item_id=item, decision="rejected") for item in reject]
    )


@curation_app.command("preview")
def preview(
    ctx: typer.Context,
    proposal_id: str,
    accept: Annotated[list[str] | None, typer.Option("--accept")] = None,
    reject: Annotated[list[str] | None, typer.Option("--reject")] = None,
) -> None:
    """Read a proposal or preview the exact accepted/rejected item set."""
    try:
        emit(
            api_for(ctx).preview(
                PreviewRequest(
                    proposal_id=proposal_id, decisions=decisions_for(accept or [], reject or [])
                )
            )
        )
    except (ResearchKBError, ValueError, OSError) as error:
        fail(error)


@curation_app.command("decide")
def decide(
    ctx: typer.Context,
    proposal_id: str,
    accept: Annotated[list[str] | None, typer.Option("--accept")] = None,
    reject: Annotated[list[str] | None, typer.Option("--reject")] = None,
) -> None:
    """Apply the user's explicit decisions; every proposal item must be ruled on once.

    This command is outside the research MCP and should run only in a user-controlled terminal.
    """
    try:
        api = api_for(ctx)
        proposal = api.operations._load_proposal(proposal_id)
        decisions = decisions_for(accept or [], reject or [])
        # Constructing the explicit command is the user's approval; there is no blanket yes flag.
        emit(
            CurationApprovalService(api.operations).apply(
                ApprovedCurationRequest(
                    proposal_id=proposal_id,
                    decisions=decisions,
                    expected_revision=proposal.expected_revision,
                )
            )
        )
    except (ResearchKBError, ValueError, OSError) as error:
        fail(error)


@curation_app.command("history")
def history(ctx: typer.Context, citekey: str | None = None) -> None:
    """List durable operation receipts."""
    try:
        emit(api_for(ctx).call("history", {"citekey": citekey}))
    except (ResearchKBError, ValueError, OSError) as error:
        fail(error)


@curation_app.command("undo")
def undo(ctx: typer.Context, operation_id: str) -> None:
    """Undo a specific operation only if every affected file is still unchanged."""
    try:
        service = CurationApprovalService(api_for(ctx).operations)
        receipt = next(
            (item for item in service.history() if item.operation_id == operation_id), None
        )
        if receipt is None:
            raise ValueError("Unknown operation ID")
        emit(service.undo(operation_id, receipt.revisions))
    except (ResearchKBError, ValueError, OSError) as error:
        fail(error)


@catalog_app.command("preview")
def catalog_preview(ctx: typer.Context, proposal_id: str) -> None:
    """Show every file diff of a project or taxonomy proposal."""
    try:
        emit(api_for(ctx).catalog.preview(proposal_id))
    except (ResearchKBError, ValueError, OSError) as error:
        fail(error)


@catalog_app.command("decide")
def catalog_decide(ctx: typer.Context, proposal_id: str) -> None:
    """Apply this exact proposal, in a user-controlled terminal outside research MCP."""
    try:
        emit(CatalogApprovalService(api_for(ctx).catalog).apply(proposal_id))
    except (ResearchKBError, ValueError, OSError) as error:
        fail(error)


@catalog_app.command("reject")
def catalog_reject(ctx: typer.Context, proposal_id: str) -> None:
    """Reject this exact proposal without applying its project or taxonomy changes."""
    try:
        emit(CatalogApprovalService(api_for(ctx).catalog).reject(proposal_id))
    except (ResearchKBError, ValueError, OSError) as error:
        fail(error)


@catalog_app.command("undo")
def catalog_undo(ctx: typer.Context, operation_id: str) -> None:
    """Undo a catalog operation with revision checks on all affected files."""
    try:
        emit(api_for(ctx).catalog.transactions.undo(operation_id))
    except (ResearchKBError, ValueError, OSError) as error:
        fail(error)


@curation_app.command("recover")
def recover(ctx: typer.Context, operation_id: str | None = None) -> None:
    """List interrupted operations, or recover one explicit operation by ID."""
    try:
        transactions = api_for(ctx).catalog.transactions
        emit(
            {"pending": transactions.pending()}
            if operation_id is None
            else {"recovered": transactions.recover(operation_id)}
        )
    except (ResearchKBError, ValueError, OSError) as error:
        fail(error)
