"""Tests for local Zotero API discovery."""

import httpx
import pytest

from research_kb.exceptions import ZoteroUnavailableError
from research_kb.zotero_client import ZoteroClient, ZoteroServerInfo


def _response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        headers={
            "Zotero-API-Version": "3",
            "Zotero-Server-ID": "local-zotero",
            "Zotero-Schema-Version": "12",
        },
        request=request,
    )


def test_discover_returns_server_info() -> None:
    client = ZoteroClient("http://zotero.test/api", transport=httpx.MockTransport(_response))

    with client:
        info = client.discover()

    assert info == ZoteroServerInfo(api_version=3, server_id="local-zotero", schema_version=12)


def test_discover_requests_api_root_with_trailing_slash() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _response(request)

    with ZoteroClient("http://zotero.test/api", transport=httpx.MockTransport(handler)) as client:
        client.discover()

    assert requests[0].method == "GET"
    assert requests[0].url.path == "/api/"


def test_discover_accepts_response_without_server_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "Zotero-API-Version": "3",
                "Zotero-Schema-Version": "42",
            },
            request=request,
        )

    with ZoteroClient("http://zotero.test/api", transport=httpx.MockTransport(handler)) as client:
        info = client.discover()

    assert info == ZoteroServerInfo(api_version=3, server_id=None, schema_version=42)


def test_discover_converts_connection_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with ZoteroClient("http://zotero.test/api", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ZoteroUnavailableError, match="Could not reach"):
            client.discover()


def test_discover_converts_bad_http_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    with ZoteroClient("http://zotero.test/api", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ZoteroUnavailableError, match="Could not reach"):
            client.discover()


@pytest.mark.parametrize(
    ("headers", "message"),
    [
        ({"Zotero-Server-ID": "server", "Zotero-Schema-Version": "12"}, "Zotero-API-Version"),
        ({"Zotero-API-Version": "3", "Zotero-Server-ID": "server"}, "Zotero-Schema-Version"),
        (
            {
                "Zotero-API-Version": "three",
                "Zotero-Server-ID": "server",
                "Zotero-Schema-Version": "12",
            },
            "Zotero-API-Version",
        ),
        (
            {
                "Zotero-API-Version": "3",
                "Zotero-Server-ID": "server",
                "Zotero-Schema-Version": "twelve",
            },
            "Zotero-Schema-Version",
        ),
    ],
)
def test_discover_rejects_missing_or_invalid_headers(
    headers: dict[str, str], message: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=headers, request=request)

    with ZoteroClient("http://zotero.test/api", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ZoteroUnavailableError, match=message):
            client.discover()


def test_discover_rejects_unsupported_api_version() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "Zotero-API-Version": "2",
                "Zotero-Server-ID": "local-zotero",
                "Zotero-Schema-Version": "12",
            },
            request=request,
        )

    with ZoteroClient("http://zotero.test/api", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ZoteroUnavailableError, match="Unsupported Zotero API version 2"):
            client.discover()
