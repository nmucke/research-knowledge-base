"""Shared test isolation for regenerable research state."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_research_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin `.research/` inside the test's tmp_path.

    The research state directory is configured independently of the vault, so it
    would otherwise default to `.research` relative to the working directory and
    let a test write into the developer's real repository.
    """
    monkeypatch.setenv("RESEARCH_STATE_PATH", str(tmp_path / ".research"))
