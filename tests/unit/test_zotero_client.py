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
def test_discover_rejects_missing_or_invalid_headers(headers: dict[str, str], message: str) -> None:
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
            "volume": "42",
            "issue": "7",
            "pages": "100-120",
            "collections": ["COLLECT01"],
            "dateAdded": "2026-08-05T10:00:00Z",
            "dateModified": "2026-08-06T10:00:00Z",
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
    assert item.volume == "42"
    assert item.issue == "7"
    assert item.pages == "100-120"
    assert item.collections == ("COLLECT01",)
    assert item.date_added == "2026-08-05T10:00:00Z"
    assert item.date_modified == "2026-08-06T10:00:00Z"
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


def test_list_items_paginates_and_filters_non_paper_objects() -> None:
    requests: list[httpx.Request] = []
    unsupported = _item_payload()
    unsupported_data = unsupported["data"]
    assert isinstance(unsupported_data, dict)
    unsupported_data["itemType"] = "attachment"
    child = _item_payload()
    child_data = child["data"]
    assert isinstance(child_data, dict)
    child_data["parentItem"] = "PARENT01"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/deleted"):
            return httpx.Response(
                200,
                json={"items": ["DELETED1"]},
                headers={"Last-Modified-Version": "18"},
                request=request,
            )
        if request.url.params.get("start") == "0":
            return httpx.Response(
                200,
                json=[_item_payload(), unsupported, child],
                headers={
                    "Last-Modified-Version": "18",
                    "Link": (
                        "<http://zotero.test/api/users/0/items/top?"
                        'limit=100&start=100&since=0>; rel="next"'
                    ),
                },
                request=request,
            )
        return httpx.Response(
            200,
            json=[],
            headers={"Last-Modified-Version": "18"},
            request=request,
        )

    with ZoteroClient("http://zotero.test/api", transport=httpx.MockTransport(handler)) as client:
        batch = client.list_items("user", 0, since=0)

    assert [item.key for item in batch.items] == ["ABCDE123"]
    assert batch.removed_item_keys == ("DELETED1",)
    assert batch.library_version == 18
    assert requests[0].url.path == "/api/users/0/items/top"
    assert dict(requests[0].url.params) == {
        "limit": "100",
        "start": "0",
        "since": "0",
        "includeTrashed": "1",
    }
    assert dict(requests[1].url.params)["start"] == "100"
    assert requests[2].url.path == "/api/users/0/deleted"
    assert dict(requests[2].url.params) == {"since": "0"}


@pytest.mark.parametrize("header", [None, "not-a-number", "-1"])
def test_list_items_rejects_invalid_library_version_headers(header: str | None) -> None:
    headers = {} if header is None else {"Last-Modified-Version": header}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[], headers=headers, request=request)

    with ZoteroClient("http://zotero.test/api", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ZoteroInvalidResponseError, match="Last-Modified-Version"):
            client.list_items()


def test_list_items_unions_trashed_and_deleted_item_keys() -> None:
    trashed = _item_payload()
    trashed_data = trashed["data"]
    assert isinstance(trashed_data, dict)
    trashed_data["deleted"] = True

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/items/top"):
            return httpx.Response(
                200,
                json=[trashed],
                headers={"Last-Modified-Version": "20"},
                request=request,
            )
        return httpx.Response(
            200,
            json={"items": ["ABCDE123", "ZXCVB123"]},
            headers={"Last-Modified-Version": "20"},
            request=request,
        )

    with ZoteroClient("http://zotero.test/api", transport=httpx.MockTransport(handler)) as client:
        batch = client.list_items(since=4)

    assert batch.items == ()
    assert batch.removed_item_keys == ("ABCDE123", "ZXCVB123")


def test_list_items_full_read_does_not_request_deleted_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=[],
            headers={"Last-Modified-Version": "20"},
            request=request,
        )

    with ZoteroClient("http://zotero.test/api", transport=httpx.MockTransport(handler)) as client:
        client.list_items()

    assert [request.url.path for request in requests] == ["/api/users/0/items/top"]


def test_list_items_allows_local_api_without_deleted_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/deleted"):
            return httpx.Response(404, request=request)
        return httpx.Response(
            200,
            json=[],
            headers={"Last-Modified-Version": "20"},
            request=request,
        )

    with ZoteroClient("http://zotero.test/api", transport=httpx.MockTransport(handler)) as client:
        batch = client.list_items(since=4)

    assert batch.library_version == 20
    assert batch.items == ()
    assert batch.removed_item_keys == ()


@pytest.mark.parametrize(
    ("payload", "version"),
    [
        ({"items": ["bad-key"]}, "20"),
        ({"items": "ABCDE123"}, "20"),
        ([], "20"),
        ({"items": []}, "21"),
    ],
)
def test_list_items_rejects_invalid_or_inconsistent_deleted_response(
    payload: object, version: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/items/top"):
            return httpx.Response(
                200,
                json=[],
                headers={"Last-Modified-Version": "20"},
                request=request,
            )
        return httpx.Response(
            200,
            json=payload,
            headers={"Last-Modified-Version": version},
            request=request,
        )

    with ZoteroClient("http://zotero.test/api", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ZoteroInvalidResponseError):
            client.list_items(since=1)


@pytest.mark.parametrize(
    "link",
    [
        '<https://elsewhere.test/items/top?start=100>; rel="next"',
        '<http://zotero.test/api/users/0/items/top?start=0>; rel="next"',
        '<http://zotero.test/api/users/0/items/top?start=not-a-number>; rel="next"',
    ],
)
def test_list_items_rejects_unsafe_or_looping_pagination_links(link: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[],
            headers={"Last-Modified-Version": "20", "Link": link},
            request=request,
        )

    with ZoteroClient("http://zotero.test/api", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ZoteroInvalidResponseError, match="pagination Link"):
            client.list_items()


def test_list_items_preserves_cursor_when_the_next_link_drops_it() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params.get("start") == "0":
            return httpx.Response(
                200,
                json=[],
                headers={
                    "Last-Modified-Version": "20",
                    "Link": '<http://zotero.test/api/users/0/items/top?start=100>; rel="next"',
                },
                request=request,
            )
        if request.url.path.endswith("/deleted"):
            return httpx.Response(
                200,
                json={"items": []},
                headers={"Last-Modified-Version": "20"},
                request=request,
            )
        return httpx.Response(
            200,
            json=[],
            headers={"Last-Modified-Version": "20"},
            request=request,
        )

    with ZoteroClient("http://zotero.test/api", transport=httpx.MockTransport(handler)) as client:
        client.list_items(since=3)

    assert dict(requests[1].url.params) == {
        "limit": "100",
        "start": "100",
        "since": "3",
        "includeTrashed": "1",
    }
