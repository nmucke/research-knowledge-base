"""Private, local storage for Zotero authorization keys."""

import json
import os
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from research_kb.exceptions import CredentialStoreError

_FORMAT_VERSION: Final = 1


class CredentialStore:
    """Store Zotero API keys by the local Zotero server identifier.

    The file contains no bibliographic data.  Writes use a same-directory
    temporary file and replacement so a crash cannot leave a partial JSON
    credentials file behind.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def get(self, server_id: str) -> str | None:
        """Return the key remembered for *server_id*, if any."""
        self._validate_server_id(server_id)
        if not self._path.exists() and not self._path.is_symlink():
            return None
        data = self._read()
        return data[server_id] if server_id in data else None

    def save(self, server_id: str, key: str) -> None:
        """Atomically remember a key for one local Zotero server."""
        self._validate_server_id(server_id)
        self._validate_key(key)
        data = self._read() if self._path.exists() or self._path.is_symlink() else {}
        data[server_id] = key
        self._write(data)

    # These names make the intent at call sites explicit while retaining the
    # short mapping-like operations above.
    def get_key(self, server_id: str) -> str | None:
        return self.get(server_id)

    def save_key(self, server_id: str, key: str) -> None:
        self.save(server_id, key)

    def _read(self) -> dict[str, str]:
        try:
            self._validate_parent()
            mode = self._path.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise CredentialStoreError("Local Zotero credentials are not a regular file.")
            if stat.S_IMODE(mode) & 0o077:
                raise CredentialStoreError(
                    "Local Zotero credentials must be readable and writable only by their owner."
                )
            raw: object = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise CredentialStoreError("Could not read local Zotero credentials.") from error
        if not isinstance(raw, Mapping) or set(raw) != {"schema_version", "zotero_server_keys"}:
            raise CredentialStoreError("Local Zotero credentials have an invalid format.")
        if raw.get("schema_version") != _FORMAT_VERSION or not isinstance(
            raw.get("zotero_server_keys"), Mapping
        ):
            raise CredentialStoreError("Local Zotero credentials have an invalid format.")
        keys = raw["zotero_server_keys"]
        assert isinstance(keys, Mapping)
        if not all(
            isinstance(server_id, str)
            and self._is_nonempty(server_id)
            and isinstance(key, str)
            and self._is_nonempty(key)
            for server_id, key in keys.items()
        ):
            raise CredentialStoreError("Local Zotero credentials have an invalid format.")
        return dict(keys)

    def _write(self, keys: Mapping[str, str]) -> None:
        try:
            self._validate_parent()
            self._path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temp_name = tempfile.mkstemp(
                prefix=f".{self._path.name}.", dir=self._path.parent
            )
            temp_path = Path(temp_name)
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(
                        {"schema_version": _FORMAT_VERSION, "zotero_server_keys": dict(keys)},
                        stream,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_path, self._path)
                os.chmod(self._path, 0o600)
            except BaseException:
                temp_path.unlink(missing_ok=True)
                raise
        except OSError as error:
            raise CredentialStoreError("Could not write local Zotero credentials.") from error

    def _validate_parent(self) -> None:
        """Reject a redirected credentials directory before reading or writing secrets."""
        parent = self._path.parent
        if parent.is_symlink():
            raise CredentialStoreError("Local Zotero credentials directory must not be a symlink.")

    @staticmethod
    def _is_nonempty(value: str) -> bool:
        return bool(value.strip()) and "\x00" not in value

    @classmethod
    def _validate_server_id(cls, server_id: str) -> None:
        if not isinstance(server_id, str) or not cls._is_nonempty(server_id):
            raise CredentialStoreError("Zotero server ID is invalid.")

    @classmethod
    def _validate_key(cls, key: str) -> None:
        if not isinstance(key, str) or not cls._is_nonempty(key):
            raise CredentialStoreError("Zotero authorization key is invalid.")
