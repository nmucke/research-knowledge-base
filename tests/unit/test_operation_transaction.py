from pathlib import Path

import pytest

from research_kb.exceptions import ValidationError
from research_kb.operation_transaction import PlannedFileChange, WorkspaceTransactionStore


def test_transaction_previews_applies_and_undoes_all_files(tmp_path: Path) -> None:
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("one\n", encoding="utf-8")
    second.write_text("two\n", encoding="utf-8")
    store = WorkspaceTransactionStore(tmp_path, tmp_path / ".research" / "recovery")
    changes = (
        PlannedFileChange(first, "one\n", "changed one\n"),
        PlannedFileChange(second, "two\n", "changed two\n"),
    )
    preview = store.preview(changes)
    assert preview[0].path == "first.md"
    assert "-one" in preview[0].unified_diff
    result = store.apply(changes)
    assert first.read_text(encoding="utf-8") == "changed one\n"
    assert second.read_text(encoding="utf-8") == "changed two\n"
    store.undo(result.operation_id)
    assert first.read_text(encoding="utf-8") == "one\n"
    assert second.read_text(encoding="utf-8") == "two\n"


def test_transaction_preflights_every_file_before_writing(tmp_path: Path) -> None:
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("one", encoding="utf-8")
    second.write_text("concurrent", encoding="utf-8")
    store = WorkspaceTransactionStore(tmp_path, tmp_path / ".research" / "recovery")
    with pytest.raises(ValidationError, match="revision conflict"):
        store.apply(
            (
                PlannedFileChange(first, "one", "changed"),
                PlannedFileChange(second, "two", "changed"),
            )
        )
    assert first.read_text(encoding="utf-8") == "one"


def test_injected_second_write_failure_rolls_back_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")
    store = WorkspaceTransactionStore(tmp_path, tmp_path / ".research" / "recovery")
    import research_kb.operation_transaction as module

    real_commit = module._commit
    calls = 0

    def failing_commit(path: Path, content: str | None, expected: str | None) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected")
        real_commit(path, content, expected)

    monkeypatch.setattr(module, "_commit", failing_commit)
    with pytest.raises(OSError, match="injected"):
        store.apply(
            (
                PlannedFileChange(first, "one", "changed one"),
                PlannedFileChange(second, "two", "changed two"),
            )
        )
    assert first.read_text(encoding="utf-8") == "one"
    assert second.read_text(encoding="utf-8") == "two"
    assert store.pending() == ()


def test_undo_preflights_all_files_before_restoring_any(tmp_path: Path) -> None:
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")
    store = WorkspaceTransactionStore(tmp_path, tmp_path / ".research" / "recovery")
    result = store.apply(
        (
            PlannedFileChange(first, "one", "changed one"),
            PlannedFileChange(second, "two", "changed two"),
        )
    )
    second.write_text("concurrent", encoding="utf-8")
    with pytest.raises(ValidationError, match="revision conflict"):
        store.undo(result.operation_id)
    assert first.read_text(encoding="utf-8") == "changed one"
