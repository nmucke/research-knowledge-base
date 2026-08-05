"""Tests for Better BibTeX JSON-RPC readiness checks."""

import json

import httpx
import pytest

from research_kb.better_bibtex import BetterBibTeXClient
from research_kb.exceptions import BetterBibTeXUnavailableError

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
        assert client.check_ready() is None


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
    handler: httpx.MockTransport,
) -> None:
    with BetterBibTeXClient(RPC_URL, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BetterBibTeXUnavailableError, match=f"^{UNAVAILABLE_MESSAGE}$"):
            client.check_ready()
