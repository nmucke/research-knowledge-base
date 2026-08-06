"""CLI-level safety tests for tag planning, authorization, and pushes."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Self

import pytest
from typer.testing import CliRunner

import research_kb.cli as cli
from research_kb.config import Settings
from research_kb.credential_store import CredentialStore
from research_kb.exceptions import (
    ZoteroAuthorizationError,
    ZoteroLocalWriteUnsupportedError,
)
from research_kb.markdown_store import MarkdownStore
from research_kb.models import PaperNote, ZoteroItem, ZoteroTag
from research_kb.zotero_client import ZoteroServerInfo

runner = CliRunner()


class FakeZoteroClient:
    patch_calls: ClassVar[list[tuple[str, tuple[ZoteroTag, ...], int, str]]] = []
    authorize_calls: ClassVar[list[str]] = []
    reject_key_once: ClassVar[str | None] = None
    server_id: ClassVar[str | None] = "server-1"
    web_verifications: ClassVar[list[tuple[str, int]]] = []

    def __init__(self, base_url: str, *, api_key: str | None = None) -> None:
        self.is_web = base_url == "https://api.zotero.org"
        self.api_key = api_key

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    @classmethod
    def reset(cls) -> None:
        cls.patch_calls = []
        cls.authorize_calls = []
        cls.reject_key_once = None
        cls.server_id = "server-1"
        cls.web_verifications = []

    def discover(self) -> ZoteroServerInfo:
        return ZoteroServerInfo(3, type(self).server_id, 1)

    def authorize(self, app_name: str) -> tuple[str, str]:
        type(self).authorize_calls.append(app_name)
        if type(self).server_id is None:
            raise ZoteroLocalWriteUnsupportedError("unsupported")
        return "server-1", "replacement-secret"

    def verify_web_api_key(self, *, library_type: str, library_id: int) -> None:
        assert self.is_web
        assert self.api_key == "web-secret"
        type(self).web_verifications.append((library_type, library_id))

    def get_item(self, item_key: str, *, library_type: str, library_id: int) -> ZoteroItem:
        assert item_key == "ABCD1234"
        assert library_type == "user"
        assert library_id == (123 if self.is_web else 0)
        existing = ZoteroTag(tag="existing-automatic", type=1)
        return ZoteroItem(
            key=item_key,
            version=7,
            library_id=library_id,
            item_type="journalArticle",
            title="Tags",
            creators=(),
            tags=(existing.tag,),
            tag_entries=(existing,),
        )

    def patch_tags(
        self,
        item_key: str,
        tags: tuple[ZoteroTag, ...],
        version: int,
        api_key: str,
        *,
        library_type: str,
        library_id: int,
    ) -> int:
        type(self).patch_calls.append((item_key, tags, version, api_key))
        if type(self).reject_key_once == api_key:
            type(self).reject_key_once = None
            raise ZoteroAuthorizationError("expired")
        return 8


@pytest.fixture(autouse=True)
def _reset_client() -> None:
    FakeZoteroClient.reset()


def _vault(tmp_path: Path) -> tuple[Settings, MarkdownStore, Path]:
    (tmp_path / "System").mkdir()
    (tmp_path / "System" / "tag-registry.md").write_text(
        "### `domain/weather`\n\nWeather.\n", encoding="utf-8"
    )
    store = MarkdownStore(tmp_path / "Literature" / "Papers")
    path = store.create(
        PaperNote(
            zotero_key="ABCD1234",
            zotero_version=6,
            zotero_server_id="server-1",
            citekey="doe2026",
            title="Tags",
            tags=("domain/weather",),
            ai_applied_tags=("domain/weather",),
            ai_suggested_tags=("method/proposed",),
        )
    )
    return Settings(_env_file=None, research_vault_path=tmp_path), store, path


def _invoke(
    tmp_path: Path, arguments: list[str], *, extra_env: dict[str, str] | None = None
) -> object:
    environment = {"RESEARCH_VAULT_PATH": str(tmp_path)}
    environment.update(extra_env or {})
    return runner.invoke(
        cli.app,
        arguments,
        env=environment,
    )


def test_tags_displays_distinct_tag_states(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _vault(tmp_path)
    monkeypatch.setattr(cli, "ZoteroClient", FakeZoteroClient)

    result = _invoke(tmp_path, ["tags", "doe2026"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "Citation key: doe2026",
        "Zotero key: ABCD1234",
        "Existing Zotero tags: existing-automatic",
        "Approved tags: domain/weather",
        "AI applied tags: domain/weather",
        "Suggested tags (not pushed): method/proposed",
        "Pending additions: domain/weather",
    ]


def test_dry_run_needs_no_credentials_and_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settings, _store, path = _vault(tmp_path)
    before = path.read_bytes()
    monkeypatch.setattr(cli, "ZoteroClient", FakeZoteroClient)

    result = _invoke(tmp_path, ["push-tags", "doe2026", "--dry-run"])

    assert result.exit_code == 0
    assert "DRY-RUN: would add domain/weather" in result.stdout
    assert "Pending additions: domain/weather" in result.stdout
    assert FakeZoteroClient.patch_calls == []
    assert path.read_bytes() == before


def test_authorize_uses_documented_app_name_and_never_prints_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, _store, _path = _vault(tmp_path)
    monkeypatch.setattr(cli, "ZoteroClient", FakeZoteroClient)

    result = _invoke(tmp_path, ["authorize"])

    assert result.exit_code == 0
    assert FakeZoteroClient.authorize_calls == ["Research Literature Manager"]
    assert "replacement-secret" not in result.output
    assert CredentialStore(settings.credentials_path).get("server-1") == "replacement-secret"
    log = (settings.log_dir / "research.log").read_text(encoding="utf-8")
    assert "replacement-secret" not in log


def test_live_push_requires_matching_credentials_before_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _vault(tmp_path)
    monkeypatch.setattr(cli, "ZoteroClient", FakeZoteroClient)

    result = _invoke(tmp_path, ["push-tags", "doe2026"])

    assert result.exit_code == 1
    assert "uv run research authorize" in result.stderr
    assert FakeZoteroClient.patch_calls == []


def test_live_push_reauthorizes_once_without_exposing_either_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, store, path = _vault(tmp_path)
    CredentialStore(settings.credentials_path).save("server-1", "expired-secret")
    FakeZoteroClient.reject_key_once = "expired-secret"
    monkeypatch.setattr(cli, "ZoteroClient", FakeZoteroClient)

    result = _invoke(tmp_path, ["push-tags", "doe2026"])

    assert result.exit_code == 0
    assert [call[3] for call in FakeZoteroClient.patch_calls] == [
        "expired-secret",
        "replacement-secret",
    ]
    assert FakeZoteroClient.authorize_calls == ["Research Literature Manager"]
    assert "expired-secret" not in result.output
    assert "replacement-secret" not in result.output
    assert "PUSH: added domain/weather (attempts=1)" in result.stdout
    note = store.parse(path).note
    assert note.zotero_tags == ("existing-automatic", "domain/weather")
    assert note.zotero_tag_sync == "synced"
    assert note.zotero_version == 8
    log = (settings.log_dir / "research.log").read_text(encoding="utf-8")
    assert "expired-secret" not in log
    assert "replacement-secret" not in log


def test_web_api_fallback_handles_zotero_without_server_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settings, store, path = _vault(tmp_path)
    FakeZoteroClient.server_id = None
    monkeypatch.setattr(cli, "ZoteroClient", FakeZoteroClient)
    web_env = {
        "ZOTERO_WEB_API_KEY": "web-secret",
        "ZOTERO_WEB_LIBRARY_ID": "123",
    }

    authorized = _invoke(tmp_path, ["authorize"], extra_env=web_env)
    pushed = _invoke(tmp_path, ["push-tags", "doe2026"], extra_env=web_env)

    assert authorized.exit_code == 0
    assert "Web API write access verified" in authorized.stdout
    assert pushed.exit_code == 0
    assert FakeZoteroClient.web_verifications == [("user", 123), ("user", 123)]
    assert FakeZoteroClient.patch_calls[-1][3] == "web-secret"
    assert "web-secret" not in authorized.output
    assert "web-secret" not in pushed.output
    assert store.parse(path).note.zotero_tag_sync == "synced"


def test_missing_web_fallback_explains_required_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _vault(tmp_path)
    FakeZoteroClient.server_id = None
    monkeypatch.setattr(cli, "ZoteroClient", FakeZoteroClient)

    result = _invoke(tmp_path, ["authorize"])

    assert result.exit_code == 1
    assert "ZOTERO_WEB_API_KEY and ZOTERO_WEB_LIBRARY_ID" in result.stderr
