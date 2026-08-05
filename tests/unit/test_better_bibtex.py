"""Tests for Better BibTeX JSON-RPC readiness checks."""

import json
from collections.abc import Callable

import httpx
import pytest

from research_kb.better_bibtex import BetterBibTeXClient
from research_kb.exceptions import BetterBibTeXUnavailableError, CitationKeyMissingError

RPC_URL = "http://zotero.test/better-bibtex/json-rpc"
UNAVAILABLE_MESSAGE = (
    "Better BibTeX is unavailable. Install or enable Better BibTeX and restart Zotero."
)


def test_check_ready_posts_expected_json_rpc_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == RPC_URL
        assert json.loads(request.content) == {
            "jsonrpc": "2.0",
            "method": "api.ready",
            "params": [],
            "id": "research-kb-ready",
        }
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": "research-kb-ready", "result": True},
        )

    with BetterBibTeXClient(RPC_URL, transport=httpx.MockTransport(handler)) as client:
        client.check_ready()


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("offline", request=request)),
        lambda request: httpx.Response(503),
        lambda request: httpx.Response(200, content=b"not json"),
        lambda request: httpx.Response(200, json=[]),
        lambda request: httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": "research-kb-ready",
                "error": {"code": -32601, "message": "missing"},
            },
        ),
        lambda request: httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": "research-kb-ready", "result": False},
        ),
    ],
    ids=["connection", "http", "invalid_json", "malformed", "json_rpc_error", "not_ready"],
)
def test_check_ready_converts_failures_to_actionable_error(
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    with BetterBibTeXClient(RPC_URL, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BetterBibTeXUnavailableError, match=f"^{UNAVAILABLE_MESSAGE}$"):
            client.check_ready()


def test_get_citation_key_posts_my_library_json_rpc_request_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == RPC_URL
        assert json.loads(request.content) == {
            "jsonrpc": "2.0",
            "method": "item.citationkey",
            "params": [["HBCH99D2"]],
            "id": "research-kb-citation-key",
        }
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": "research-kb-citation-key",
                "result": {"HBCH99D2": "Doe2026"},
            },
        )

    with BetterBibTeXClient(RPC_URL, transport=httpx.MockTransport(handler)) as client:
        assert client.get_citation_key("HBCH99D2") == "Doe2026"


def test_get_citation_key_prefixes_nonzero_library_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["params"] == [["42:HBCH99D2"]]
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": "research-kb-citation-key",
                "result": {"42:HBCH99D2": "Doe2026"},
            },
        )

    with BetterBibTeXClient(RPC_URL, transport=httpx.MockTransport(handler)) as client:
        assert client.get_citation_key("HBCH99D2", library_id=42) == "Doe2026"


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"HBCH99D2": None},
        {"HBCH99D2": ""},
        {"HBCH99D2": 42},
        [],
    ],
    ids=["missing_mapping", "null", "empty", "wrong_type", "wrong_result_type"],
)
def test_get_citation_key_reports_missing_keys(result: object) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": "research-kb-citation-key",
                "result": result,
            },
        )

    with BetterBibTeXClient(RPC_URL, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(
            CitationKeyMissingError,
            match="HBCH99D2.*Generate or refresh.*Better BibTeX",
        ):
            client.get_citation_key("HBCH99D2")


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"jsonrpc": "2.0", "id": "wrong", "result": {}},
        {"jsonrpc": "1.0", "id": "research-kb-citation-key", "result": {}},
        {"jsonrpc": "2.0", "id": "research-kb-citation-key"},
        {
            "jsonrpc": "2.0",
            "id": "research-kb-citation-key",
            "error": {"code": -32601, "message": "missing"},
        },
    ],
    ids=["not_object", "wrong_id", "wrong_version", "missing_result", "rpc_error"],
)
def test_get_citation_key_converts_bad_envelopes_to_unavailable(payload: object) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with BetterBibTeXClient(RPC_URL, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BetterBibTeXUnavailableError, match=f"^{UNAVAILABLE_MESSAGE}$"):
            client.get_citation_key("HBCH99D2")


def test_get_citation_key_converts_transport_failure_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    with BetterBibTeXClient(RPC_URL, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BetterBibTeXUnavailableError, match=f"^{UNAVAILABLE_MESSAGE}$"):
            client.get_citation_key("HBCH99D2")


@pytest.mark.parametrize(
    ("item_key", "library_id"),
    [("", 0), ("not-a-key", 0), ("HBCH99D2", -1), ("HBCH99D2", True)],
)
def test_get_citation_key_rejects_malformed_item_references(
    item_key: object, library_id: object
) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(500))
    with BetterBibTeXClient(RPC_URL, transport=transport) as client:
        with pytest.raises(ValueError):
            client.get_citation_key(item_key, library_id=library_id)  # type: ignore[arg-type]
