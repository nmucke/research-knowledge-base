"""Client for Better BibTeX's local JSON-RPC endpoint."""

import re
from typing import Any, Self

import httpx

from research_kb.exceptions import BetterBibTeXUnavailableError, CitationKeyMissingError

_UNAVAILABLE_MESSAGE = (
    "Better BibTeX is unavailable. Install or enable Better BibTeX and restart Zotero."
)
_READY_REQUEST_ID = "research-kb-ready"
_CITATION_KEY_REQUEST_ID = "research-kb-citation-key"
_ITEM_KEY_PATTERN = re.compile(r"^[A-Z0-9]{8}$")
_CITATION_KEY_MISSING_MESSAGE = (
    "Better BibTeX has no citation key for Zotero item {item_key!r}. "
    "Generate or refresh the citation key in Better BibTeX."
)


class BetterBibTeXClient:
    """Minimal JSON-RPC client, ready to grow with Better BibTeX item methods."""

    def __init__(
        self,
        rpc_url: str,
        *,
        timeout: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(timeout=timeout, transport=transport)
        self._rpc_url = rpc_url

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Release the underlying HTTP resources."""
        self._client.close()

    def check_ready(self) -> None:
        """Confirm that Better BibTeX accepts JSON-RPC requests."""
        result = self._call("api.ready", [], _READY_REQUEST_ID)

        if result is False:
            raise BetterBibTeXUnavailableError(_UNAVAILABLE_MESSAGE)

    def get_citation_key(self, item_key: str, *, library_id: int = 0) -> str:
        """Return Better BibTeX's citation key for one Zotero item."""
        self._validate_item_reference(item_key, library_id)
        zotero_key = item_key if library_id == 0 else f"{library_id}:{item_key}"
        result = self._call(
            "item.citationkey", [[zotero_key]], _CITATION_KEY_REQUEST_ID
        )

        citation_key = result.get(zotero_key) if isinstance(result, dict) else None
        if not isinstance(citation_key, str) or not citation_key:
            raise CitationKeyMissingError(
                _CITATION_KEY_MISSING_MESSAGE.format(item_key=item_key)
            )
        return citation_key

    def _call(self, method: str, params: list[Any], request_id: str) -> Any:
        """Make one JSON-RPC request and return its successful result."""
        try:
            response = self._client.post(
                self._rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": params,
                    "id": request_id,
                },
            )
            response.raise_for_status()
            payload: Any = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise BetterBibTeXUnavailableError(_UNAVAILABLE_MESSAGE) from error

        if not (
            isinstance(payload, dict)
            and payload.get("jsonrpc") == "2.0"
            and payload.get("id") == request_id
            and "result" in payload
            and payload.get("error") is None
        ):
            raise BetterBibTeXUnavailableError(_UNAVAILABLE_MESSAGE)
        return payload["result"]

    @staticmethod
    def _validate_item_reference(item_key: str, library_id: int) -> None:
        if not isinstance(item_key, str) or not _ITEM_KEY_PATTERN.fullmatch(item_key):
            raise ValueError("item_key must be an 8-character Zotero item key")
        if isinstance(library_id, bool) or not isinstance(library_id, int) or library_id < 0:
            raise ValueError("library_id must be a non-negative integer")
