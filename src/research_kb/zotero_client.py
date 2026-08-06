"""Small, typed client for the local Zotero API."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Self, cast
from urllib.parse import parse_qs, unquote_to_bytes, urlsplit

import httpx

from research_kb.exceptions import (
    PDFNotFoundError,
    ZoteroAuthorizationError,
    ZoteroConflictError,
    ZoteroInvalidItemReferenceError,
    ZoteroInvalidResponseError,
    ZoteroItemNotFoundError,
    ZoteroLocalWriteUnsupportedError,
    ZoteroUnavailableError,
    ZoteroUnsupportedItemError,
)
from research_kb.models import (
    ZoteroAttachment,
    ZoteroCreator,
    ZoteroItem,
    ZoteroItemBatch,
    ZoteroTag,
)

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
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")


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
        api_key: str | None = None,
    ) -> None:
        parsed_base_url = httpx.URL(base_url)
        self._is_local_api = parsed_base_url.host in {
            "localhost",
            "127.0.0.1",
            "::1",
        }
        headers = {"Zotero-API-Version": "3"}
        if api_key is not None:
            if not api_key.strip():
                raise ZoteroAuthorizationError("A Zotero Web API key must not be blank.")
            headers["Zotero-API-Key"] = api_key
        self._client = httpx.Client(
            base_url=f"{base_url.rstrip('/')}/",
            timeout=timeout,
            transport=transport,
            headers=headers,
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

    def authorize(self, app_name: str) -> tuple[str, str]:
        """Request a local API key after confirming the connected Zotero server."""
        if not isinstance(app_name, str) or not app_name.strip():
            raise ZoteroInvalidResponseError("Zotero authorization app name must not be empty.")
        server = self.discover()
        if server.server_id is None:
            raise ZoteroLocalWriteUnsupportedError(
                "This Zotero build does not support local write authorization."
            )
        try:
            response = self._client.post(
                "local/authorize",
                headers={"Zotero-Server-ID": server.server_id},
                json={"appName": app_name},
            )
            if response.status_code in {
                httpx.codes.NOT_FOUND,
                httpx.codes.METHOD_NOT_ALLOWED,
                httpx.codes.NOT_IMPLEMENTED,
            }:
                raise ZoteroLocalWriteUnsupportedError(
                    "This Zotero build does not support local writes; no data was changed."
                )
            if response.status_code in {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}:
                raise ZoteroAuthorizationError("Zotero did not authorize this application.")
            response.raise_for_status()
        except ZoteroAuthorizationError:
            raise
        except httpx.HTTPError:
            raise ZoteroUnavailableError("Could not authorize with the local Zotero API.") from None
        try:
            payload: object = response.json()
        except ValueError as error:
            raise ZoteroInvalidResponseError(
                "Zotero returned invalid authorization JSON."
            ) from error
        data = self._mapping(payload, "authorization response")
        key = self._required_string(data, "key", "authorization response")
        return server.server_id, key

    def verify_web_api_key(
        self,
        *,
        library_type: str,
        library_id: int,
    ) -> None:
        """Verify that the configured Web API key can write the target library."""
        self._validate_library_reference(library_type, library_id)
        try:
            response = self._client.get("keys/current")
        except httpx.HTTPError:
            raise ZoteroUnavailableError("Could not verify the Zotero Web API key.") from None
        if response.status_code in {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}:
            raise ZoteroAuthorizationError(
                "The Zotero Web API key is invalid or lacks access."
            )
        try:
            response.raise_for_status()
        except httpx.HTTPError:
            raise ZoteroUnavailableError("Could not verify the Zotero Web API key.") from None
        try:
            payload: object = response.json()
        except ValueError as error:
            raise ZoteroInvalidResponseError(
                "Zotero returned invalid Web API key metadata."
            ) from error
        data = self._mapping(payload, "Web API key metadata")
        access = self._mapping(data.get("access"), "Web API key metadata.access")
        if library_type == "user":
            if data.get("userID") != library_id:
                raise ZoteroAuthorizationError(
                    "The Zotero Web API key belongs to a different user library."
                )
            permission = self._mapping(
                access.get("user"), "Web API key metadata.access.user"
            )
        else:
            groups = self._mapping(access.get("groups"), "Web API key metadata.access.groups")
            raw_permission = groups.get(str(library_id), groups.get("all"))
            permission = self._mapping(
                raw_permission, "Web API key metadata.access.groups permission"
            )
        if permission.get("library") is not True or permission.get("write") is not True:
            raise ZoteroAuthorizationError(
                "The Zotero Web API key does not have library write permission."
            )

    def patch_tags(
        self,
        zotero_key: str,
        tags: Sequence[ZoteroTag] | tuple[str, ...] | list[str],
        version: int,
        api_key: str,
        *,
        library_type: str = "user",
        library_id: int = 0,
    ) -> int:
        """Replace an item's complete tag list, using Zotero's version precondition."""
        self._validate_item_reference(zotero_key, library_type, library_id)
        if isinstance(version, bool) or not isinstance(version, int) or version < 0:
            raise ZoteroInvalidItemReferenceError(
                "Zotero item version must be a non-negative integer."
            )
        if not isinstance(api_key, str) or not api_key.strip():
            raise ZoteroAuthorizationError(
                "A Zotero authorization key is required for tag updates."
            )
        tag_payload = self._patch_tag_payload(tags)
        if tag_payload is None:
            raise ZoteroInvalidResponseError("Zotero tags must be non-empty strings.")
        path = f"{library_type}s/{library_id}/items/{zotero_key}"
        try:
            response = self._client.patch(
                path,
                headers={
                    "If-Unmodified-Since-Version": str(version),
                    "Zotero-API-Key": api_key,
                },
                json={"tags": tag_payload},
            )
        except httpx.HTTPError:
            raise ZoteroUnavailableError("Could not update tags through the Zotero API.") from None
        if response.status_code in {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}:
            raise ZoteroAuthorizationError("Zotero did not authorize the tag update.")
        if response.status_code == httpx.codes.NOT_FOUND:
            raise ZoteroItemNotFoundError(
                f"Zotero item {zotero_key!r} was not found during the tag update."
            )
        if response.status_code in {
            httpx.codes.METHOD_NOT_ALLOWED,
            httpx.codes.NOT_IMPLEMENTED,
        }:
            if self._is_local_api:
                raise ZoteroLocalWriteUnsupportedError(
                    "This Zotero build does not support local writes; no data was changed."
                )
            raise ZoteroUnavailableError(
                "The Zotero Web API rejected the tag write; no data was changed."
            )
        if response.status_code == httpx.codes.PRECONDITION_FAILED:
            raise ZoteroConflictError("Zotero item changed before the tag update could be applied.")
        try:
            response.raise_for_status()
        except httpx.HTTPError:
            raise ZoteroUnavailableError("Could not update tags through the Zotero API.") from None
        return self._last_modified_version(response)

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

    def get_child_attachments(
        self, item_key: str, *, library_type: str, library_id: int
    ) -> tuple[ZoteroAttachment, ...]:
        """Return non-deleted PDF children in Zotero's child order."""
        self._validate_item_reference(item_key, library_type, library_id)
        endpoint = f"{library_type}s/{library_id}/items/{item_key}/children"
        expected_url = self._client.build_request("GET", endpoint).url
        params = {"limit": 100, "start": 0}
        next_start: int | None = 0
        attachments: list[ZoteroAttachment] = []

        while next_start is not None:
            params["start"] = next_start
            try:
                response = self._client.get(endpoint, params=params)
                if response.status_code == httpx.codes.NOT_FOUND:
                    raise ZoteroItemNotFoundError(
                        f"Zotero item {item_key!r} was not found in "
                        f"{library_type} library {library_id}."
                    )
                response.raise_for_status()
            except ZoteroItemNotFoundError:
                raise
            except httpx.HTTPError as error:
                raise ZoteroUnavailableError(
                    f"Could not fetch child attachments for Zotero item {item_key!r}: {error}."
                ) from error

            try:
                payload: object = response.json()
            except ValueError as error:
                raise ZoteroInvalidResponseError(
                    f"Zotero returned invalid JSON for children of item {item_key!r}."
                ) from error
            for raw_child in self._list(payload, "item children"):
                attachment = self._parse_attachment(raw_child, item_key)
                if attachment is not None:
                    attachments.append(attachment)

            next_link = response.links.get("next")
            if next_link is None:
                next_start = None
            else:
                next_url = next_link.get("url")
                if not isinstance(next_url, str):
                    raise ZoteroInvalidResponseError(
                        "Zotero response has an invalid pagination Link."
                    )
                next_start = self._next_start(next_url, response.url, expected_url, next_start)

        return tuple(attachments)

    def select_pdf_attachment(
        self, item_key: str, *, library_type: str, library_id: int
    ) -> ZoteroAttachment | None:
        """Select the primary or newest remaining locally available PDF child."""
        attachments = self.get_child_attachments(
            item_key, library_type=library_type, library_id=library_id
        )
        if not attachments:
            return None

        primary, *remaining = attachments
        if self._attachment_is_available(primary, library_type, library_id):
            return primary

        available = [
            attachment
            for attachment in remaining
            if self._attachment_is_available(attachment, library_type, library_id)
        ]
        # Python's stable sort retains Zotero child order for equal timestamps.
        available.sort(
            key=lambda attachment: datetime.fromisoformat(
                attachment.date_modified.replace("Z", "+00:00")
            ),
            reverse=True,
        )
        return available[0] if available else None

    def resolve_attachment_path(
        self, attachment_key: str, *, library_type: str, library_id: int
    ) -> Path:
        """Resolve Zotero's plain-text local file URL to an existing PDF path."""
        self._validate_item_reference(attachment_key, library_type, library_id)
        endpoint = f"{library_type}s/{library_id}/items/{attachment_key}/file/view/url"
        try:
            response = self._client.get(endpoint)
            if response.status_code == httpx.codes.NOT_FOUND:
                raise PDFNotFoundError(
                    f"Zotero attachment {attachment_key!r} has no available local file."
                )
            response.raise_for_status()
        except PDFNotFoundError:
            raise
        except httpx.HTTPError as error:
            raise ZoteroUnavailableError(
                f"Could not resolve Zotero attachment {attachment_key!r}: {error}."
            ) from error

        path = self._local_file_path(response.text, attachment_key)
        if not path.exists():
            raise PDFNotFoundError(
                f"The local file for Zotero attachment {attachment_key!r} does not exist."
            )
        if not path.is_file():
            raise PDFNotFoundError(
                f"The local path for Zotero attachment {attachment_key!r} is not a file."
            )
        return path

    def list_items(
        self,
        library_type: str = "user",
        library_id: int = 0,
        *,
        since: int | None = None,
    ) -> ZoteroItemBatch:
        """List supported top-level items, following Zotero's pagination links."""
        self._validate_library_reference(library_type, library_id)
        if since is not None and (
            isinstance(since, bool) or not isinstance(since, int) or since < 0
        ):
            raise ZoteroInvalidItemReferenceError(
                "Zotero since version must be a non-negative integer."
            )

        params: dict[str, int] = {"limit": 100, "start": 0}
        if since is not None:
            params["since"] = since
            params["includeTrashed"] = 1
        endpoint = f"{library_type}s/{library_id}/items/top"
        expected_url = self._client.build_request("GET", endpoint).url
        next_start: int | None = 0
        items: list[ZoteroItem] = []
        removed_item_keys: set[str] = set()
        library_version: int | None = None

        while next_start is not None:
            params["start"] = next_start
            try:
                response = self._client.get(endpoint, params=params)
                response.raise_for_status()
            except httpx.HTTPError as error:
                raise ZoteroUnavailableError(
                    f"Could not list Zotero items: {error}. "
                    "Is Zotero running and its local API enabled?"
                ) from error

            response_version = self._last_modified_version(response)
            if library_version is None:
                library_version = response_version
            elif response_version != library_version:
                raise ZoteroInvalidResponseError(
                    "Zotero returned inconsistent Last-Modified-Version headers."
                )
            try:
                payload: object = response.json()
            except ValueError as error:
                raise ZoteroInvalidResponseError(
                    "Zotero returned invalid JSON while listing items."
                ) from error
            raw_items = self._list(payload, "items")
            for raw_item in raw_items:
                item, removed_key = self._classify_batch_item(raw_item)
                if item is not None:
                    items.append(item)
                if removed_key is not None:
                    removed_item_keys.add(removed_key)

            next_link = response.links.get("next")
            if next_link is None:
                next_start = None
            else:
                next_url = next_link.get("url")
                if not isinstance(next_url, str):
                    raise ZoteroInvalidResponseError(
                        "Zotero response has an invalid pagination Link."
                    )
                next_start = self._next_start(next_url, response.url, expected_url, next_start)

        # A successful Zotero response always has the required version header.
        assert library_version is not None
        if since is not None:
            removed_item_keys.update(
                self._list_removed_item_keys(library_type, library_id, since, library_version)
            )
        return ZoteroItemBatch(
            items=tuple(items),
            library_version=library_version,
            removed_item_keys=tuple(sorted(removed_item_keys)),
        )

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
            tag_entries=cls._tag_entries(data.get("tags")),
            volume=cls._optional_string(data, "volume", "item.data"),
            issue=cls._optional_string(data, "issue", "item.data"),
            pages=cls._optional_string(data, "pages", "item.data"),
            collections=cls._collections(data.get("collections")),
            date_added=cls._optional_timestamp(data, "dateAdded", "item.data"),
            date_modified=cls._optional_timestamp(data, "dateModified", "item.data"),
        )

    @classmethod
    def _parse_attachment(cls, payload: object, expected_parent: str) -> ZoteroAttachment | None:
        """Validate an attachment child, excluding notes and unusable PDF metadata."""
        item = cls._mapping(payload, "child item")
        data = cls._mapping(item.get("data"), "child item.data")
        item_type = cls._required_string(data, "itemType", "child item.data")
        if item_type != "attachment":
            return None

        deleted = data.get("deleted")
        if deleted is not None and not isinstance(deleted, bool):
            raise ZoteroInvalidResponseError("Zotero response has invalid child item.data.deleted.")
        content_type = cls._optional_string(data, "contentType", "child item.data")
        if deleted or content_type != "application/pdf":
            return None

        parent_item = cls._required_string(data, "parentItem", "child item.data")
        if not _ITEM_KEY_PATTERN.fullmatch(parent_item) or parent_item != expected_parent:
            raise ZoteroInvalidResponseError(
                "Zotero response has invalid child item.data.parentItem."
            )
        version = cls._required_integer(item, "version", "child item")
        if version < 0:
            raise ZoteroInvalidResponseError("Zotero response has invalid child item.version.")
        mtime = data.get("mtime")
        if mtime is not None and (
            isinstance(mtime, bool) or not isinstance(mtime, int) or mtime < 0
        ):
            raise ZoteroInvalidResponseError("Zotero response has invalid child item.data.mtime.")
        date_modified = cls._optional_timestamp(data, "dateModified", "child item.data")
        if (
            date_modified is None
            or "T" not in date_modified
            or datetime.fromisoformat(date_modified.replace("Z", "+00:00")).tzinfo is None
        ):
            raise ZoteroInvalidResponseError(
                "Zotero response has invalid child item.data.dateModified."
            )

        return ZoteroAttachment(
            key=cls._response_item_key(item),
            version=version,
            parent_item=parent_item,
            content_type="application/pdf",
            filename=cls._optional_string(data, "filename", "child item.data"),
            link_mode=cls._required_string(data, "linkMode", "child item.data"),
            title=cls._optional_string(data, "title", "child item.data"),
            date_modified=date_modified,
            mtime=mtime,
        )

    @classmethod
    def _classify_batch_item(cls, payload: object) -> tuple[ZoteroItem | None, str | None]:
        """Identify valid objects that are intentionally outside paper syncing."""
        item = cls._mapping(payload, "item")
        data = cls._mapping(item.get("data"), "item.data")
        item_type = cls._required_string(data, "itemType", "item.data")
        if item_type not in SUPPORTED_ITEM_TYPES:
            return None, None
        parent_item = data.get("parentItem")
        if parent_item is not None and not isinstance(parent_item, str):
            raise ZoteroInvalidResponseError("Zotero response has invalid item.data.parentItem.")
        if parent_item:
            return None, None
        library = cls._mapping(item.get("library"), "item.library")
        if cls._optional_string(library, "type", "item.library") == "feed":
            return None, None
        deleted = data.get("deleted")
        if deleted is not None and not isinstance(deleted, bool):
            raise ZoteroInvalidResponseError("Zotero response has invalid item.data.deleted.")
        if deleted:
            return None, cls._response_item_key(item)
        return cls._parse_item(item), None

    def _list_removed_item_keys(
        self, library_type: str, library_id: int, since: int, expected_version: int
    ) -> tuple[str, ...]:
        """Read tombstones Zotero does not include in the normal item listing."""
        path = f"{library_type}s/{library_id}/deleted"
        try:
            response = self._client.get(path, params={"since": since})
            # Zotero's local API supports ``since`` and ``includeTrashed`` on
            # item reads, but some releases do not expose the Web API's
            # deletion-log endpoint. Trashed items are still reported by the
            # main incremental read; an explicit full sync reconciles items
            # permanently erased before they could be observed in the trash.
            if response.status_code == httpx.codes.NOT_FOUND:
                return ()
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise ZoteroUnavailableError(
                f"Could not list deleted Zotero items: {error}. "
                "Is Zotero running and its local API enabled?"
            ) from error
        if self._last_modified_version(response) != expected_version:
            raise ZoteroInvalidResponseError(
                "Zotero returned inconsistent Last-Modified-Version headers."
            )
        try:
            payload: object = response.json()
        except ValueError as error:
            raise ZoteroInvalidResponseError(
                "Zotero returned invalid JSON while listing deleted items."
            ) from error
        deleted = self._mapping(payload, "deleted items")
        raw_keys = self._list(deleted.get("items"), "deleted items.items")
        keys: list[str] = []
        for raw_key in raw_keys:
            if not isinstance(raw_key, str) or not _ITEM_KEY_PATTERN.fullmatch(raw_key):
                raise ZoteroInvalidResponseError(
                    "Zotero response has invalid deleted items.items entry."
                )
            keys.append(raw_key)
        return tuple(keys)

    def _attachment_is_available(
        self, attachment: ZoteroAttachment, library_type: str, library_id: int
    ) -> bool:
        """Check local availability without hiding connectivity failures."""
        try:
            self.resolve_attachment_path(
                attachment.key, library_type=library_type, library_id=library_id
            )
        except PDFNotFoundError:
            return False
        return True

    @staticmethod
    def _local_file_path(value: str, attachment_key: str) -> Path:
        """Parse a safe absolute local path from Zotero's file URL response."""
        raw_url = value.strip()
        if not raw_url or _INVALID_PERCENT_ESCAPE.search(raw_url):
            raise PDFNotFoundError(
                f"Zotero returned a malformed file URL for attachment {attachment_key!r}."
            )
        try:
            parsed = urlsplit(raw_url)
        except ValueError:
            raise PDFNotFoundError(
                f"Zotero returned a malformed file URL for attachment {attachment_key!r}."
            ) from None
        if (
            parsed.scheme.lower() != "file"
            or parsed.netloc not in {"", "localhost"}
            or parsed.query
            or parsed.fragment
        ):
            raise PDFNotFoundError(
                f"Zotero attachment {attachment_key!r} does not resolve to a local file URL."
            )
        try:
            decoded = unquote_to_bytes(parsed.path).decode("utf-8")
        except UnicodeDecodeError:
            raise PDFNotFoundError(
                f"Zotero returned a malformed file URL for attachment {attachment_key!r}."
            ) from None
        if not decoded or "\x00" in decoded or decoded.startswith("//"):
            raise PDFNotFoundError(
                f"Zotero attachment {attachment_key!r} resolves to an invalid local path."
            )
        path = Path(decoded)
        if not path.is_absolute():
            raise PDFNotFoundError(
                f"Zotero attachment {attachment_key!r} resolves to an invalid local path."
            )
        return path

    @staticmethod
    def _next_start(
        link: str, response_url: httpx.URL, expected_url: httpx.URL, current_start: int
    ) -> int:
        """Accept only a same-endpoint Link cursor and regenerate its parameters locally."""
        resolved = response_url.join(link)
        if (
            resolved.scheme != expected_url.scheme
            or resolved.host != expected_url.host
            or resolved.port != expected_url.port
            or resolved.path != expected_url.path
        ):
            raise ZoteroInvalidResponseError("Zotero response has an invalid pagination Link.")
        parsed = urlsplit(str(resolved))
        starts = parse_qs(parsed.query, keep_blank_values=True).get("start", [])
        if len(starts) != 1 or not starts[0].isdigit():
            raise ZoteroInvalidResponseError("Zotero response has an invalid pagination Link.")
        start = int(starts[0])
        if start <= current_start:
            raise ZoteroInvalidResponseError(
                "Zotero response has a non-increasing pagination Link."
            )
        return start

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
        return tuple(entry.tag for entry in cls._tag_entries(value))

    @classmethod
    def _tag_entries(cls, value: object) -> tuple[ZoteroTag, ...]:
        if value is None:
            return ()
        tags = cls._list(value, "item.data.tags")
        result: list[ZoteroTag] = []
        for tag in tags:
            entry = cls._mapping(tag, "item.data.tags entry")
            tag_type = entry.get("type", 0)
            if tag_type not in {None, 0, 1} or isinstance(tag_type, bool):
                raise ZoteroInvalidResponseError("Zotero response has invalid tag.type.")
            result.append(
                ZoteroTag(
                    tag=cls._required_string(entry, "tag", "tag"),
                    type=tag_type,
                )
            )
        return tuple(result)

    @staticmethod
    def _patch_tag_payload(
        tags: Sequence[ZoteroTag] | tuple[str, ...] | list[str],
    ) -> list[dict[str, str | int]] | None:
        if not isinstance(tags, (tuple, list)):
            return None
        payload: list[dict[str, str | int]] = []
        for entry in tags:
            if isinstance(entry, ZoteroTag):
                if not entry.tag.strip():
                    return None
                rendered: dict[str, str | int] = {"tag": entry.tag}
                if entry.type is not None:
                    rendered["type"] = entry.type
                payload.append(rendered)
            elif isinstance(entry, str) and entry.strip():
                payload.append({"tag": entry})
            else:
                return None
        return payload

    @classmethod
    def _collections(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        collections = cls._list(value, "item.data.collections")
        return tuple(
            cls._required_string({"collection": collection}, "collection", "item.data.collections")
            for collection in collections
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

    @classmethod
    def _optional_timestamp(
        cls, data: Mapping[str, object], field: str, location: str
    ) -> str | None:
        value = cls._optional_string(data, field, location)
        if value is None:
            return None
        try:
            if "T" in value:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            else:
                date.fromisoformat(value)
        except ValueError:
            raise ZoteroInvalidResponseError(
                f"Zotero response has invalid {location}.{field}."
            ) from None
        return value

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
        ZoteroClient._validate_library_reference(library_type, library_id)

    @staticmethod
    def _validate_library_reference(library_type: str, library_id: int) -> None:
        if library_type not in {"user", "group"}:
            raise ZoteroInvalidItemReferenceError("Zotero library type must be 'user' or 'group'.")
        if isinstance(library_id, bool) or not isinstance(library_id, int) or library_id < 0:
            raise ZoteroInvalidItemReferenceError(
                "Zotero library ID must be a non-negative integer."
            )

    @staticmethod
    def _last_modified_version(response: httpx.Response) -> int:
        value = response.headers.get("Last-Modified-Version")
        if value is None:
            raise ZoteroInvalidResponseError(
                "Zotero API response is missing required header Last-Modified-Version."
            )
        try:
            version = int(value)
        except ValueError:
            raise ZoteroInvalidResponseError(
                "Zotero API response has invalid Last-Modified-Version header."
            ) from None
        if version < 0:
            raise ZoteroInvalidResponseError(
                "Zotero API response has invalid Last-Modified-Version header."
            )
        return version

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
