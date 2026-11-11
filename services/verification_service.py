"""Central verification workflow. Independent of Textual: the UI calls these methods and
subscribes to ProgressSnapshot callbacks.

Flow per student (one DB transaction per student, committed as soon as that student finishes):
  rate-limited fetch (+retries) -> classify -> parse (only if accessible) -> evaluate -> store
  VerificationRecord + per-course results -> student's latest status updated -> progress emitted.
The record is written only when the outcome is known, so no "in progress" row can mislead.
Cancelled / not-started students get no record at all.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.constants import VerificationMode
from database import (
    CampaignRepository,
    Database,
    DatabaseKnownProfileLookup,
    ImportCounts,
    ImportRepository,
    ImportStoreResult,
    ProfileInput,
    StudentRepository,
    VerificationRepository,
)
from scraper.batch import BatchState, VerificationBatch
from scraper.parser import parse_profile
from services.csv_import import CsvImportService
from services.evaluator import evaluate
from services.progress import ProgressTracker
from services.selection import select_students
from services.verification_mapper import build_verification_input
from services.verification_models import (
    ConfirmationRequired,
    ProgressSnapshot,
    RunInProgressError,
    RunSummary,
    SelectionMode,
    StudentSnapshot,
    VerificationPreview,
)

logger = logging.getLogger(__name__)

_DB_MODE = {
    SelectionMode.ALL: VerificationMode.BULK,
    SelectionMode.INCOMPLETE: VerificationMode.BULK,
    SelectionMode.RETRY_FAILED: VerificationMode.RETRY,
}


@dataclass(frozen=True, slots=True)
class ImportReport:
    result: Any  # models.import_schemas.ImportResult (summary, duplicates, invalid rows, ...)
    stored: ImportStoreResult
    new_student_ids: tuple[int, ...]
    existing_student_ids: tuple[int, ...]  # pass to preview(..., student_ids=) to reverify them


class VerificationService:
    def __init__(self, database: Database, campaign: Any, controlled: Any, limiter: Any, *,
                 parser: Callable[..., Any] = parse_profile, evaluator: Callable[..., Any] = evaluate,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._db, self._campaign = database, campaign
        self._controlled, self._limiter = controlled, limiter
        self._parser, self._evaluator, self._clock = parser, evaluator, clock
        self._csv = CsvImportService(DatabaseKnownProfileLookup(database))
        self._batch: VerificationBatch | None = None
        self._running = False
        self.last_progress: ProgressSnapshot | None = None

    # -- setup ---------------------------------------------------------------
    def ensure_campaign(self) -> int:
        """Sync config -> DB (deactivates dropped courses, never deletes history)."""
        with self._db.session_scope() as s:
            return CampaignRepository(s).sync_from_config(self._campaign).id

    # -- import (never scrapes) ----------------------------------------------
    def preview_csv(self, source: Path | str, *, is_text: bool = False) -> Any:
        """Validate + classify a CSV (new / existing / duplicate / invalid) WITHOUT storing anything."""
        return self._csv.import_text(str(source)) if is_text else self._csv.import_file(source)

    
    def import_csv(self, source: Path | str, *, is_text: bool = False) -> ImportReport:
        result = self._csv.import_text(str(source)) if is_text else self._csv.import_file(source)
        campaign_id = self.ensure_campaign()
        profiles = [ProfileInput(p.original_url, p.normalized_url, p.profile_id) for p in result.profiles]
        with self._db.session_scope() as s:
            stored = ImportRepository(s).import_profiles(
                campaign_id=campaign_id, profiles=profiles,
                source_name=None if is_text else Path(source).name,
                counts=ImportCounts(**result.summary.model_dump()))
            students = StudentRepository(s)
            new_ids, existing_ids = [], []
            for p in result.profiles:
                st = students.get_by_normalized_url(p.normalized_url)
                if st is None:  # held for review (case conflict)
                    continue
                (existing_ids if p.already_known else new_ids).append(st.id)
        return ImportReport(result, stored, tuple(new_ids), tuple(existing_ids))

    # -- dry run ----------------------------------------------------------------
    def preview(self, mode: SelectionMode, *, student_ids: Collection[int] | None = None) -> VerificationPreview:
        """No requests are made. `student_ids` restricts the run (e.g. 'reverify these existing students')."""
        campaign_id = self.ensure_campaign()
        with self._db.session_scope() as s:
            vr = VerificationRepository(s)
            snaps = []
            for st in StudentRepository(s).list_all(campaign_id=campaign_id):
                att = vr.latest_attempt(st.id, campaign_id)
                snaps.append(StudentSnapshot(st.id, st.normalized_url, att.status if att else None,
                                             att.error_category if att else None))
        eligible, excluded = select_students(mode, snaps, subset=student_ids)
        return VerificationPreview(mode, campaign_id, len(snaps), tuple(eligible), tuple(excluded), self._clock())

    # -- run ------------------------------------------------------------------------
    def cancel(self) -> None:
        """Graceful: finish the request in flight, store it, start nothing new."""
        if self._batch is not None:
            self._batch.cancel()

    async def run(self, preview: VerificationPreview, *, confirmed: bool,
                  on_progress: Callable[[ProgressSnapshot], None] | None = None) -> RunSummary:
        if not confirmed:
            raise ConfirmationRequired("Verification requires explicit confirmation of the preview.")
        if self._running:
            raise RunInProgressError("A verification run is already active.")
        self._running = True
        tracker = ProgressTracker(preview.mode, preview.eligible_count, on_progress)
        record_ids: list[int] = []
        try:
            campaign_id = self.ensure_campaign()  # pick up any config change since the preview
            with self._db.session_scope() as s:
                cert_ids = {int(c.template_id): c.id for c in CampaignRepository(s).active_certifications(campaign_id)}
            db_mode = _DB_MODE[preview.mode]
            urls = {row.student_id: row.url for row in preview.eligible}
            batch = self._batch = VerificationBatch(self._controlled, self._limiter)
            tracker.start()

            async def store(outcome) -> None:
                student_id = outcome.key
                vi = build_verification_input(
                    student_id=student_id, campaign_id=campaign_id, mode=db_mode, outcome=outcome,
                    campaign=self._campaign, cert_ids=cert_ids, parser=self._parser,
                    evaluator=self._evaluator, now=self._clock())
                with self._db.session_scope() as s:  # one transaction per student
                    rec = VerificationRepository(s).record(vi)
                    rec_id = rec.id
                record_ids.append(rec_id)
                tracker.record(vi.status, outcome.url)

            outcomes = await batch.run([(sid, url) for sid, url in urls.items()], store)
        except Exception as exc:
            logger.exception("Verification run stopped by an error; committed records are kept")
            self.last_progress = tracker.finish("error", f"{type(exc).__name__}: {exc}")
            raise
        finally:
            self._running = False
            self._batch = None

        not_started = [o for o in outcomes if o.state is BatchState.NOT_STARTED]
        cancelled = [o for o in outcomes if o.state is BatchState.CANCELLED]
        if any(o.detail == "rate_limit_circuit_open" for o in not_started):
            state = "stopped_rate_limited"
        elif not_started or cancelled:
            state = "cancelled"
        else:
            state = "finished"
        self.last_progress = tracker.finish(state)
        return RunSummary(preview.mode, self.last_progress, tuple(record_ids), len(not_started), len(cancelled))