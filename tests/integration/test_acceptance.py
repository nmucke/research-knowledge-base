"""End-to-end acceptance test for the complete version 0.1 literature workflow.

The specification's acceptance test and definition of done describe one
sequence: a paper is added to Zotero, synchronised into Markdown, extracted,
reviewed by an agent, validated, approved, and pushed back to Zotero as tags,
after which a changed PDF marks the review outdated. This module drives that
sequence through the real CLI, services, and Markdown store against a stateful
fake of Zotero's local API and Better BibTeX's JSON-RPC endpoint.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pymupdf
import pytest
import yaml
from typer.testing import CliRunner, Result

import research_kb.cli as cli
from research_kb.better_bibtex import BetterBibTeXClient
from research_kb.markdown_store import MarkdownStore
from research_kb.zotero_client import ZoteroClient

REPO_ROOT = Path(__file__).parents[2]
DASHBOARDS_DIR = REPO_ROOT / "Literature" / "Dashboards"

SERVER_ID = "acceptance-zotero"
LOCAL_WRITE_KEY = "acceptance-local-write-key"
ITEM_KEY = "ACCEPT01"
FIRST_PDF_KEY = "PDFFIRST"
SECOND_PDF_KEY = "PDFSECND"
CITEKEY = "lovelaceAcceptance2026"
AGENT = "claude-code"
MODEL = "claude-opus-5"
EXISTING_ZOTERO_TAG = "imported-from-zotero"
APPROVED_TAG = "method/diffusion-models"
SUGGESTED_TAG = "method/likelihood-guided-sampling"
HUMAN_NOTE_TEXT = "My own reading notes, written before any agent ran."

# Filter expressions name frontmatter properties directly; `file.` and
# `formula.` references are Obsidian Bases built-ins rather than note fields.
_FILTER_FIELD = re.compile(r"(?<![\w.])([a-z][a-z0-9_]*)\s*[=!]=")

runner = CliRunner()


class FakeZoteroLibrary:
    """A small stateful stand-in for one item in Zotero's local HTTP API."""

    def __init__(self, pdf_path: Path) -> None:
        self.library_version = 10
        self.item_version = 10
        self.attachment_key = FIRST_PDF_KEY
        self.attachment_date_modified = "2026-08-01T09:00:00Z"
        self.pdf_path = pdf_path
        self.tags: list[dict[str, Any]] = [{"tag": EXISTING_ZOTERO_TAG, "type": 1}]
        self.issued_keys: list[str] = []
        self.patch_keys: list[str | None] = []

    def replace_pdf(self, attachment_key: str, pdf_path: Path, date_modified: str) -> None:
        """Model Zotero receiving a new PDF version for the same paper."""
        self.library_version += 1
        self.item_version = self.library_version
        self.attachment_key = attachment_key
        self.pdf_path = pdf_path
        self.attachment_date_modified = date_modified

    @property
    def tag_names(self) -> list[str]:
        return [tag["tag"] for tag in self.tags]

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in {"/api", "/api/"}:
            return self._discovery(request)
        if path == "/api/local/authorize":
            return self._authorize(request)
        if path == "/api/users/0/items/top":
            return self._items_top(request)
        if path == "/api/users/0/deleted":
            return self._deleted(request)
        if path == f"/api/users/0/items/{ITEM_KEY}":
            if request.method == "PATCH":
                return self._patch_item(request)
            return self._item(request)
        if path == f"/api/users/0/items/{ITEM_KEY}/children":
            return self._children(request)
        if re.fullmatch(r"/api/users/0/items/[A-Z0-9]{8}/file/view/url", path):
            return self._attachment_url(request, path.split("/")[5])
        raise AssertionError(f"unexpected Zotero request: {request.method} {request.url}")

    def _discovery(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "Zotero-API-Version": "3",
                "Zotero-Server-ID": SERVER_ID,
                "Zotero-Schema-Version": "42",
            },
            request=request,
        )

    def _authorize(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.headers["Zotero-Server-ID"] == SERVER_ID
        self.issued_keys.append(LOCAL_WRITE_KEY)
        return httpx.Response(200, json={"key": LOCAL_WRITE_KEY}, request=request)

    def _items_top(self, request: httpx.Request) -> httpx.Response:
        since = request.url.params.get("since")
        changed = since is None or self.item_version > int(since)
        return httpx.Response(
            200,
            json=[self._item_payload()] if changed else [],
            headers={"Last-Modified-Version": str(self.library_version)},
            request=request,
        )

    def _deleted(self, request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("since") is not None
        return httpx.Response(
            200,
            json={"items": [], "collections": [], "searches": [], "tags": [], "settings": []},
            headers={"Last-Modified-Version": str(self.library_version)},
            request=request,
        )

    def _item(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=self._item_payload(),
            headers={"Last-Modified-Version": str(self.library_version)},
            request=request,
        )

    def _patch_item(self, request: httpx.Request) -> httpx.Response:
        self.patch_keys.append(request.headers.get("Zotero-API-Key"))
        if request.headers.get("Zotero-API-Key") != LOCAL_WRITE_KEY:
            return httpx.Response(403, request=request)
        if request.headers.get("If-Unmodified-Since-Version") != str(self.item_version):
            return httpx.Response(412, request=request)
        payload = json.loads(request.content)
        assert set(payload) == {"tags"}, "a tag push must not modify bibliographic metadata"
        self.tags = list(payload["tags"])
        self.library_version += 1
        self.item_version = self.library_version
        return httpx.Response(
            204,
            headers={"Last-Modified-Version": str(self.library_version)},
            request=request,
        )

    def _children(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "key": self.attachment_key,
                    "version": self.item_version,
                    "library": {"type": "user", "id": 0},
                    "data": {
                        "key": self.attachment_key,
                        "version": self.item_version,
                        "itemType": "attachment",
                        "parentItem": ITEM_KEY,
                        "contentType": "application/pdf",
                        "filename": self.pdf_path.name,
                        "linkMode": "imported_file",
                        "title": "Full text PDF",
                        "dateModified": self.attachment_date_modified,
                        "mtime": 1_786_000_000_000,
                    },
                }
            ],
            headers={"Last-Modified-Version": str(self.library_version)},
            request=request,
        )

    def _attachment_url(self, request: httpx.Request, attachment_key: str) -> httpx.Response:
        if attachment_key != self.attachment_key:
            return httpx.Response(404, request=request)
        return httpx.Response(200, text=self.pdf_path.as_uri(), request=request)

    def _item_payload(self) -> dict[str, Any]:
        return {
            "key": ITEM_KEY,
            "version": self.item_version,
            "library": {"type": "user", "id": 0},
            "data": {
                "key": ITEM_KEY,
                "version": self.item_version,
                "itemType": "journalArticle",
                "title": "Diffusion surrogates for sparse-observation data assimilation",
                "creators": [
                    {"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"},
                    {"creatorType": "author", "firstName": "Grace", "lastName": "Hopper"},
                ],
                "date": "2026-03-01",
                "publicationTitle": "Journal of Computational Experiments",
                "volume": "12",
                "issue": "3",
                "pages": "115-142",
                "DOI": "10.1000/acceptance.2026",
                "url": "https://example.org/acceptance",
                "abstractNote": (
                    "We study diffusion-based surrogates for assimilating sparse and noisy "
                    "observations of nonlinear flow systems."
                ),
                "tags": self.tags,
                "collections": ["COLLECT1"],
                "dateAdded": "2026-08-01T09:00:00Z",
                "dateModified": "2026-08-05T09:00:00Z",
            },
        }


def _better_bibtex_handler(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    method = payload["method"]
    if method == "api.ready":
        result: Any = True
    elif method == "item.citationkey":
        result = {ITEM_KEY: CITEKEY}
    else:
        raise AssertionError(f"unexpected Better BibTeX method: {method}")
    return httpx.Response(
        200,
        json={"jsonrpc": "2.0", "id": payload["id"], "result": result},
        request=request,
    )


def _write_pdf(path: Path, pages: tuple[str, ...]) -> Path:
    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        page.insert_text((72, 72), text)
    document.save(path)
    document.close()
    return path


def _paper_pages(marker: str) -> tuple[str, ...]:
    body = f"{marker} sentence about assimilation, diffusion samplers, and flow systems.\n"
    return (body * 12, body * 12, body * 12)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Build a vault that matches a clean checkout of the repository."""
    root = tmp_path / "vault"
    (root / "Literature" / "Papers").mkdir(parents=True)
    (root / "Literature" / "Dashboards").mkdir(parents=True)
    for dashboard in DASHBOARDS_DIR.glob("*.base"):
        shutil.copy(dashboard, root / "Literature" / "Dashboards" / dashboard.name)
    shutil.copytree(REPO_ROOT / "System", root / "System")
    (root / "references.bib").write_text("% Better BibTeX keep-updated export\n", encoding="utf-8")
    return root


@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeZoteroLibrary:
    """Serve a Zotero item with one locally stored PDF over mocked transports."""
    storage = tmp_path / "zotero-storage"
    storage.mkdir()
    fake = FakeZoteroLibrary(_write_pdf(storage / "first.pdf", _paper_pages("First version")))
    zotero_transport = httpx.MockTransport(fake.handle)
    better_bibtex_transport = httpx.MockTransport(_better_bibtex_handler)

    def zotero_client(base_url: str, **kwargs: Any) -> ZoteroClient:
        return ZoteroClient(base_url, transport=zotero_transport, **kwargs)

    def better_bibtex_client(rpc_url: str, **kwargs: Any) -> BetterBibTeXClient:
        return BetterBibTeXClient(rpc_url, transport=better_bibtex_transport, **kwargs)

    monkeypatch.setattr(cli, "ZoteroClient", zotero_client)
    monkeypatch.setattr(cli, "BetterBibTeXClient", better_bibtex_client)
    return fake


def _env(vault: Path) -> dict[str, str]:
    """Pin every setting so a developer's local `.env` cannot change the run."""
    return {
        "RESEARCH_VAULT_PATH": str(vault),
        "ZOTERO_LOCAL_API": "http://localhost:23119/api",
        "BETTER_BIBTEX_RPC": "http://localhost:23119/better-bibtex/json-rpc",
        "ZOTERO_LIBRARY_TYPE": "user",
        "ZOTERO_LIBRARY_ID": "0",
        "RESEARCH_LOG_LEVEL": "INFO",
        "UNKNOWN_TAG_POLICY": "error",
        "ZOTERO_WEB_API_KEY": "",
        "ZOTERO_WEB_LIBRARY_ID": "",
        "ALLOWED_TAG_NAMESPACES": "domain,method,task,property,model,data",
    }


def _run(vault: Path, *arguments: str, exit_code: int = 0) -> Result:
    result = runner.invoke(cli.app, list(arguments), env=_env(vault))
    assert result.exit_code == exit_code, (
        f"research {' '.join(arguments)} exited {result.exit_code}: "
        f"{result.output}{result.exception!r}"
    )
    return result


def _frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    closing = text.index("\n---\n", 4)
    parsed = yaml.safe_load(text[4:closing])
    assert isinstance(parsed, dict)
    return parsed


def _edit(path: Path, old: str, new: str) -> None:
    """Apply the kind of manual edit a user makes in Obsidian."""
    text = path.read_text(encoding="utf-8")
    assert old in text, f"expected to find {old!r} in {path}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _review_block(review_date: date) -> str:
    return "\n".join(
        (
            f"> **Generated by:** {AGENT}",
            f"> **Model:** {MODEL}",
            f"> **Date:** {review_date.isoformat()}",
            "> **Scope:** full-text",
            "> **Coverage:** complete",
            "> **Human verified:** No",
            "",
            "### Summary",
            "",
            "- Trains a diffusion surrogate that assimilates sparse, noisy flow observations.",
            "- Compares the sampler with an ensemble Kalman baseline on two flow problems.",
            "",
            "### Main contribution",
            "",
            "A likelihood-guided sampling scheme that keeps assimilated states physically",
            "consistent while remaining tractable for sparse observations.",
            "",
            "### Main limitations",
            "",
            "- Evaluation covers only two low-dimensional systems, so scaling is unproven.",
            "",
            "### Reading recommendation",
            "",
            "**Read.** It connects generative modelling with data assimilation, a primary",
            "interest in the reading profile, and reports ablations.",
            "",
            "### Tags",
            "",
            f"**Applied:** `{APPROVED_TAG}`",
            f"**Suggested:** `{SUGGESTED_TAG}`",
        )
    )


def _dashboard_properties() -> set[str]:
    """Collect every note property the shipped Obsidian dashboards depend on."""
    properties: set[str] = set()
    for dashboard in sorted(DASHBOARDS_DIR.glob("*.base")):
        parsed = yaml.safe_load(dashboard.read_text(encoding="utf-8"))
        containers = [parsed, *parsed.get("views", [])]
        for container in containers:
            for condition in container.get("filters", {}).get("and", []):
                properties.update(_FILTER_FIELD.findall(condition))
            for column in container.get("order", []):
                if column.startswith("note."):
                    properties.add(column.removeprefix("note."))
            for rule in container.get("sort", []):
                if rule["property"].startswith("note."):
                    properties.add(rule["property"].removeprefix("note."))
    return properties


def test_version_0_1_acceptance_workflow(vault: Path, library: FakeZoteroLibrary) -> None:
    store = MarkdownStore(vault / "Literature" / "Papers")
    note_path = store.note_path(CITEKEY)

    # 1. The environment is healthy before any paper is processed.
    doctor = _run(vault, "doctor")
    assert "PASS" in doctor.output
    assert "FAIL" not in doctor.output

    # 2. `research sync` turns the new Zotero item into a valid Markdown note.
    created = _run(vault, "sync")
    assert f"CREATE {ITEM_KEY} {CITEKEY}" in created.output
    assert note_path.is_file()
    note = store.parse(note_path).note
    assert note.title == "Diffusion surrogates for sparse-observation data assimilation"
    assert note.authors == ("Ada Lovelace", "Grace Hopper")
    assert note.year == 2026
    assert note.zotero_key == ITEM_KEY
    assert note.pdf_attachment_key == FIRST_PDF_KEY
    assert note.zotero_tags == (EXISTING_ZOTERO_TAG,)
    assert note.human_read_status == "unread"
    assert note.ai_review_status == "not-reviewed"

    # 3. The user triages the paper in Obsidian and starts personal notes.
    _edit(note_path, "human_read_status: unread", "human_read_status: queued")
    _edit(note_path, "human_priority: null", "human_priority: 4")
    _edit(note_path, "human_relevance: null", "human_relevance: 5")
    _edit(
        note_path,
        "### Summary\n\n### Important results",
        f"### Summary\n\n{HUMAN_NOTE_TEXT}\n\n### Important results",
    )

    # 4. `research extract` produces readable, page-aware text.
    extracted = _run(vault, "extract", CITEKEY)
    assert extracted.output.startswith(f"EXTRACTED {CITEKEY} ->")
    assert "Status: complete" in extracted.output
    cache_path = vault / ".research" / "paper-text" / f"{CITEKEY}.md"
    cache_text = cache_path.read_text(encoding="utf-8")
    assert "<!-- PAGE 1 -->" in cache_text
    assert "<!-- PAGE 3 -->" in cache_text
    assert "First version" in cache_text
    assert _run(vault, "extract", CITEKEY).output.startswith(f"CACHED {CITEKEY} ->")

    # 5. `research review-context` names the four files an agent must read.
    context = _run(vault, "review-context", CITEKEY)
    assert "Literature/Papers/lovelaceAcceptance2026.md" in context.output
    assert ".research/paper-text/lovelaceAcceptance2026.md" in context.output
    assert "System/reading-profile.md" in context.output
    assert "System/tag-registry.md" in context.output
    snapshot_path = vault / ".research" / "review-snapshots" / f"{CITEKEY}.json"
    assert snapshot_path.is_file()

    # 6. The agent writes only AI-owned frontmatter and the managed block.
    review_date = date.today()
    store.update_ai_fields(
        note_path,
        {
            "ai_review_status": "reviewed",
            "ai_review_scope": "full-text",
            "ai_review_coverage": "complete",
            "ai_review_agent": AGENT,
            "ai_review_model": MODEL,
            "ai_review_date": review_date,
            "ai_review_version": 1,
            "ai_recommendation": "read",
            "ai_recommendation_reason": (
                "Connects generative modelling with data assimilation and reports ablations."
            ),
            "ai_relevance": 4,
            "ai_recommendation_confidence": "medium",
            "ai_applied_tags": (APPROVED_TAG,),
            "ai_suggested_tags": (SUGGESTED_TAG,),
        },
    )
    store.replace_managed_block(note_path, "AI_REVIEW", _review_block(review_date))

    # 7. Validation passes and the protected-content baseline is consumed.
    validated = _run(vault, "validate", CITEKEY)
    assert "Summary: checked=1, errors=0, warnings=0" in validated.output
    assert not snapshot_path.exists()

    reviewed = store.parse(note_path)
    assert reviewed.note.ai_review_status == "reviewed"
    assert reviewed.note.ai_review_human_verified is False
    assert reviewed.note.human_read_status == "queued"
    assert reviewed.note.human_priority == 4
    assert reviewed.note.human_relevance == 5
    assert HUMAN_NOTE_TEXT in reviewed.body

    # 8. The user approves one existing registry tag; suggestions stay proposals.
    _edit(note_path, "tags: []", f"tags:\n- {APPROVED_TAG}")
    plan = _run(vault, "tags", CITEKEY)
    assert f"Existing Zotero tags: {EXISTING_ZOTERO_TAG}" in plan.output
    assert f"Approved tags: {APPROVED_TAG}" in plan.output
    assert f"Suggested tags (not pushed): {SUGGESTED_TAG}" in plan.output
    assert f"Pending additions: {APPROVED_TAG}" in plan.output

    dry_run = _run(vault, "push-tags", CITEKEY, "--dry-run")
    assert f"DRY-RUN: would add {APPROVED_TAG}" in dry_run.output
    assert library.tag_names == [EXISTING_ZOTERO_TAG]

    # 9. An explicit authorization precedes the only Zotero write in the flow.
    authorized = _run(vault, "authorize")
    assert "Authorization saved" in authorized.output
    assert (vault / ".research" / "credentials.json").is_file()
    assert library.issued_keys == [LOCAL_WRITE_KEY]

    pushed = _run(vault, "push-tags", CITEKEY)
    assert f"PUSH: added {APPROVED_TAG} (attempts=1)" in pushed.output
    assert library.patch_keys == [LOCAL_WRITE_KEY]
    assert library.tag_names == [EXISTING_ZOTERO_TAG, APPROVED_TAG]
    assert {"tag": EXISTING_ZOTERO_TAG, "type": 1} in library.tags
    assert SUGGESTED_TAG not in library.tag_names

    synchronized = store.parse(note_path).note
    assert synchronized.zotero_tag_sync == "synced"
    assert synchronized.zotero_tag_sync_date is not None
    assert set(synchronized.zotero_tags) == {EXISTING_ZOTERO_TAG, APPROVED_TAG}
    assert synchronized.ai_suggested_tags == (SUGGESTED_TAG,)
    assert synchronized.human_read_status == "queued"

    assert "Summary: checked=1, errors=0, warnings=0" in _run(vault, "validate").output

    # 10. A changed PDF marks the AI review outdated without touching human state.
    library.replace_pdf(
        SECOND_PDF_KEY,
        _write_pdf(library.pdf_path.parent / "second.pdf", _paper_pages("Revised version")),
        "2026-08-06T12:00:00Z",
    )
    updated = _run(vault, "sync")
    assert f"UPDATE {ITEM_KEY} {CITEKEY}" in updated.output
    assert "mode=incremental" in updated.output

    outdated = store.parse(note_path)
    assert outdated.note.ai_review_status == "outdated"
    assert outdated.note.pdf_attachment_key == SECOND_PDF_KEY
    assert outdated.note.human_read_status == "queued"
    assert outdated.note.human_priority == 4
    assert outdated.note.human_relevance == 5
    assert outdated.note.ai_review_human_verified is False
    assert HUMAN_NOTE_TEXT in outdated.body
    assert "**Read.** It connects generative modelling" in outdated.body

    # The stale extraction cache is reported until the new PDF is extracted.
    stale = _run(vault, "validate", exit_code=1)
    assert "extraction-cache-mismatch" in stale.output
    re_extracted = _run(vault, "extract", CITEKEY)
    assert re_extracted.output.startswith(f"EXTRACTED {CITEKEY} ->")
    assert "Revised version" in cache_path.read_text(encoding="utf-8")
    assert "Summary: checked=1, errors=0, warnings=0" in _run(vault, "validate").output

    # 11. Obsidian can display the note: every dashboard property exists in it.
    properties = _dashboard_properties()
    assert properties
    assert properties <= set(_frontmatter(note_path))
