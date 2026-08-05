"""Small, typed client for the local Zotero API.

Item-read methods will be added here in a later step.
"""

from dataclasses import dataclass
from typing import Self

import httpx

from research_kb.exceptions import ZoteroUnavailableError


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
