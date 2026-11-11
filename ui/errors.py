"""Maps exceptions to short, user-facing messages. Tracebacks go to the diagnostics log instead."""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from core.exceptions import (
    CsvFileError, CsvFormatError, CsvImportError, DatabaseError, ExportError,
    ProfileFetchError, ProfileParseError,
)
from services.verification_models import ConfirmationRequired, RunInProgressError

Severity = Literal["information", "warning", "error"]


@dataclass(frozen=True, slots=True)
class UserMessage:
    title: str
    text: str
    severity: Severity = "error"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    context: str
    title: str
    summary: str
    trace: str
    at: datetime = field(default_factory=lambda: datetime.now(UTC))


def describe_error(exc: BaseException) -> UserMessage:
    if isinstance(exc, (CsvFileError, CsvFormatError, CsvImportError)):
        return UserMessage("Invalid CSV", str(exc) or "The CSV file could not be read.")
    if isinstance(exc, DatabaseError):
        return UserMessage("Database error", "The database operation failed. Nothing half-written was kept.")
    if isinstance(exc, ExportError):
        return UserMessage("Export error", str(exc) or "The report could not be exported.")
    if isinstance(exc, ProfileParseError):
        return UserMessage("Parser error", "A profile page did not have the expected structure.")
    if isinstance(exc, ProfileFetchError):
        return UserMessage("HTTP error", "A profile could not be fetched. It can be retried later.")
    if isinstance(exc, RunInProgressError):
        return UserMessage("Verification running", "A verification run is already active.", "warning")
    if isinstance(exc, ConfirmationRequired):
        return UserMessage("Confirmation required", "Verification must be confirmed first.", "warning")
    if isinstance(exc, LookupError):
        return UserMessage("Campaign not ready", "The campaign has not been created yet. Import a CSV first.", "warning")
    return UserMessage("Unexpected error", f"{type(exc).__name__}: {exc}. Press F2 for diagnostics.")


def make_diagnostic(exc: BaseException, context: str) -> Diagnostic:
    msg = describe_error(exc)
    trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return Diagnostic(context, msg.title, f"{type(exc).__name__}: {exc}", trace)


# Run-level outcomes (ProgressSnapshot.state) that deserve a notification.
STATE_MESSAGES: dict[str, UserMessage] = {
    "finished": UserMessage("Verification finished", "All selected profiles were processed.", "information"),
    "cancelled": UserMessage("Verification cancelled", "Unprocessed students were left untouched.", "warning"),
    "stopped_rate_limited": UserMessage(
        "Rate limited", "The server kept returning HTTP 429, so the run stopped. Retry later (mode C).", "warning"),
}