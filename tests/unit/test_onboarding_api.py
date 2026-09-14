from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

import research_kb.onboarding_api as onboarding_module
from research_kb.config import Settings
from research_kb.onboarding_api import EmptyRequest, OnboardingAPI
from research_kb.setup_service import SetupLibrary, SetupRequest
from research_kb.workspace_service import initialize_workspace


def _api(tmp_path: Path, **settings: object) -> OnboardingAPI:
    workspace = tmp_path / "workspace"
    initialize_workspace(workspace)
    return OnboardingAPI(Settings.for_workspace(workspace).model_copy(update=settings))


def test_empty_request_and_setup_schema_expose_no_host_control_inputs(tmp_path: Path) -> None:
    api = _api(tmp_path)

    assert EmptyRequest.model_json_schema()["properties"] == {}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EmptyRequest.model_validate({"path": "/tmp/outside", "command": "cat .env"})
    schema = api.schema(EmptyRequest())
    assert schema["required"] == ["expected_revision"]
    properties = cast(dict[str, object], schema["properties"])
    assert {"reading_profile", "tags", "projects", "library"} <= properties.keys()


def test_apply_updates_library_but_preserves_caller_endpoints(tmp_path: Path) -> None:
    api = _api(
        tmp_path,
        zotero_local_api="http://127.0.0.1:24567/custom-api",
        better_bibtex_rpc="http://127.0.0.1:24567/custom-bbt",
    )
    revision = str(api.status(EmptyRequest())["revision"])
    request = SetupRequest(
        expected_revision=revision,
        library=SetupLibrary(library_type="group", library_id=731),
    )

    assert api.preview(request)["files"]
    assert api.apply(request)["completed"] is True
    assert api.settings.zotero_library_type == "group"
    assert api.settings.zotero_library_id == 731
    assert api.settings.zotero_local_api == "http://127.0.0.1:24567/custom-api"
    assert api.settings.better_bibtex_rpc == "http://127.0.0.1:24567/custom-bbt"


@dataclass(frozen=True)
class _FakeReport:
    ok: bool = True
    checks: tuple[object, ...] = ("synthetic-check",)


class _FakeClient:
    created: list[tuple[type[_FakeClient], str]] = []

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint
        self.created.append((type(self), endpoint))

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class _FakeZotero(_FakeClient):
    pass


class _FakeBibTeX(_FakeClient):
    pass


def test_doctor_and_sync_use_accepted_library_in_persistent_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = _api(
        tmp_path,
        zotero_local_api="http://127.0.0.1:24568/api",
        better_bibtex_rpc="http://127.0.0.1:24568/bbt",
    )
    request = SetupRequest(
        expected_revision=str(api.status(EmptyRequest())["revision"]),
        library=SetupLibrary(library_type="group", library_id=991),
    )
    api.apply(request)
    seen: dict[str, Any] = {}
    _FakeClient.created.clear()

    class FakeDoctorService:
        def __init__(self, settings: Settings, zotero: object, bibtex: object) -> None:
            seen["doctor_settings"] = settings

        def run(self) -> _FakeReport:
            return _FakeReport()

    class FakeSyncReport:
        counts = {"created": 0}

    class FakeSyncService:
        def __init__(
            self, settings: Settings, zotero: object, bibtex: object, store: object
        ) -> None:
            seen["sync_settings"] = settings

        def run(self) -> FakeSyncReport:
            return FakeSyncReport()

    class FakeProjectService:
        def __init__(self, settings: Settings, store: object) -> None:
            seen["project_settings"] = settings

        def index(self) -> dict[str, object]:
            return {"changed": []}

    monkeypatch.setattr(onboarding_module, "ZoteroClient", _FakeZotero)
    monkeypatch.setattr(onboarding_module, "BetterBibTeXClient", _FakeBibTeX)
    monkeypatch.setattr(onboarding_module, "DoctorService", FakeDoctorService)
    monkeypatch.setattr(onboarding_module, "SyncService", FakeSyncService)
    monkeypatch.setattr(onboarding_module, "ProjectService", FakeProjectService)
    monkeypatch.setattr(
        api,
        "validate",
        lambda request: {"ok": True, "checked_count": 0, "issues": ()},
    )

    assert api.doctor(EmptyRequest())["ok"] is True
    sync = api.sync(EmptyRequest())
    assert sync["counts"] == {"created": 0}
    assert cast(dict[str, object], sync["validation"])["ok"] is True
    assert _FakeClient.created == [
        (_FakeZotero, "http://127.0.0.1:24568/api"),
        (_FakeBibTeX, "http://127.0.0.1:24568/bbt"),
        (_FakeZotero, "http://127.0.0.1:24568/api"),
        (_FakeBibTeX, "http://127.0.0.1:24568/bbt"),
    ]
    for key in ("doctor_settings", "sync_settings", "project_settings"):
        settings = seen[key]
        assert settings.zotero_library_type == "group"
        assert settings.zotero_library_id == 991
