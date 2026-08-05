"""Tests for file-based diagnostic logging."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from research_kb.config import Settings
from research_kb.logging_config import configure_logging


def test_logging_creates_rebuildable_log_file(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, research_vault_path=tmp_path)
    logger = configure_logging(settings)

    logger.info("step-one logging smoke test")
    for handler in logger.handlers:
        handler.flush()

    log_path = tmp_path / ".research" / "logs" / "research.log"
    assert log_path.is_file()
    assert "step-one logging smoke test" in log_path.read_text(encoding="utf-8")

    for handler in logger.handlers:
        handler.close()
    logging.shutdown()


def test_reconfiguration_closes_replaced_file_handler(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, research_vault_path=tmp_path)
    logger = configure_logging(settings)
    old_file_handler = next(
        handler for handler in logger.handlers if isinstance(handler, RotatingFileHandler)
    )

    configure_logging(settings)

    assert old_file_handler.stream is None
