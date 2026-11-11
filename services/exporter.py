"""CSV report export (Module 10). Reads via CampaignReports; only this module writes report files.

Rules: failed / private / not-found / needs-review profiles are never counted as incomplete;
counts are blank (never 0) when the latest attempt did not read the profile; files are never
overwritten (a numeric suffix is added); cells that could run as spreadsheet formulas are neutralised.
"""

from __future__ import annotations

import csv
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from core.constants import VerificationStatus as V
from core.exceptions import ExportError

logger = logging.getLogger(__name__)


class ReportKind(StrEnum):
    STUDENT_SUMMARY = "student_summary"
    COURSE_REPORT = "course_report"
    MISSING_CERTIFICATIONS = "missing_certifications"
    FAILED_PROFILES = "failed_profiles"
    CAMPAIGN_SUMMARY = "campaign_summary"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").capitalize()


@dataclass(frozen=True, slots=True)
class ExportResult:
    kind: ReportKind
    path: Path
    rows: int  # data rows, header excluded


_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def safe_filename(name: str | None, default_stem: str) -> str:
    """Return a plain `<name>.csv`: no directories, no odd characters, no Windows reserved names."""
    base = Path((name or "").replace("\\", "/")).name
    stem = base[:-4] if base.lower().endswith(".csv") else base
    stem = _UNSAFE.sub("_", stem).strip("._-")[:80] or default_stem
    if stem.lower() in _RESERVED:
        stem += "_"
    return f"{stem}.csv"


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat(timespec="seconds")
    if isinstance(value, float):
        return f"{value:.1f}"
    if not isinstance(value, str):
        return str(value)
    text = " ".join(value.split())  # one line per cell
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def _pct(part: int | None, whole: int | None) -> float | None:
    return round(100 * part / whole, 1) if part is not None and whole else None


class ReportExporter:
    def __init__(self, reports: Any, output_dir: Path | str, *,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._reports, self._dir, self._clock = reports, Path(output_dir), clock

    @property
    def output_dir(self) -> Path:
        return self._dir

    # -- public ------------------------------------------------------------------------
    def build(self, kind: ReportKind) -> tuple[list[str], list[list[Any]]]:
        """Header and rows for one report (no file written)."""
        try:
            return getattr(self, f"_{kind.value}")()
        except LookupError as exc:
            raise ExportError(str(exc)) from exc

    def export(self, kind: ReportKind, *, filename: str | None = None,
               output_dir: Path | str | None = None) -> ExportResult:
        header, rows = self.build(kind)
        stamp = self._clock().strftime("%Y%m%d_%H%M%S")
        name = safe_filename(filename, f"{kind.value}_{stamp}")
        path = self._write(Path(output_dir) if output_dir else self._dir, name, header, rows)
        logger.info("Exported %s: %d rows", kind.value, len(rows))
        return ExportResult(kind, path, len(rows))

    def export_all(self, *, output_dir: Path | str | None = None, prefix: str | None = None) -> list[ExportResult]:
        out = []
        for kind in ReportKind:
            name = f"{prefix}_{kind.value}" if prefix else None
            out.append(self.export(kind, filename=name, output_dir=output_dir))
        return out

    # -- writing -----------------------------------------------------------------------
    @staticmethod
    def _write(directory: Path, filename: str, header: Sequence[str], rows: Sequence[Sequence[Any]]) -> Path:
        stem, ext = filename[:-4], ".csv"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            for n in range(1000):
                path = directory / (filename if n == 0 else f"{stem}_{n}{ext}")
                try:
                    fh = open(path, "x", newline="", encoding="utf-8-sig")  # "x": never overwrite
                except FileExistsError:
                    continue
                try:
                    with fh:
                        w = csv.writer(fh)
                        w.writerow(header)
                        w.writerows([_cell(v) for v in row] for row in rows)
                except OSError:
                    path.unlink(missing_ok=True)  # no half-written report left behind
                    raise
                return path
        except OSError as exc:
            raise ExportError(f"Could not write the report to '{directory}': {exc.strerror or exc}") from exc
        raise ExportError(f"Too many existing files named '{filename}'. Choose another filename.")

    # -- report builders ---------------------------------------------------------------
    def _student_summary(self):
        header = ["Student ID", "Profile URL", "Verification Status", "Completed Count", "Missing Count",
                  "Completion Percentage", "Last Verified (UTC)"]
        rows = []
        for s in self._reports.student_summary():
            current = s.evaluation_is_current and s.completed_count is not None
            done, miss = (s.completed_count, s.missing_count) if current else (None, None)
            rows.append([s.student_id, s.url, s.latest_status.value if s.latest_status else "NOT_VERIFIED",
                         done, miss, _pct(done, (done or 0) + (miss or 0)), s.last_verified_at])
        return header, rows

    def _course_report(self):
        header = ["Course Name", "Course Template ID", "Completed Students", "Missing Students",
                  "Completion Percentage"]
        rows = [[c.course_name, c.template_id, c.completed_students, c.evaluated_students - c.completed_students,
                 c.percent] for c in self._reports.course_completion()]
        return header, rows

    def _missing_certifications(self):
        header = ["Profile URL", "Missing Courses", "Verification Timestamp (UTC)"]
        rows = [[m.url, "; ".join(m.missing_courses), m.verified_at]
                for m in self._reports.missing_certifications()]
        return header, rows

    def _failed_profiles(self):
        header = ["Profile URL", "Failure Classification", "HTTP Status", "Diagnostic Message",
                  "Last Attempt (UTC)"]
        rows = []
        for f in self._reports.failed_records():
            kind = f"{f.status.value} / {f.error_category}" if f.error_category else f.status.value
            rows.append([f.url, kind, f.http_status, f.diagnostic, f.verified_at])
        return header, rows

    def _campaign_summary(self):
        """Counts follow each student's LATEST attempt, so the buckets add up to the registered total."""
        by: dict[V | None, int] = {}
        current_done = []
        for s in self._reports.student_summary():
            by[s.latest_status] = by.get(s.latest_status, 0) + 1
            if s.evaluation_is_current and s.completed_count is not None:
                current_done.append(s.completed_count)
        completed, incomplete = by.get(V.COMPLETED, 0), by.get(V.INCOMPLETE, 0)
        evaluated = completed + incomplete
        avg = round(sum(current_done) / len(current_done), 2) if current_done else None
        rows = [
            ["Total registered students", sum(by.values())],
            ["Not yet verified", by.get(None, 0)],
            ["Successfully evaluated students", evaluated],
            ["Completed students", completed],
            ["Incomplete students", incomplete],
            ["Failed students", by.get(V.FAILED, 0)],
            ["Inaccessible students", by.get(V.PRIVATE_INACCESSIBLE, 0)],
            ["Not-found students", by.get(V.NOT_FOUND, 0)],
            ["Needs-review students", by.get(V.NEEDS_REVIEW, 0)],
            ["Completion percentage (completed / evaluated)", _pct(completed, evaluated)],
            ["Average courses completed (evaluated students)", avg],
            ["Report generated (UTC)", self._clock()],
        ]
        return ["Metric", "Value"], rows