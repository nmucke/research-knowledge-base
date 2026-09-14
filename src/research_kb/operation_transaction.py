"""Recoverable compare-and-swap transactions for bounded workspace files."""

from __future__ import annotations

import difflib
import fcntl
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from research_kb.exceptions import ValidationError


@dataclass(frozen=True)
class PlannedFileChange:
    path: Path
    before: str | None
    after: str | None

    @property
    def expected_revision(self) -> str | None:
        return _revision(self.before) if self.before is not None else None


@dataclass(frozen=True)
class FileDiff:
    path: str
    before_revision: str | None
    after_revision: str | None
    unified_diff: str


@dataclass(frozen=True)
class TransactionResult:
    operation_id: str
    files: tuple[FileDiff, ...]
    created_at: datetime


class WorkspaceTransactionStore:
    """Apply an all-file plan under one workspace with durable rollback state.

    The lock coordinates this software's processes. External editors do not honor it;
    per-file revision checks narrow that race but cannot provide an OS-wide transaction.
    """

    def __init__(self, workspace: Path, recovery_dir: Path) -> None:
        self.workspace = _safe_directory(workspace, workspace, create=False)
        self.recovery_dir = _safe_directory(recovery_dir, workspace, create=True)

    def preview(self, changes: tuple[PlannedFileChange, ...]) -> tuple[FileDiff, ...]:
        self._preflight(changes)
        return tuple(self._diff(change) for change in changes if change.before != change.after)

    def apply(
        self,
        changes: tuple[PlannedFileChange, ...],
        *,
        validate: Callable[[tuple[PlannedFileChange, ...]], None] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> TransactionResult:
        with self._lock():
            files = self.preview(changes)
            effective = tuple(change for change in changes if change.before != change.after)
            if validate is not None:
                validate(effective)
            # Validation may take time, so repeat every revision check immediately before WAL/write.
            self._preflight(effective)
            operation_id = uuid4().hex
            journal = self.recovery_dir / f"{operation_id}.json"
            payload = {
                "status": "prepared",
                "created_at": datetime.now().astimezone().isoformat(),
                "metadata": metadata or {},
                "files": [
                    {
                        "path": str(change.path.relative_to(self.workspace)),
                        "before": change.before,
                        "after": change.after,
                        "before_revision": change.expected_revision,
                        "after_revision": (
                            _revision(change.after) if change.after is not None else None
                        ),
                    }
                    for change in effective
                ],
            }
            _atomic_json(journal, payload)
            written: list[PlannedFileChange] = []
            try:
                for change in effective:
                    _commit(change.path, change.after, change.expected_revision)
                    written.append(change)
            except Exception:
                try:
                    for change in reversed(written):
                        expected = _revision(change.after) if change.after is not None else None
                        _commit(change.path, change.before, expected)
                except Exception:
                    # Keep prepared so explicit recovery can finish the rollback.
                    raise
                payload["status"] = "rolled-back"
                _atomic_json(journal, payload)
                raise
            payload["status"] = "completed"
            _atomic_json(journal, payload)
            return TransactionResult(operation_id, files, datetime.now().astimezone())

    def undo(self, operation_id: str) -> TransactionResult:
        path, raw = self._journal(operation_id)
        if raw.get("status") != "completed":
            raise ValidationError("only a completed operation can be undone")
        items = cast(list[dict[str, Any]], raw["files"])
        changes = tuple(
            PlannedFileChange(
                path=self._resolve_relative(item["path"]),
                before=item["after"],
                after=item["before"],
            )
            for item in items
        )
        # preview preflights every file before undo writes the first one.
        result = self.apply(changes)
        raw["status"] = "undone"
        raw["undo_operation_id"] = result.operation_id
        _atomic_json(path, raw)
        return result

    def recover(self, operation_id: str) -> TransactionResult | None:
        path, raw = self._journal(operation_id)
        if raw.get("status") != "prepared":
            return None
        changes: list[PlannedFileChange] = []
        for item in cast(list[dict[str, Any]], raw["files"]):
            target = self._resolve_relative(item["path"])
            current = target.read_text(encoding="utf-8") if target.exists() else None
            current_revision = _revision(current) if current is not None else None
            if current_revision == item["before_revision"]:
                continue
            if current_revision != item["after_revision"]:
                raise ValidationError(f"cannot recover {target}: contents diverged")
            changes.append(PlannedFileChange(target, current, item["before"]))
        result = self.apply(tuple(changes))
        raw["status"] = "recovered"
        raw["recovery_operation_id"] = result.operation_id
        _atomic_json(path, raw)
        return result

    def pending(self) -> tuple[str, ...]:
        found = []
        for path in sorted(self.recovery_dir.glob("*.json")):
            self._require_safe_journal(path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("status") == "prepared":
                found.append(path.stem)
        return tuple(found)

    def completed(self) -> tuple[str, ...]:
        found = []
        for path in sorted(self.recovery_dir.glob("*.json")):
            self._require_safe_journal(path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("status") == "completed":
                found.append(path.stem)
        return tuple(found)

    def result(self, operation_id: str) -> TransactionResult:
        _path, raw = self._journal(operation_id)
        if raw.get("status") not in {"completed", "undone"}:
            raise ValidationError("operation has no completed result")
        created_at = datetime.fromisoformat(str(raw["created_at"]))
        files = tuple(
            FileDiff(
                path=str(item["path"]),
                before_revision=item.get("before_revision"),
                after_revision=item.get("after_revision"),
                unified_diff=self._diff(
                    PlannedFileChange(
                        self._resolve_relative(str(item["path"])),
                        item.get("before"),
                        item.get("after"),
                    )
                ).unified_diff,
            )
            for item in cast(list[dict[str, Any]], raw["files"])
        )
        return TransactionResult(operation_id, files, created_at)

    def metadata(self, operation_id: str) -> dict[str, object]:
        _path, raw = self._journal(operation_id)
        value = raw.get("metadata", {})
        if not isinstance(value, dict):
            raise ValidationError("operation metadata is invalid")
        return value

    def _preflight(self, changes: tuple[PlannedFileChange, ...]) -> None:
        paths = [change.path for change in changes]
        if len(paths) != len(set(paths)):
            raise ValidationError("transaction contains a path more than once")
        for change in changes:
            target = self._resolve(change.path)
            if target != change.path.resolve() or change.path.is_symlink():
                raise ValidationError(f"unsafe transaction target: {change.path}")
            current = change.path.read_text(encoding="utf-8") if change.path.exists() else None
            if current != change.before:
                raise ValidationError(f"revision conflict for {change.path}")
            if change.before is not None and not change.path.is_file():
                raise ValidationError(f"unsafe transaction target: {change.path}")

    def _resolve(self, path: Path) -> Path:
        _reject_symlink_components(path, self.workspace)
        resolved = path.resolve()
        if not resolved.is_relative_to(self.workspace):
            raise ValidationError(f"transaction path is outside workspace: {path}")
        return resolved

    def _resolve_relative(self, value: str) -> Path:
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValidationError("unsafe path in recovery journal")
        return self._resolve(self.workspace / relative)

    def _journal(self, operation_id: str) -> tuple[Path, dict[str, object]]:
        if len(operation_id) != 32 or any(char not in "0123456789abcdef" for char in operation_id):
            raise ValidationError("invalid operation identifier")
        path = self.recovery_dir / f"{operation_id}.json"
        self._require_safe_journal(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValidationError(f"invalid recovery journal {path}: {error}") from error
        if not isinstance(raw, dict) or not isinstance(raw.get("files"), list):
            raise ValidationError(f"invalid recovery journal {path}")
        return path, raw

    def _require_safe_journal(self, path: Path) -> None:
        if path.is_symlink() or path.resolve().parent != self.recovery_dir:
            raise ValidationError(f"unsafe recovery journal: {path}")

    def _lock(self) -> _WorkspaceLock:
        lock = self.recovery_dir / ".lock"
        if lock.is_symlink():
            raise ValidationError(f"unsafe workspace lock: {lock}")
        return _WorkspaceLock(lock)

    def _diff(self, change: PlannedFileChange) -> FileDiff:
        relative = str(change.path.resolve().relative_to(self.workspace))
        diff = "".join(
            difflib.unified_diff(
                (change.before or "").splitlines(keepends=True),
                (change.after or "").splitlines(keepends=True),
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
            )
        )
        after_revision = _revision(change.after) if change.after is not None else None
        return FileDiff(relative, change.expected_revision, after_revision, diff)


def _safe_directory(path: Path, workspace: Path, *, create: bool) -> Path:
    if workspace.is_symlink():
        raise ValidationError(f"unsafe symlinked workspace: {workspace}")
    root = workspace.resolve()
    resolved = path.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise ValidationError(f"directory is outside workspace: {path}")
    relative = path.absolute().relative_to(workspace.absolute())
    cursor = workspace.absolute()
    for part in relative.parts:
        cursor /= part
        if cursor.exists() and cursor.is_symlink():
            raise ValidationError(f"unsafe symlinked directory: {cursor}")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_dir():
        raise ValidationError(f"directory is outside workspace: {path}")
    return resolved


def _reject_symlink_components(path: Path, workspace: Path) -> None:
    try:
        relative = path.absolute().relative_to(workspace.absolute())
    except ValueError as error:
        raise ValidationError(f"path is outside workspace: {path}") from error
    cursor = workspace.absolute()
    for part in relative.parts:
        cursor /= part
        if cursor.exists() and cursor.is_symlink():
            raise ValidationError(f"unsafe symlink in transaction path: {cursor}")


class _WorkspaceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any = None

    def __enter__(self) -> _WorkspaceLock:
        self.handle = self.path.open("a+", encoding="utf-8")
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *_args: object) -> None:
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()


def _revision(content: str) -> str:
    return sha256(content.encode("utf-8")).hexdigest()


def _commit(path: Path, content: str | None, expected: str | None) -> None:
    current = path.read_text(encoding="utf-8") if path.exists() else None
    actual = _revision(current) if current is not None else None
    if actual != expected:
        raise ValidationError(f"revision conflict for {path}")
    if content is None:
        path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if expected is None:
            try:
                os.link(temporary, path)
            except FileExistsError as error:
                raise ValidationError(f"revision conflict for {path}") from error
        else:
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json(path: Path, value: object) -> None:
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
