"""Read-only queries that prepare data for Module 10's exports. No file writing here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.constants import VerificationStatus as V
from database import CampaignRepository, Database, StudentRepository, VerificationRepository


@dataclass(frozen=True, slots=True)
class StudentSummary:
    student_id: int
    url: str
    latest_status: V | None  # latest attempt (None = never verified)
    last_verified_at: datetime | None
    error_category: str | None
    evaluated_status: V | None  # latest successful evaluation, may be older than the latest attempt
    completed_count: int | None
    missing_count: int | None
    evaluation_is_current: bool


@dataclass(frozen=True, slots=True)
class CourseCompletion:
    template_id: str
    course_name: str
    completed_students: int
    evaluated_students: int
    percent: float | None


@dataclass(frozen=True, slots=True)
class FailedRecord:
    student_id: int
    url: str
    status: V
    error_category: str | None
    diagnostic: str | None
    http_status: int | None
    verified_at: datetime


@dataclass(frozen=True, slots=True)
class MissingCertifications:
    student_id: int
    url: str
    missing_courses: tuple[str, ...]
    verified_at: datetime
    evaluation_is_current: bool


@dataclass(frozen=True, slots=True)
class CampaignStatistics:
    registered: int
    never_verified: int
    by_latest_status: dict[str, int]
    evaluated_students: int
    completed_students: int
    incomplete_students: int
    completion_rate: float | None  # completed / evaluated, as a percentage
    average_courses_completed: float | None


class CampaignReports:
    def __init__(self, database: Database, campaign: Any) -> None:
        self._db, self._name = database, campaign.campaign_name

    def _campaign_id(self, s) -> int:
        c = CampaignRepository(s).get_by_name(self._name)
        if c is None:
            raise LookupError(f"Campaign {self._name!r} has not been created yet.")
        return c.id

    def _rows(self, s):
        cid = self._campaign_id(s)
        vr = VerificationRepository(s)
        for st in StudentRepository(s).list_all(campaign_id=cid):
            yield st, vr.latest_attempt(st.id, cid), vr.latest_evaluated(st.id, cid), vr

    def student_summary(self) -> list[StudentSummary]:
        with self._db.session_scope() as s:
            return [StudentSummary(
                st.id, st.normalized_url, att.status if att else None, att.verified_at if att else None,
                att.error_category if att else None, ev.status if ev else None,
                ev.completed_count if ev else None, ev.missing_count if ev else None,
                bool(att and ev and att.id == ev.id)) for st, att, ev, _ in self._rows(s)]

    def course_completion(self) -> list[CourseCompletion]:
        with self._db.session_scope() as s:
            cid = self._campaign_id(s)
            certs = CampaignRepository(s).active_certifications(cid)
            done = {c.id: 0 for c in certs}
            evaluated = 0
            for _, _, ev, vr in self._rows(s):
                if ev is None:
                    continue
                evaluated += 1
                for r in vr.results(ev.id):
                    if r.is_completed and r.required_certification_id in done:
                        done[r.required_certification_id] += 1
            return [CourseCompletion(c.template_id, c.course_name, done[c.id], evaluated,
                                     round(100 * done[c.id] / evaluated, 1) if evaluated else None)
                    for c in certs]

    def failed_records(self) -> list[FailedRecord]:
        """Students whose LATEST attempt did not read the profile (failed/private/not found/review)."""
        with self._db.session_scope() as s:
            return [FailedRecord(st.id, st.normalized_url, att.status, att.error_category,
                                 att.diagnostic_message, att.http_status, att.verified_at)
                    for st, att, _, _ in self._rows(s) if att is not None and not att.status.profile_was_read]

    def missing_certifications(self) -> list[MissingCertifications]:
        with self._db.session_scope() as s:
            out = []
            for st, att, ev, vr in self._rows(s):
                if ev is None or ev.status is not V.INCOMPLETE:
                    continue
                missing = tuple(r.certification.course_name for r in vr.results(ev.id) if not r.is_completed)
                out.append(MissingCertifications(st.id, st.normalized_url, missing, ev.verified_at,
                                                 bool(att and att.id == ev.id)))
            return out

    def statistics(self) -> CampaignStatistics:
        with self._db.session_scope() as s:
            by_status: dict[str, int] = {}
            registered = never = evaluated = completed = incomplete = 0
            total_done = 0
            for _, att, ev, _ in self._rows(s):
                registered += 1
                if att is None:
                    never += 1
                else:
                    by_status[att.status.value] = by_status.get(att.status.value, 0) + 1
                if ev is not None:
                    evaluated += 1
                    total_done += ev.completed_count or 0
                    completed += ev.status is V.COMPLETED
                    incomplete += ev.status is V.INCOMPLETE
            return CampaignStatistics(
                registered, never, by_status, evaluated, completed, incomplete,
                round(100 * completed / evaluated, 1) if evaluated else None,
                round(total_done / evaluated, 2) if evaluated else None)