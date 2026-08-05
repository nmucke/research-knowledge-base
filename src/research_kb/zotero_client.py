"""Small, typed client for the local Zotero API."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self, cast

import httpx

from research_kb.exceptions import (
    ZoteroInvalidItemReferenceError,
    ZoteroInvalidResponseError,
    ZoteroItemNotFoundError,
    ZoteroUnavailableError,
    ZoteroUnsupportedItemError,
)
from research_kb.models import ZoteroCreator, ZoteroItem

SUPPORTED_ITEM_TYPES = frozenset(
    {
        "journalArticle",
        "conferencePaper",
        "preprint",
        "book",
        "bookSection",
        "thesis",
        "report",
        "document",
    }
)
_ITEM_KEY_PATTERN = re.compile(r"^[A-Z0-9]{8}$")


@dataclass(frozen=True)
class ZoteroServerInfo:
    """Version information returned by the local Zotero API."""

    api_version: int
    server_id: str | None
    schema_version: int


class ZoteroClient:
    """Client for the local Zotero API server."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=f"{base_url.rstrip('/')}/",
            timeout=timeout,
            transport=transport,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        """Release the underlying HTTP client resources."""
        self._client.close()

    def discover(self) -> ZoteroServerInfo:
        """Confirm that the local API is available and supports API version 3."""
        try:
            response = self._client.get("")
            response.raise_for_status()
        except httpx.HTTPError:
            raise ZoteroUnavailableError(
                "Could not reach the local Zotero API. Is Zotero running and its local API enabled?"
            ) from None

        api_version = self._integer_header(response, "Zotero-API-Version")
        server_id = self._optional_string_header(response, "Zotero-Server-ID")
        schema_version = self._integer_header(response, "Zotero-Schema-Version")

        if api_version != 3:
            raise ZoteroUnavailableError(
                f"Unsupported Zotero API version {api_version}; "
                "this tool currently requires version 3."
            )

        return ZoteroServerInfo(
            api_version=api_version,
            server_id=server_id,
            schema_version=schema_version,
        )

    def get_item(
        self, zotero_key: str, *, library_type: str = "user", library_id: int = 0
    ) -> ZoteroItem:
        """Read and validate a single supported, top-level Zotero item."""
        self._validate_item_reference(zotero_key, library_type, library_id)

        path = f"{library_type}s/{library_id}/items/{zotero_key}"
        try:
            response = self._client.get(path)
            if response.status_code == httpx.codes.NOT_FOUND:
                raise ZoteroItemNotFoundError(
                    f"Zotero item {zotero_key!r} was not found in "
                    f"{library_type} library {library_id}."
                )
            response.raise_for_status()
        except ZoteroItemNotFoundError:
            raise
        except httpx.HTTPError as error:
            raise ZoteroUnavailableError(
                f"Could not fetch Zotero item {zotero_key!r}: {error}. "
                "Is Zotero running and its local API enabled?"
            ) from error

        try:
            payload: object = response.json()
        except ValueError as error:
            raise ZoteroInvalidResponseError(
                f"Zotero returned invalid JSON for item {zotero_key!r}."
            ) from error
        return self._parse_item(payload)

    @classmethod
    def _parse_item(cls, payload: object) -> ZoteroItem:
        item = cls._mapping(payload, "item")
        data = cls._mapping(item.get("data"), "item.data")
        library = cls._mapping(item.get("library"), "item.library")

        item_type = cls._required_string(data, "itemType", "item.data")
        if item_type not in SUPPORTED_ITEM_TYPES:
            raise ZoteroUnsupportedItemError(f"Zotero item type {item_type!r} is not supported.")
        parent_item = data.get("parentItem")
        if parent_item is not None and not isinstance(parent_item, str):
            raise ZoteroInvalidResponseError("Zotero response has invalid item.data.parentItem.")
        if parent_item:
            raise ZoteroUnsupportedItemError("Zotero child items cannot be reviewed as papers.")
        deleted = data.get("deleted")
        if deleted is not None and not isinstance(deleted, bool):
            raise ZoteroInvalidResponseError("Zotero response has invalid item.data.deleted.")
        if deleted:
            raise ZoteroUnsupportedItemError("Deleted Zotero items cannot be reviewed as papers.")
        if cls._optional_string(library, "type", "item.library") == "feed":
            raise ZoteroUnsupportedItemError("Zotero feed items cannot be reviewed as papers.")

        return ZoteroItem(
            key=cls._response_item_key(item),
            version=cls._required_integer(item, "version", "item"),
            library_id=cls._required_integer(library, "id", "item.library"),
            item_type=item_type,
            title=cls._required_string(data, "title", "item.data"),
            creators=cls._creators(data.get("creators")),
            date=cls._optional_string(data, "date", "item.data"),
            publication=cls._publication(data),
            doi=cls._optional_string(data, "DOI", "item.data"),
            url=cls._optional_string(data, "url", "item.data"),
            abstract=cls._optional_string(data, "abstractNote", "item.data"),
            tags=cls._tags(data.get("tags")),
        )

    @classmethod
    def _creators(cls, value: object) -> tuple[ZoteroCreator, ...]:
        if value is None:
            return ()
        creators = cls._list(value, "item.data.creators")
        result: list[ZoteroCreator] = []
        for index, raw_creator in enumerate(creators):
            creator = cls._mapping(raw_creator, f"item.data.creators[{index}]")
            name = cls._optional_string(creator, "name", "creator") or ""
            first_name = cls._optional_string(creator, "firstName", "creator") or ""
            last_name = cls._optional_string(creator, "lastName", "creator") or ""
            if not name and not (first_name or last_name):
                raise ZoteroInvalidResponseError("A Zotero creator must have a name.")
            result.append(
                ZoteroCreator(
                    first_name=first_name,
                    last_name=last_name,
                    name=name,
                    creator_type=cls._required_string(creator, "creatorType", "creator"),
                )
            )
        return tuple(result)

    @classmethod
    def _tags(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        tags = cls._list(value, "item.data.tags")
        return tuple(
            cls._required_string(cls._mapping(tag, "item.data.tags entry"), "tag", "tag")
            for tag in tags
        )

    @classmethod
    def _publication(cls, data: Mapping[str, object]) -> str | None:
        for field in (
            "publicationTitle",
            "bookTitle",
            "proceedingsTitle",
            "university",
            "institution",
        ):
            value = cls._optional_string(data, field, "item.data")
            if value:
                return value
        return None

    @staticmethod
    def _mapping(value: object, location: str) -> Mapping[str, object]:
        if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
            raise ZoteroInvalidResponseError(f"Zotero response has invalid {location}.")
        return cast(Mapping[str, object], value)

    @staticmethod
    def _list(value: object, location: str) -> list[object]:
        if not isinstance(value, list):
            raise ZoteroInvalidResponseError(f"Zotero response has invalid {location}.")
        return cast(list[object], value)

    @classmethod
    def _required_string(cls, data: Mapping[str, object], field: str, location: str) -> str:
        value = cls._optional_string(data, field, location)
        if value is None:
            raise ZoteroInvalidResponseError(f"Zotero response is missing {location}.{field}.")
        return value

    @staticmethod
    def _optional_string(data: Mapping[str, object], field: str, location: str) -> str | None:
        value = data.get(field)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ZoteroInvalidResponseError(f"Zotero response has invalid {location}.{field}.")
        return value.strip() or None

    @staticmethod
    def _required_integer(data: Mapping[str, object], field: str, location: str) -> int:
        value = data.get(field)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ZoteroInvalidResponseError(f"Zotero response has invalid {location}.{field}.")
        return value

    @staticmethod
    def _response_item_key(item: Mapping[str, object]) -> str:
        value = item.get("key")
        if not isinstance(value, str) or not _ITEM_KEY_PATTERN.fullmatch(value):
            raise ZoteroInvalidResponseError("Zotero response has invalid item.key.")
        return value

    @staticmethod
    def _validate_item_reference(zotero_key: str, library_type: str, library_id: int) -> None:
        if not isinstance(zotero_key, str) or not _ITEM_KEY_PATTERN.fullmatch(zotero_key):
            raise ZoteroInvalidItemReferenceError(
                "Zotero item keys must contain exactly 8 uppercase letters or digits."
            )
        if library_type not in {"user", "group"}:
            raise ZoteroInvalidItemReferenceError(
                "Zotero library type must be 'user' or 'group'."
            )
        if isinstance(library_id, bool) or not isinstance(library_id, int) or library_id < 0:
            raise ZoteroInvalidItemReferenceError(
                "Zotero library ID must be a non-negative integer."
            )

    @staticmethod
    def _integer_header(response: httpx.Response, name: str) -> int:
        value = response.headers.get(name)
        if value is None:
            raise ZoteroUnavailableError(f"Zotero API response is missing required header {name}.")
        try:
            return int(value)
        except ValueError:
            raise ZoteroUnavailableError(
                f"Zotero API response has invalid {name} header: {value!r}."
            ) from None

    @staticmethod
    def _optional_string_header(response: httpx.Response, name: str) -> str | None:
        value = response.headers.get(name)
        return value.strip() if value and value.strip() else None
