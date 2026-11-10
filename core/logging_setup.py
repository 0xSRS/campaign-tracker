"""Centralized logging configuration.

Logs go to a rotating file only. Printing to the console would corrupt
the Textual interface, which owns the terminal while the app runs.
"""

import logging
from logging.handlers import RotatingFileHandler

from core.config import Settings, get_settings

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_MAX_BYTES = 1_000_000   # rotate at ~1 MB
_BACKUP_COUNT = 3        # keep tracker.log plus 3 older files


def setup_logging(settings: Settings | None = None) -> None:
    """Configure the root logger. Safe to call more than once."""
    settings = settings or get_settings()
    settings.ensure_directories()

    root = logging.getLogger()
    root.setLevel(settings.log_level)

    # Remove handlers from any earlier call so we never log twice.
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    file_handler = RotatingFileHandler(
        settings.log_file,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    root.addHandler(file_handler)

    # Keep third-party libraries quiet unless we are debugging.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger(__name__).info("Logging initialised (level=%s)", settings.log_level)