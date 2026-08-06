"""Tests for private local Zotero credential storage."""

import json
import stat

import pytest

from research_kb.credential_store import CredentialStore
from research_kb.exceptions import CredentialStoreError


def test_save_is_mode_0600_and_keyed_by_server_id(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / ".research" / "credentials.json"
    store = CredentialStore(path)

    store.save("server-a", "first-secret")
    store.save("server-b", "second-secret")

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == {
        "schema_version": 1,
        "zotero_server_keys": {"server-a": "first-secret", "server-b": "second-secret"},
    }
    assert store.get("server-a") == "first-secret"


def test_rejects_malformed_credentials_without_exposing_key(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "credentials.json"
    path.write_text('{"zotero_server_keys":{"server":"never-show-this"}}')

    with pytest.raises(CredentialStoreError) as error:
        CredentialStore(path).get("server")

    assert "never-show-this" not in str(error.value)


def test_rejects_symlink_credentials_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    target = tmp_path / "target.json"
    target.write_text("{}")
    path = tmp_path / "credentials.json"
    path.symlink_to(target)

    with pytest.raises(CredentialStoreError, match="regular file"):
        CredentialStore(path).get("server")


def test_rejects_symlink_credentials_directory(tmp_path) -> None:  # type: ignore[no-untyped-def]
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / ".research"
    linked.symlink_to(real, target_is_directory=True)

    with pytest.raises(CredentialStoreError, match="directory must not be a symlink"):
        CredentialStore(linked / "credentials.json").save("server", "secret")

    assert not (real / "credentials.json").exists()


def test_rejects_non_utf8_or_overly_permissive_credentials(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "credentials.json"
    path.write_bytes(b"\xff\xfe")
    path.chmod(0o600)

    with pytest.raises(CredentialStoreError, match="Could not read"):
        CredentialStore(path).get("server")

    path.write_text('{"schema_version":1,"zotero_server_keys":{"server":"secret"}}')
    path.chmod(0o644)
    with pytest.raises(CredentialStoreError, match="only by their owner"):
        CredentialStore(path).get("server")
