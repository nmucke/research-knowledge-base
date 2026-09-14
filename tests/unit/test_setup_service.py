from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from research_kb.catalog_operations import ProjectCreateRequest
from research_kb.config import Settings
from research_kb.markdown_store import MarkdownStore
from research_kb.models import PaperNote, TagRegistryEntry
from research_kb.project_registry import parse_project_note
from research_kb.setup_service import ReadingProfile, SetupLibrary, SetupRequest, SetupService
from research_kb.tag_registry import parse_tag_registry
from research_kb.validation_service import ValidationService
from research_kb.workspace_service import initialize_workspace


def _service(tmp_path: Path) -> tuple[Path, SetupService]:
    workspace = tmp_path / "workspace"
    initialize_workspace(workspace)
    return workspace, SetupService(Settings.for_workspace(workspace))


def _request(service: SetupService, **updates: object) -> SetupRequest:
    values: dict[str, object] = {
        "expected_revision": service.status()["revision"],
        "reading_profile": ReadingProfile(
            primary_interests="Reliable weather foundation models",
            valuable_papers="Strong physical evaluation and reproducible evidence",
            lower_priority_papers="Benchmarks without physical diagnostics",
            recommendation_policy="Prefer papers that compare against numerical baselines",
        ),
        "tags": (
            TagRegistryEntry(
                name="domain/weather", definition="Research on weather prediction systems."
            ),
        ),
        "projects": (
            ProjectCreateRequest(
                project_id="weather-models",
                title="Weather models",
                description="Study reliable learned weather models.",
                goals=("Compare physical fidelity",),
                scope_in=("Forecast models",),
                scope_out=("Operational deployment",),
                tags=("domain/weather",),
                rationale="The user selected this initial project.",
            ),
        ),
        "library": SetupLibrary(library_type="user", library_id=0),
    }
    values.update(updates)
    return SetupRequest.model_validate(values)


def test_setup_applies_profile_tags_project_and_validates(tmp_path: Path) -> None:
    workspace, service = _service(tmp_path)
    paper_store = MarkdownStore(service.settings.papers_dir)
    paper = paper_store.create(
        PaperNote(zotero_key="ABCD1234", citekey="private2026", title="Private paper")
    )
    paper.write_text(
        paper.read_text(encoding="utf-8").replace(
            "## Human notes\n", "## Human notes\n\nPrivate human note.\n"
        ),
        encoding="utf-8",
    )
    paper_before = paper.read_bytes()
    request = _request(service)
    preview = service.preview(request)
    assert preview["revision"] == request.expected_revision
    files = preview["files"]
    assert isinstance(files, tuple)
    assert {item.path for item in files} >= {
        "vault/System/reading-profile.md",
        "vault/System/tag-registry.md",
        "vault/Projects/weather-models.md",
        ".research/setup.json",
    }

    result = service.apply(request)
    assert result["completed"] is True
    assert result["files"] == preview["files"]
    assert service.status()["completed"] is True
    assert "Reliable weather" in service.settings.reading_profile_path.read_text(encoding="utf-8")
    assert "domain/weather" in parse_tag_registry(service.settings.tag_registry_path).names
    registry_text = service.settings.tag_registry_path.read_text(encoding="utf-8")
    domain_section = registry_text.split("## Domain\n", 1)[1].split("## Method\n", 1)[0]
    assert "### `domain/weather`" in domain_section
    project = parse_project_note(service.settings.projects_dir / "weather-models.md")
    assert project.tags == ("domain/weather",)
    assert ValidationService(service.settings, MarkdownStore(service.settings.papers_dir)).run().ok
    assert paper.read_bytes() == paper_before
    assert workspace.joinpath(".research/setup.json").is_file()


def test_exact_replay_is_idempotent_but_new_setup_is_refused(tmp_path: Path) -> None:
    _workspace, service = _service(tmp_path)
    request = _request(service)
    service.apply(request)
    assert service.apply(request) == {"completed": True, "already_applied": True}
    assert request.reading_profile is not None
    changed = request.model_copy(
        update={
            "reading_profile": request.reading_profile.model_copy(
                update={"primary_interests": "Different interests"}
            )
        }
    )
    with pytest.raises(ValueError, match="already complete"):
        service.apply(changed)


def test_stale_revision_and_custom_profile_fail_without_writes(tmp_path: Path) -> None:
    _workspace, service = _service(tmp_path)
    request = _request(service)
    service.settings.tag_registry_path.write_text(
        service.settings.tag_registry_path.read_text(encoding="utf-8") + "\nHuman edit.\n",
        encoding="utf-8",
    )
    before = service.settings.tag_registry_path.read_bytes()
    with pytest.raises(ValueError, match="inputs changed"):
        service.apply(request)
    assert service.settings.tag_registry_path.read_bytes() == before
    assert not service.marker.exists()

    profile = service.settings.reading_profile_path
    profile.write_text("# Reading profile\n\nMy custom profile.\n", encoding="utf-8")
    fresh = _request(service)
    profile_before = profile.read_bytes()
    with pytest.raises(ValueError, match="already customized"):
        service.apply(fresh)
    assert profile.read_bytes() == profile_before
    assert not service.marker.exists()


def test_invalid_taxonomy_and_unknown_project_tag_leave_workspace_unchanged(
    tmp_path: Path,
) -> None:
    _workspace, service = _service(tmp_path)
    with pytest.raises(PydanticValidationError, match="supported namespace"):
        _request(
            service,
            tags=(TagRegistryEntry(name="unknown/bad", definition="Invalid."),),
            projects=(),
        )

    request = _request(
        service,
        tags=(),
        projects=(_request(service).projects[0].model_copy(update={"tags": ("domain/missing",)}),),
        reading_profile=None,
        library=None,
    )
    before = {
        path: path.read_bytes()
        for path in service.settings.obsidian_vault_path.rglob("*")
        if path.is_file()
    }
    with pytest.raises(ValueError, match="unknown tags"):
        service.apply(request)
    assert all(path.read_bytes() == content for path, content in before.items())
    assert not service.marker.exists()


def test_existing_project_and_tag_definition_cannot_be_overwritten(tmp_path: Path) -> None:
    _workspace, service = _service(tmp_path)
    registry = service.settings.tag_registry_path
    registry.write_text(
        registry.read_text(encoding="utf-8")
        + "\n### `domain/weather`\n\nExisting human definition.\n",
        encoding="utf-8",
    )
    request = _request(service, projects=(), reading_profile=None, library=None)
    before = registry.read_bytes()
    with pytest.raises(ValueError, match="cannot redefine"):
        service.apply(request)
    assert registry.read_bytes() == before

    existing = service.settings.projects_dir / "weather-models.md"
    existing.write_text(
        "---\nschema_version: 1\ntype: project\nproject_id: weather-models\n"
        "title: Existing project\nstatus: active\nstarted:\ntarget:\ntags: []\n---\n\n"
        "# Existing project\n\n## Description\n\nHuman-owned.\n",
        encoding="utf-8",
    )
    project_request = _request(service, tags=(), reading_profile=None, library=None)
    existing_before = existing.read_bytes()
    with pytest.raises(ValueError, match="cannot overwrite project"):
        service.apply(project_request)
    assert existing.read_bytes() == existing_before


def test_second_write_failure_rolls_back_entire_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace, service = _service(tmp_path)
    request = _request(service)
    before = {
        path: path.read_bytes()
        for path in service.settings.workspace_path.rglob("*")
        if path.is_file()
    }
    import research_kb.operation_transaction as transaction_module

    real_commit = transaction_module._commit
    calls = 0

    def fail_second(path: Path, content: str | None, expected: str | None) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected setup failure")
        real_commit(path, content, expected)

    monkeypatch.setattr(transaction_module, "_commit", fail_second)
    with pytest.raises(OSError, match="injected setup failure"):
        service.apply(request)
    assert all(path.read_bytes() == content for path, content in before.items())
    assert not service.marker.exists()
    assert not (service.settings.projects_dir / "weather-models.md").exists()


def test_existing_env_secrets_are_preserved_and_absent_from_preview(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    initialize_workspace(workspace)
    env = workspace / ".env"
    secret = "ZOTERO_WEB_API_KEY=top-secret-value"
    env.write_text(
        f"{secret}\nZOTERO_WEB_LIBRARY_ID=123\nZOTERO_LIBRARY_TYPE=user\nZOTERO_LIBRARY_ID=0\n",
        encoding="utf-8",
    )
    service = SetupService(Settings.for_workspace(workspace))
    request = _request(service, projects=(), tags=())
    preview = service.preview(request)
    assert secret not in repr(preview)
    service.apply(request)
    assert secret in env.read_text(encoding="utf-8")
