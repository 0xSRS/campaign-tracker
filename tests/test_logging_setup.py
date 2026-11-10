import logging

import pytest

from core.config import Settings
from core.logging_setup import setup_logging


@pytest.fixture(autouse=True)
def restore_root_logger():
    """Put the root logger back how we found it after each test."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    for handler in saved_handlers:
        root.addHandler(handler)
    root.setLevel(saved_level)


def test_setup_creates_log_file_and_writes(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")
    setup_logging(settings)

    logging.getLogger("test.module").info("hello from the test")
    for handler in logging.getLogger().handlers:
        handler.flush()

    log_text = settings.log_file.read_text(encoding="utf-8")
    assert "hello from the test" in log_text
    assert "test.module" in log_text


def test_calling_setup_twice_does_not_duplicate_handlers(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")
    setup_logging(settings)
    setup_logging(settings)
    assert len(logging.getLogger().handlers) == 1


def test_log_level_is_applied(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", log_level="WARNING")
    setup_logging(settings)
    assert logging.getLogger().level == logging.WARNING