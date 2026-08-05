"""Client for Better BibTeX's local JSON-RPC endpoint."""

from typing import Any, Self

import httpx

from research_kb.exceptions import BetterBibTeXUnavailableError

_UNAVAILABLE_MESSAGE = (
    "Better BibTeX is unavailable. Install or enable Better BibTeX and restart Zotero."
)
_READY_REQUEST_ID = "research-kb-ready"


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
        try:
            response = self._client.post(
                self._rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "method": "api.ready",
                    "params": [],
                    "id": _READY_REQUEST_ID,
                },
            )
            response.raise_for_status()
            payload: Any = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise BetterBibTeXUnavailableError(_UNAVAILABLE_MESSAGE) from error

        if not self._is_ready_response(payload):
            raise BetterBibTeXUnavailableError(_UNAVAILABLE_MESSAGE)

    @staticmethod
    def _is_ready_response(payload: Any) -> bool:
        return (
            isinstance(payload, dict)
            and payload.get("jsonrpc") == "2.0"
            and payload.get("id") == _READY_REQUEST_ID
            and "result" in payload
            and payload.get("error") is None
            and payload["result"] is not False
        )
