"""Tests for local Zotero API discovery."""

import httpx
import pytest

from research_kb.exceptions import (
    ZoteroInvalidItemReferenceError,
    ZoteroInvalidResponseError,
    ZoteroItemNotFoundError,
    ZoteroUnavailableError,
    ZoteroUnsupportedItemError,
)
from research_kb.zotero_client import SUPPORTED_ITEM_TYPES, ZoteroClient, ZoteroServerInfo


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


def _item_payload() -> dict[str, object]:
    return {
        "key": "ABCDE123",
        "version": 17,
        "library": {"id": 42, "type": "group"},
        "data": {
            "itemType": "journalArticle",
            "title": "A representative paper",
            "creators": [
                {"firstName": "Ada", "lastName": "Lovelace", "creatorType": "author"},
                {"name": "CERN", "creatorType": "contributor"},
            ],
            "date": "2025-01-02",
            "publicationTitle": "Journal of Examples",
            "DOI": "10.1000/example",
            "url": "https://example.test/paper",
            "abstractNote": "An abstract.",
            "tags": [{"tag": "physics"}, {"tag": "methods"}],
        },
    }


def test_get_item_requests_expected_path_and_parses_real_item_payload() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_item_payload(), request=request)

    with ZoteroClient("http://zotero.test/api", transport=httpx.MockTransport(handler)) as client:
        item = client.get_item("ABCDE123", library_type="group", library_id=42)

    assert requests[0].method == "GET"
    assert requests[0].url.path == "/api/groups/42/items/ABCDE123"
    assert item.key == "ABCDE123"
    assert item.version == 17
    assert item.library_id == 42
    assert item.publication == "Journal of Examples"
    assert item.tags == ("physics", "methods")
    assert item.creators[1].display_name == "CERN"
    assert item.authors[0].display_name == "Ada Lovelace"


def test_get_item_allows_null_optional_fields_and_uses_publication_fallback() -> None:
    payload = _item_payload()
    data = payload["data"]
    assert isinstance(data, dict)
    data.update(
        {
            "date": None,
            "publicationTitle": "",
            "bookTitle": None,
            "proceedingsTitle": "Proceedings",
            "DOI": None,
            "url": None,
            "abstractNote": None,
            "tags": None,
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    with ZoteroClient("http://zotero.test/api", transport=httpx.MockTransport(handler)) as client:
        item = client.get_item("ABCDE123")

    assert item.date is None
    assert item.publication == "Proceedings"
    assert item.doi is None
    assert item.url is None
    assert item.abstract is None
    assert item.tags == ()


def test_get_item_distinguishes_a_missing_item() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    with ZoteroClient("http://zotero.test/api", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ZoteroItemNotFoundError, match="ABCDE123"):
            client.get_item("ABCDE123")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda payload: payload["data"].update({"itemType": "attachment"}), "not supported"),
        (lambda payload: payload["data"].update({"deleted": True}), "Deleted"),
        (lambda payload: payload["data"].update({"parentItem": "PARENT123"}), "child"),
        (lambda payload: payload["library"].update({"type": "feed"}), "feed"),
    ],
)
def test_get_item_rejects_unsupported_items(change: object, message: str) -> None:
    payload = _item_payload()
    assert callable(change)
    change(payload)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    with ZoteroClient("http://zotero.test/api", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ZoteroUnsupportedItemError, match=message):
            client.get_item("ABCDE123")


@pytest.mark.parametrize("body", ["not JSON", {"key": "ABCDE123"}])
def test_get_item_rejects_invalid_json_or_shape(body: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(body, str):
            return httpx.Response(200, text=body, request=request)
        return httpx.Response(200, json=body, request=request)

    with ZoteroClient("http://zotero.test/api", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ZoteroInvalidResponseError):
            client.get_item("ABCDE123")


def test_get_item_converts_connection_errors_to_actionable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with ZoteroClient("http://zotero.test/api", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ZoteroUnavailableError, match="Could not fetch Zotero item"):
            client.get_item("ABCDE123")


@pytest.mark.parametrize(
    ("zotero_key", "library_type", "library_id"),
    [
        ("", "user", 0),
        ("bad/key!", "user", 0),
        ("abcde123", "user", 0),
        ("ABCDE123", "shared", 0),
        ("ABCDE123", "user", -1),
        ("ABCDE123", "user", True),
    ],
)
def test_get_item_rejects_malformed_item_references(
    zotero_key: object, library_type: object, library_id: object
) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(500))
    with ZoteroClient("http://zotero.test/api", transport=transport) as client:
        with pytest.raises(ZoteroInvalidItemReferenceError):
            client.get_item(  # type: ignore[arg-type]
                zotero_key,
                library_type=library_type,
                library_id=library_id,
            )


@pytest.mark.parametrize(
    "change",
    [
        lambda payload: payload.update({"key": "bad/key!"}),
        lambda payload: payload["data"].update({"parentItem": 7}),
        lambda payload: payload["data"].update({"deleted": "false"}),
    ],
)
def test_get_item_rejects_malformed_identity_and_state_fields(change: object) -> None:
    payload = _item_payload()
    assert callable(change)
    change(payload)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    with ZoteroClient("http://zotero.test/api", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ZoteroInvalidResponseError):
            client.get_item("ABCDE123")


def test_supported_item_types_are_the_documented_set() -> None:
    assert SUPPORTED_ITEM_TYPES == {
        "journalArticle",
        "conferencePaper",
        "preprint",
        "book",
        "bookSection",
        "thesis",
        "report",
        "document",
    }
