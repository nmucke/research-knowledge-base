from pathlib import Path

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).parents[2]
PACKAGED = ROOT / "src" / "research_kb" / "assets"
RESEARCH_SKILLS = {
    "paper-review",
    "project-curation",
    "research-synthesis",
    "curation-approval",
    "literature-discovery",
    "workspace-initialization",
}


def _frontmatter(path: Path) -> dict[str, object]:
    _, raw, _ = path.read_text(encoding="utf-8").split("---", maxsplit=2)
    value = yaml.safe_load(raw)
    assert isinstance(value, dict)
    return value


def test_checkout_instructions_define_the_development_role() -> None:
    agents = ROOT / "AGENTS.md"
    claude = ROOT / "CLAUDE.md"
    assert agents.read_text(encoding="utf-8") == claude.read_text(encoding="utf-8")

    contract = claude.read_text(encoding="utf-8")
    assert "Development checkout" in contract
    assert "synthetic fixtures" in contract
    assert "personal vault" in contract
    assert "workflow-evaluation" in contract


def test_research_role_is_packaged_instead_of_checkout_discoverable() -> None:
    for client in (".claude", ".codex"):
        for skill in RESEARCH_SKILLS:
            assert not (ROOT / client / "skills" / skill / "SKILL.md").exists()

    contract = (PACKAGED / "agents" / "AGENTS.md").read_text(encoding="utf-8")
    assert "Research workspace" in contract
    assert "user-only CLI" in contract
    assert "approval boolean" in contract
    for field in (
        "human_read_status",
        "human_read_date",
        "human_rating",
        "human_priority",
        "human_relevance",
    ):
        assert field in contract


def test_packaged_research_skills_have_valid_discriminating_metadata() -> None:
    skill_root = PACKAGED / "skills"
    assert {path.name for path in skill_root.iterdir() if path.is_dir()} == RESEARCH_SKILLS

    descriptions: set[object] = set()
    for name in RESEARCH_SKILLS:
        metadata = _frontmatter(skill_root / name / "SKILL.md")
        assert metadata["name"] == name
        description = metadata["description"]
        assert isinstance(description, str) and "Use " in description
        descriptions.add(description)
    assert len(descriptions) == len(RESEARCH_SKILLS)


def test_research_skills_route_through_bounded_operations() -> None:
    skills = {
        name: (PACKAGED / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        for name in RESEARCH_SKILLS
    }
    assert "research_context" in skills["paper-review"]
    assert "research_submit_review" in skills["paper-review"]
    assert "abstract-only" in skills["paper-review"]
    assert "research_save_artifact" in skills["research-synthesis"]
    assert "research_search" in skills["literature-discovery"]
    assert "research_preview_proposal" in skills["curation-approval"]
    assert "research_propose_curation" in skills["project-curation"]
    assert "research_catalog_propose" in skills["project-curation"]
    assert "research_catalog_preview" in skills["curation-approval"]
    assert "research_setup_status" in skills["workspace-initialization"]
    assert "research_setup_preview" in skills["workspace-initialization"]
    assert "research_setup_apply" in skills["workspace-initialization"]

    combined = "\n".join(skills.values())
    assert "Never invoke `research curation decide`" in combined
    assert "approval boolean" in combined


def test_workspace_initialization_is_resumable_and_narrowly_authorized() -> None:
    skill = (
        PACKAGED / "skills" / "workspace-initialization" / "SKILL.md"
    ).read_text(encoding="utf-8")
    reference = (
        PACKAGED
        / "skills"
        / "workspace-initialization"
        / "references"
        / "setup-plan.md"
    ).read_text(encoding="utf-8")
    assert skill.index("research_setup_status") < skill.index("research_setup_preview")
    assert skill.index("research_setup_preview") < skill.index("research_setup_apply")
    for behavior in (
        "preserve configured content",
        "user accepts that concrete plan",
        "narrow initial-setup exception",
        "does not mean Zotero is connected",
    ):
        assert behavior in skill
    assert "Do not use this fallback in Cowork" in skill
    assert "./bin/research workspace setup status" in skill
    assert skill.index("./bin/research sync") < skill.index("./bin/research projects index")
    assert skill.index("./bin/research projects index") < skill.rindex("./bin/research validate")
    assert "personal or group Zotero library" in skill
    assert "one initial sync" in skill
    cowork = (
        PACKAGED
        / "skills"
        / "workspace-initialization"
        / "references"
        / "cowork-plugin.md"
    ).read_text(encoding="utf-8")
    for boundary in (
        "research-workspace.plugin.zip",
        "Open the **Cowork** tab",
        "setup-schema.json",
        "never invent `expected_revision`",
        "neither configures Cowork",
    ):
        assert boundary in cowork
    for field in (
        "expected_revision",
        "primary_interests",
        "valuable_papers",
        "lower_priority_papers",
        "recommendation_policy",
        "library_type",
        "library_id",
    ):
        assert field in reference


def test_workflow_evaluation_is_development_only_and_uses_synthetic_data() -> None:
    skill = ROOT / ".agents" / "skills" / "workflow-evaluation" / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    assert _frontmatter(skill)["name"] == "workflow-evaluation"
    assert "synthetic" in text
    assert "personal vault" in text
    assert "JSON CLI" in text and "MCP" in text
    assert not (PACKAGED / "skills" / "workflow-evaluation").exists()


def test_behavioral_scenarios_cover_routing_evidence_and_recovery() -> None:
    scenarios = (
        ROOT / ".agents" / "skills" / "workflow-evaluation" / "references" / "scenarios.md"
    ).read_text(encoding="utf-8")
    for behavior in (
        "abstract-only",
        "rejected",
        "stale revision",
        "imperative sentences",
        "review this Python module",
        "compare these papers",
    ):
        assert behavior in scenarios


def test_behavioral_fixture_routes_near_misses_and_keeps_application_user_only() -> None:
    fixture = yaml.safe_load(
        (ROOT / "tests" / "fixtures" / "agent_workflows.yaml").read_text(encoding="utf-8")
    )
    assert isinstance(fixture, list)
    routes = {case["request"]: case["skill"] for case in fixture}
    assert routes["Review this Python module for race conditions"] is None
    assert routes["Compare these three papers on causal transportability"] == "research-synthesis"

    research_cases = [case for case in fixture if case["skill"] is not None]
    assert {case["skill"] for case in research_cases} == RESEARCH_SKILLS
    ordinary_cases = [
        case for case in research_cases if case["skill"] != "workspace-initialization"
    ]
    assert all("curation-apply" in case.get("forbidden_operations", []) for case in ordinary_cases)
    setup_cases = [case for case in research_cases if case["skill"] == "workspace-initialization"]
    assert any(
        "research_setup_apply" in case.get("allowed_after_acceptance", [])
        for case in setup_cases
    )
