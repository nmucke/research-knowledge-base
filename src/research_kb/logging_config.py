"""Logging configuration for CLI commands."""

import logging
from logging.handlers import RotatingFileHandler

from rich.console import Console
from rich.logging import RichHandler

from research_kb.config import Settings

LOGGER_NAME = "research_kb"


def configure_logging(
    settings: Settings, *, verbose: bool = False, persist: bool = True
) -> logging.Logger:
    """Configure concise console logging and a rotating diagnostic log file."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    logger.propagate = False

    if persist:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            settings.log_dir / "research.log",
            maxBytes=2_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(settings.research_log_level)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        logger.addHandler(file_handler)

    console_handler = RichHandler(
        console=Console(stderr=True),
        show_path=verbose,
        rich_tracebacks=verbose,
        markup=True,
    )
    # Commands render their own errors. Keep stdout reserved for JSON/MCP.
    console_handler.setLevel(logging.DEBUG if verbose else logging.CRITICAL + 1)
    logger.addHandler(console_handler)

    return logger
