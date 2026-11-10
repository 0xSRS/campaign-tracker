"""CSV ingestion service. No UI code, no network access."""

import csv
import io
import logging
from pathlib import Path
from typing import Protocol

from core.constants import CSV_HEADER
from core.exceptions import (
    CsvFileError,
    CsvFormatError,
    InvalidProfileURLError,
)
from models.import_schemas import (
    DuplicateKind,
    DuplicateRow,
    ImportResult,
    ImportSummary,
    ProfileEntry,
    RowIssue,
)
from services.profile_url import parse_profile_url

logger = logging.getLogger(__name__)


class KnownProfileLookup(Protocol):
    """Implemented by the database layer in Module 3."""

    def known_normalized_urls(self) -> set[str]: ...


class CsvImportService:
    def __init__(self, lookup: KnownProfileLookup | None = None) -> None:
        self._lookup = lookup

    # -- public API -----------------------------------------------------------
    def import_file(self, path: Path | str) -> ImportResult:
        path = Path(path)
        try:
            text = path.read_text(encoding="utf-8-sig")
        except FileNotFoundError as exc:
            raise CsvFileError(f"CSV file not found: {path}") from exc
        except UnicodeDecodeError as exc:
            raise CsvFileError(f"CSV file is not valid UTF-8 text: {path}") from exc
        except OSError as exc:
            raise CsvFileError(f"Cannot read CSV file {path}: {exc}") from exc
        return self.import_text(text)

    def import_text(self, text: str) -> ImportResult:
        text = text.lstrip("\ufeff")
        if not text.strip():
            raise CsvFormatError("The CSV file is empty.")
        reader = csv.reader(io.StringIO(text, newline=""))
        try:
            self._read_header(reader)
            result = self._process_rows(reader)
        except csv.Error as exc:
            raise CsvFormatError(f"Unreadable CSV: {exc}") from exc
        logger.info("CSV import: %s", result.summary.model_dump())
        return result

    # -- internals ------------------------------------------------------------
    @staticmethod
    def _read_header(reader) -> None:
        for cells in reader:
            if all(not c.strip() for c in cells):
                continue  # skip blank lines before the header
            cells = [c.strip() for c in cells]
            while cells and not cells[-1]:
                cells.pop()  # tolerate trailing commas
            if len(cells) > 1:
                raise CsvFormatError(
                    f"Expected a single column '{CSV_HEADER}', found {len(cells)} columns."
                )
            if cells[0].casefold() == CSV_HEADER.casefold():
                return
            try:
                parse_profile_url(cells[0])
                looks_like_url = True
            except InvalidProfileURLError:
                looks_like_url = cells[0].lower().startswith("http")
            if looks_like_url:
                raise CsvFormatError(
                    f"The header row is missing. Expected '{CSV_HEADER}' as the first line."
                )
            raise CsvFormatError(
                f"Unexpected header '{cells[0]}'. Expected '{CSV_HEADER}'."
            )
        raise CsvFormatError("The CSV file has no header row.")

    def _process_rows(self, reader) -> ImportResult:
        known = self._lookup.known_normalized_urls() if self._lookup else set()
        id_map = {u.rsplit("/", 1)[-1].casefold(): u for u in known}
        first_seen: dict[str, tuple[int, str]] = {}

        profiles: list[ProfileEntry] = []
        invalid: list[RowIssue] = []
        errors: list[RowIssue] = []
        dups: list[DuplicateRow] = []
        review: list[RowIssue] = []
        total = blank = 0

        for cells in reader:
            total += 1
            row_no = reader.line_num

            if all(not c.strip() for c in cells):
                blank += 1
                continue
            if any(c.strip() for c in cells[1:]):
                errors.append(RowIssue(
                    row_number=row_no,
                    raw_value=",".join(cells),
                    reason="Unexpected extra columns (expected exactly one)",
                ))
                continue

            raw = cells[0]
            try:
                parsed = parse_profile_url(raw)
            except InvalidProfileURLError as exc:
                invalid.append(RowIssue(row_number=row_no, raw_value=raw, reason=str(exc)))
                continue

            if parsed.normalized in first_seen:
                first_row, first_original = first_seen[parsed.normalized]
                kind = (DuplicateKind.EXACT if parsed.original == first_original
                        else DuplicateKind.NORMALIZED)
                dups.append(DuplicateRow(
                    row_number=row_no, original_url=parsed.original,
                    normalized_url=parsed.normalized,
                    first_row_number=first_row, kind=kind,
                ))
                continue

            other = id_map.get(parsed.profile_id.casefold())
            if other is not None and other != parsed.normalized:
                review.append(RowIssue(
                    row_number=row_no, raw_value=parsed.original,
                    reason=("Profile ID differs only by letter case from "
                            f"{other}; held for review, not imported"),
                ))
                continue

            id_map[parsed.profile_id.casefold()] = parsed.normalized
            first_seen[parsed.normalized] = (row_no, parsed.original)
            profiles.append(ProfileEntry(
                row_number=row_no, original_url=parsed.original,
                normalized_url=parsed.normalized, profile_id=parsed.profile_id,
                already_known=parsed.normalized in known,
            ))

        existing = sum(p.already_known for p in profiles)
        summary = ImportSummary(
            total_rows=total,
            valid_urls=len(profiles) + len(dups) + len(review),
            new_profiles=len(profiles) - existing,
            existing_profiles=existing,
            duplicate_rows=len(dups),
            blank_rows=blank,
            invalid_urls=len(invalid),
            import_errors=len(errors),
            needs_review=len(review),
        )
        return ImportResult(
            summary=summary, profiles=profiles, invalid_rows=invalid,
            error_rows=errors, duplicates=dups, review_items=review,
        )