"""The installed workspace launcher supports agent onboarding without a global CLI."""

import json
import subprocess
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from research_kb.agent_api import ResearchAPI
from research_kb.config import Settings
from research_kb.workspace_service import initialize_workspace

ROOT = Path(__file__).parents[2]


def test_workspace_agent_can_preview_apply_and_resume_from_outside_checkout(tmp_path: Path) -> None:
    workspace = tmp_path / "My Research Library"
    initialize_workspace(workspace)
    launcher = workspace / "bin/research"

    def command(*args: str) -> dict[str, object]:
        result = subprocess.run(
            [str(launcher), "workspace", "setup", *args],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)  # type: ignore[no-any-return]

    status = command("status")
    assert status["completed"] is False
    assert "expected_revision" in command("schema")["properties"]  # type: ignore[operator]
    plan = workspace / "setup.json"
    plan.write_text(
        json.dumps(
            {
                "expected_revision": status["revision"],
                "reading_profile": {"primary_interests": "Weather forecast uncertainty."},
                "tags": [{"name": "domain/weather", "definition": "Research on weather."}],
                "projects": [
                    {
                        "project_id": "forecast-evaluation",
                        "title": "Forecast evaluation",
                        "description": "Compare weather forecasts.",
                        "goals": ["Compare calibration."],
                        "scope_in": ["Uncertainty estimates"],
                        "scope_out": [],
                        "tags": ["domain/weather"],
                        "rationale": "Initial project requested by the user.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert command("preview", "--input", str(plan))["files"]
    assert command("status")["completed"] is False
    assert command("apply", "--input", str(plan))["completed"] is True
    assert command("apply", "--input", str(plan))["already_applied"] is True
    completed = command("status")
    assert completed["completed"] is True
    assert completed["projects"] == [
        {"project_id": "forecast-evaluation", "title": "Forecast evaluation"}
    ]
    validation = subprocess.run(
        [str(launcher), "validate"], cwd=workspace, check=True, capture_output=True, text=True
    )
    assert "errors=0" in validation.stdout
    settings = Settings.for_workspace(workspace)
    setup_operations = {name for name in ResearchAPI(settings).registry if "setup" in name}
    assert setup_operations == {
        "setup-status",
        "setup-schema",
        "setup-preview",
        "setup-apply",
    }
    assert (workspace / ".agents/skills/workspace-initialization/SKILL.md").is_file()
    assert (workspace / ".claude/skills/workspace-initialization/SKILL.md").is_file()


def _setup_plan(revision: str) -> dict[str, object]:
    return {
        "expected_revision": revision,
        "reading_profile": {
            "primary_interests": "Reliable weather forecasting.",
            "recommendation_policy": "Prefer physically evaluated systems.",
        },
        "tags": [{"name": "domain/weather", "definition": "Weather research."}],
        "projects": [
            {
                "project_id": "weather-evaluation",
                "title": "Weather evaluation",
                "description": "Evaluate learned weather forecasts.",
                "goals": ["Compare physical fidelity."],
                "scope_in": ["Forecast evaluation"],
                "scope_out": ["Operational deployment"],
                "tags": ["domain/weather"],
                "rationale": "The user selected this initial project.",
            }
        ],
    }


async def _mcp_onboarding(workspace: Path) -> dict[str, Any]:
    server = StdioServerParameters(
        command=str(ROOT / ".venv/bin/research"),
        args=["--workspace", str(workspace), "mcp"],
        cwd=ROOT,
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            status = await session.call_tool("research_setup_status", {})
            schema = await session.call_tool("research_setup_schema", {})
            assert status.structuredContent is not None
            assert schema.structuredContent is not None
            plan = _setup_plan(str(status.structuredContent["revision"]))
            # A user selecting their personal library supplies no account ID.
            plan["library"] = {"library_type": "user"}
            preview = await session.call_tool("research_setup_preview", plan)
            applied = await session.call_tool("research_setup_apply", plan)
            completed = await session.call_tool("research_setup_status", {})
            validation = await session.call_tool("research_validate", {})
            replay = await session.call_tool("research_setup_apply", plan)
    return {
        "tools": tools,
        "status": status,
        "schema": schema,
        "preview": preview,
        "applied": applied,
        "completed": completed,
        "validation": validation,
        "replay": replay,
    }


def test_stdio_mcp_supports_complete_initial_setup_without_source_access(tmp_path: Path) -> None:
    workspace = tmp_path / "mcp-workspace"
    initialize_workspace(workspace)

    results = anyio.run(_mcp_onboarding, workspace)
    tools = results["tools"]
    onboarding = {
        "research_setup_status",
        "research_setup_schema",
        "research_setup_preview",
        "research_setup_apply",
        "research_doctor",
        "research_validate",
        "research_sync",
    }
    assert onboarding <= tools.keys()
    assert {"research_apply_curation", "research_undo"}.isdisjoint(tools)
    for name in {
        "research_setup_status",
        "research_setup_schema",
        "research_setup_preview",
        "research_doctor",
        "research_validate",
    }:
        assert tools[name].annotations is not None
        assert tools[name].annotations.readOnlyHint is True
    for name in {"research_setup_apply", "research_sync"}:
        assert tools[name].annotations is not None
        assert tools[name].annotations.readOnlyHint is False

    schema = results["schema"].structuredContent
    assert schema is not None
    assert "expected_revision" in schema["properties"]
    assert results["status"].structuredContent["completed"] is False
    assert results["preview"].structuredContent["files"]
    assert results["applied"].structuredContent["completed"] is True
    assert results["completed"].structuredContent["completed"] is True
    assert results["completed"].structuredContent["library"] == {
        "library_type": "user",
        "library_id": 0,
    }
    assert results["validation"].structuredContent["ok"] is True
    assert results["replay"].structuredContent == {
        "completed": True,
        "already_applied": True,
    }


async def _mcp_stale_plan(workspace: Path) -> tuple[Any, Any]:
    server = StdioServerParameters(
        command=str(ROOT / ".venv/bin/research"),
        args=["--workspace", str(workspace), "mcp"],
        cwd=ROOT,
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            status = await session.call_tool("research_setup_status", {})
            assert status.structuredContent is not None
            plan = _setup_plan(str(status.structuredContent["revision"]))
            registry = workspace / "vault/System/tag-registry.md"
            registry.write_text(
                registry.read_text(encoding="utf-8") + "\nExternal curated edit.\n",
                encoding="utf-8",
            )
            preview = await session.call_tool("research_setup_preview", plan)
            apply = await session.call_tool("research_setup_apply", plan)
    return preview, apply


def test_stdio_mcp_rejects_stale_setup_without_writing(tmp_path: Path) -> None:
    workspace = tmp_path / "stale-mcp-workspace"
    initialize_workspace(workspace)
    profile = workspace / "vault/System/reading-profile.md"
    profile_before = profile.read_bytes()

    preview, apply = anyio.run(_mcp_stale_plan, workspace)

    assert preview.isError is True
    assert apply.isError is True
    assert profile.read_bytes() == profile_before
    assert not (workspace / ".research/setup.json").exists()
    assert not (workspace / "vault/Projects/weather-evaluation.md").exists()
