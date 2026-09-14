"""Public transaction API for vault mutation services."""

from research_kb.operation_transaction import (
    FileDiff,
    PlannedFileChange,
    TransactionResult,
    WorkspaceTransactionStore,
)

VaultTransaction = WorkspaceTransactionStore

__all__ = (
    "FileDiff",
    "PlannedFileChange",
    "TransactionResult",
    "VaultTransaction",
)
