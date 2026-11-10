from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from core.constants import ImportOutcome, VerificationStatus
from core.exceptions import DatabaseError, InvalidRecordError
from database.base import utcnow
from database.models import (
    READ_STATUSES,
    Campaign,
    CampaignStudent,
    ImportBatch,
    ImportEntry,
    RequiredCertification,
    Student,
    VerificationRecord,
    VerificationResult,
)
from database.records import ImportCounts, ImportStoreResult, ProfileInput, VerificationInput

if TYPE_CHECKING:
    from campaign.schemas import CampaignConfig
    from database.session import Database


class StudentRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def add(self, *, original_url: str, normalized_url: str, profile_id: str) -> Student:
        student = Student(
            original_url=original_url,
            normalized_url=normalized_url,
            profile_id=profile_id,
            profile_key=profile_id.casefold(),
        )
        self._s.add(student)
        self._s.flush()
        return student

    def get(self, student_id: int) -> Student | None:
        return self._s.get(Student, student_id)

    def get_by_normalized_url(self, normalized_url: str) -> Student | None:
        return self._s.scalar(select(Student).where(Student.normalized_url == normalized_url))

    def find_case_variant(self, profile_id: str, normalized_url: str) -> Student | None:
        """Same profile ID differing only by case (different normalized URL)."""
        return self._s.scalar(
            select(Student).where(
                Student.profile_key == profile_id.casefold(),
                Student.normalized_url != normalized_url,
            )
        )

    def known_normalized_urls(self) -> set[str]:
        return set(self._s.scalars(select(Student.normalized_url)))

    def list_all(self, *, campaign_id: int | None = None) -> list[Student]:
        stmt = select(Student).order_by(Student.id)
        if campaign_id is not None:
            stmt = stmt.join(CampaignStudent, CampaignStudent.student_id == Student.id).where(
                CampaignStudent.campaign_id == campaign_id
            )
        return list(self._s.scalars(stmt))

    def count(self) -> int:
        return self._s.scalar(select(func.count()).select_from(Student)) or 0

    def count_by_latest_status(
        self, *, campaign_id: int | None = None
    ) -> dict[VerificationStatus | None, int]:
        """Counts by latest attempt status; key None = never verified."""
        stmt = select(Student.latest_status, func.count()).group_by(Student.latest_status)
        if campaign_id is not None:
            stmt = stmt.join(CampaignStudent, CampaignStudent.student_id == Student.id).where(
                CampaignStudent.campaign_id == campaign_id
            )
        return {status: n for status, n in self._s.execute(stmt)}


class CampaignRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def get(self, campaign_id: int) -> Campaign | None:
        return self._s.get(Campaign, campaign_id)

    def get_by_name(self, name: str) -> Campaign | None:
        return self._s.scalar(select(Campaign).where(Campaign.name == name))

    def sync_from_config(self, config: CampaignConfig) -> Campaign:
        """Create/update the campaign and its required certifications from campaign config.

        Certifications dropped from the config are deactivated, never deleted,
        because historical results reference them.
        """
        campaign = self.get_by_name(config.campaign_name)
        if campaign is None:
            campaign = Campaign(name=config.campaign_name)
            self._s.add(campaign)
            self._s.flush()
        campaign.is_active = True

        existing = {c.template_id: c for c in campaign.certifications}
        seen: set[str] = set()
        for position, course in enumerate(config.courses):
            template_id = str(course.template_id)
            seen.add(template_id)
            cert = existing.get(template_id)
            if cert is None:
                campaign.certifications.append(
                    RequiredCertification(course_name=course.name, template_id=template_id, position=position)
                )
            else:
                cert.course_name = course.name
                cert.position = position
                cert.is_active = True
        for template_id, cert in existing.items():
            if template_id not in seen:
                cert.is_active = False
        self._s.flush()
        return campaign

    def active_certifications(self, campaign_id: int) -> list[RequiredCertification]:
        return list(
            self._s.scalars(
                select(RequiredCertification)
                .where(
                    RequiredCertification.campaign_id == campaign_id,
                    RequiredCertification.is_active.is_(True),
                )
                .order_by(RequiredCertification.position)
            )
        )

    def set_confirmed_credential_id(self, certification_id: int, credential_id: str | None) -> None:
        cert = self._s.get(RequiredCertification, certification_id)
        if cert is None:
            raise InvalidRecordError(f"Unknown certification id {certification_id}.")
        cert.confirmed_credential_id = credential_id

    def link_student(self, campaign_id: int, student_id: int, import_batch_id: int | None = None) -> bool:
        """Returns True if a new link was created."""
        if self._s.get(CampaignStudent, (campaign_id, student_id)) is not None:
            return False
        self._s.add(
            CampaignStudent(campaign_id=campaign_id, student_id=student_id, import_batch_id=import_batch_id)
        )
        self._s.flush()
        return True


class ImportRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def import_profiles(
        self,
        *,
        campaign_id: int,
        profiles: list[ProfileInput],
        source_name: str | None = None,
        counts: ImportCounts | None = None,
    ) -> ImportStoreResult:
        """Store new students, skip existing ones, hold case conflicts. Never verifies."""
        if self._s.get(Campaign, campaign_id) is None:
            raise InvalidRecordError(f"Unknown campaign id {campaign_id}.")

        batch = ImportBatch(campaign_id=campaign_id, source_name=source_name, **asdict(counts or ImportCounts()))
        self._s.add(batch)
        self._s.flush()

        students = StudentRepository(self._s)
        campaigns = CampaignRepository(self._s)
        added = existing = held = linked = 0

        for p in profiles:
            student = students.get_by_normalized_url(p.normalized_url)
            if student is not None:
                outcome = ImportOutcome.EXISTING
                existing += 1
            else:
                conflict = students.find_case_variant(p.profile_id, p.normalized_url)
                if conflict is not None:
                    student, outcome = conflict, ImportOutcome.CASE_CONFLICT
                    held += 1
                else:
                    student = students.add(
                        original_url=p.original_url,
                        normalized_url=p.normalized_url,
                        profile_id=p.profile_id,
                    )
                    outcome = ImportOutcome.ADDED
                    added += 1

            if outcome is not ImportOutcome.CASE_CONFLICT and campaigns.link_student(
                campaign_id, student.id, batch.id
            ):
                linked += 1
            self._s.add(
                ImportEntry(
                    batch_id=batch.id,
                    student_id=student.id,
                    submitted_url=p.original_url,
                    normalized_url=p.normalized_url,
                    outcome=outcome,
                )
            )
        self._s.flush()
        return ImportStoreResult(batch.id, added, existing, held, linked)

    def latest_batches(self, limit: int = 20) -> list[ImportBatch]:
        return list(self._s.scalars(select(ImportBatch).order_by(desc(ImportBatch.id)).limit(limit)))

    def entries(self, batch_id: int) -> list[ImportEntry]:
        return list(self._s.scalars(select(ImportEntry).where(ImportEntry.batch_id == batch_id).order_by(ImportEntry.id)))


class VerificationRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def record(self, data: VerificationInput) -> VerificationRecord:
        """Append one verification attempt (never overwrites) and refresh the student's caches."""
        student = self._s.get(Student, data.student_id)
        if student is None:
            raise InvalidRecordError(f"Unknown student id {data.student_id}.")
        if self._s.get(Campaign, data.campaign_id) is None:
            raise InvalidRecordError(f"Unknown campaign id {data.campaign_id}.")

        outcomes = list(data.outcomes)
        completed: int | None = None
        missing: int | None = None

        if data.status.profile_was_read:
            active_ids = {c.id for c in CampaignRepository(self._s).active_certifications(data.campaign_id)}
            ids = [o.required_certification_id for o in outcomes]
            if len(set(ids)) != len(ids) or set(ids) != active_ids:
                raise InvalidRecordError(
                    "A read profile must have exactly one result per active required certification."
                )
            completed = sum(1 for o in outcomes if o.is_completed)
            missing = len(outcomes) - completed
            if data.status is VerificationStatus.COMPLETED and missing != 0:
                raise InvalidRecordError("COMPLETED status requires zero missing certifications.")
            if data.status is VerificationStatus.INCOMPLETE and missing == 0:
                raise InvalidRecordError("INCOMPLETE status requires at least one missing certification.")
        elif outcomes:
            raise InvalidRecordError(f"Status {data.status.value} must not carry certification results.")

        record = VerificationRecord(
            student_id=data.student_id,
            campaign_id=data.campaign_id,
            verified_at=utcnow(),
            mode=data.mode,
            status=data.status,
            http_status=data.http_status,
            completed_count=completed,
            missing_count=missing,
            error_category=data.error_category,
            diagnostic_message=data.diagnostic_message,
            duration_seconds=data.duration_seconds,
        )
        for o in outcomes:
            record.results.append(
                VerificationResult(
                    required_certification_id=o.required_certification_id,
                    is_completed=o.is_completed,
                    matched_credential_id=o.matched_credential_id,
                    evidence=o.evidence,
                )
            )
        self._s.add(record)

        student.latest_status = data.status
        if data.status.profile_was_read:
            student.latest_evaluated_status = data.status
        student.updated_at = utcnow()
        self._s.flush()
        return record

    def _ordered(self, student_id: int, campaign_id: int | None):
        stmt = select(VerificationRecord).where(VerificationRecord.student_id == student_id)
        if campaign_id is not None:
            stmt = stmt.where(VerificationRecord.campaign_id == campaign_id)
        return stmt.order_by(desc(VerificationRecord.verified_at), desc(VerificationRecord.id))

    def latest_attempt(self, student_id: int, campaign_id: int | None = None) -> VerificationRecord | None:
        return self._s.scalar(self._ordered(student_id, campaign_id).limit(1))

    def latest_evaluated(self, student_id: int, campaign_id: int | None = None) -> VerificationRecord | None:
        """Most recent attempt where the profile was actually read (COMPLETED/INCOMPLETE)."""
        stmt = self._ordered(student_id, campaign_id).where(VerificationRecord.status.in_(READ_STATUSES))
        return self._s.scalar(stmt.limit(1))

    def history(
        self, student_id: int, campaign_id: int | None = None, limit: int | None = None
    ) -> list[VerificationRecord]:
        """Newest first."""
        stmt = self._ordered(student_id, campaign_id)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self._s.scalars(stmt))

    def results(self, verification_id: int) -> list[VerificationResult]:
        return list(
            self._s.scalars(
                select(VerificationResult)
                .join(RequiredCertification)
                .where(VerificationResult.verification_id == verification_id)
                .order_by(RequiredCertification.position)
            )
        )


class DatabaseKnownProfileLookup:
    """Implements Module 2's KnownProfileLookup protocol against SQLite."""

    def __init__(self, database: Database) -> None:
        self._db = database

    def known_normalized_urls(self) -> set[str]:
        with self._db.session_scope() as session:
            return StudentRepository(session).known_normalized_urls()


__all__ = [
    "CampaignRepository",
    "DatabaseError",
    "DatabaseKnownProfileLookup",
    "ImportRepository",
    "StudentRepository",
    "VerificationRepository",
]