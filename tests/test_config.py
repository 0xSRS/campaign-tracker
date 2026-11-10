import pytest
from pydantic import ValidationError

from core.config import Settings


def test_defaults():
    s = Settings(_env_file=None)
    assert s.log_level == "INFO"
    assert s.request_timeout_seconds == 20.0
    assert s.database_path.name == "tracker.db"
    assert s.campaign_file.name == "courses.json"


def test_env_override(monkeypatch):
    monkeypatch.setenv("CT_LOG_LEVEL", "debug")
    monkeypatch.setenv("CT_REQUEST_TIMEOUT_SECONDS", "5")
    s = Settings(_env_file=None)
    assert s.log_level == "DEBUG"
    assert s.request_timeout_seconds == 5.0


def test_invalid_log_level_rejected(monkeypatch):
    monkeypatch.setenv("CT_LOG_LEVEL", "LOUD")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_non_positive_timeout_rejected(monkeypatch):
    monkeypatch.setenv("CT_REQUEST_TIMEOUT_SECONDS", "0")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_ensure_directories_creates_data_dir(tmp_path):
    s = Settings(_env_file=None, data_dir=tmp_path / "newdata")
    s.ensure_directories()
    assert (tmp_path / "newdata").is_dir()