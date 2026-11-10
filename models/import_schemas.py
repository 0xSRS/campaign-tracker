"""Result schemas for CSV import (shared with the future UI and exports)."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class DuplicateKind(StrEnum):
    EXACT = "EXACT"            # same URL text
    NORMALIZED = "NORMALIZED"  # same profile after safe normalization


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class ProfileEntry(_Frozen):
    row_number: int
    original_url: str
    normalized_url: str
    profile_id: str
    already_known: bool


class RowIssue(_Frozen):
    row_number: int
    raw_value: str
    reason: str


class DuplicateRow(_Frozen):
    row_number: int
    original_url: str
    normalized_url: str
    first_row_number: int
    kind: DuplicateKind


class ImportSummary(_Frozen):
    total_rows: int       # data rows, excluding the header
    valid_urls: int       # = new + existing + duplicate + needs_review
    new_profiles: int
    existing_profiles: int
    duplicate_rows: int
    blank_rows: int
    invalid_urls: int
    import_errors: int    # structurally bad rows (e.g. extra columns)
    needs_review: int


class ImportResult(_Frozen):
    summary: ImportSummary
    profiles: list[ProfileEntry]
    invalid_rows: list[RowIssue]
    error_rows: list[RowIssue]
    duplicates: list[DuplicateRow]
    review_items: list[RowIssue]