from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.constants import ImportOutcome, VerificationMode, VerificationStatus
from database.base import Base, UTCDateTime, utcnow

READ_STATUSES = tuple(s for s in VerificationStatus if s.profile_was_read)
_READ_SQL = ", ".join(f"'{s.value}'" for s in READ_STATUSES)


def _enum(cls: type) -> SAEnum:
    return SAEnum(
        cls,
        native_enum=False,
        create_constraint=False,
        validate_strings=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )


class Student(Base):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(primary_key=True)
    original_url: Mapped[str] = mapped_column(String(512))
    normalized_url: Mapped[str] = mapped_column(String(512), unique=True)
    profile_id: Mapped[str] = mapped_column(String(128))
    profile_key: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    # Cache of the most recent attempt (any status).
    latest_status: Mapped[VerificationStatus | None] = mapped_column(_enum(VerificationStatus), default=None)
    # Cache of the most recent attempt where the profile was actually read.
    latest_evaluated_status: Mapped[VerificationStatus | None] = mapped_column(
        _enum(VerificationStatus), default=None
    )

    verifications: Mapped[list[VerificationRecord]] = relationship(
        back_populates="student", order_by="VerificationRecord.id"
    )
    campaign_links: Mapped[list[CampaignStudent]] = relationship(back_populates="student")


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    certifications: Mapped[list[RequiredCertification]] = relationship(
        back_populates="campaign", order_by="RequiredCertification.position"
    )
    student_links: Mapped[list[CampaignStudent]] = relationship(back_populates="campaign")


class RequiredCertification(Base):
    __tablename__ = "required_certifications"
    __table_args__ = (
        UniqueConstraint("campaign_id", "template_id", name="uq_required_certifications_campaign_template"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="RESTRICT"))
    course_name: Mapped[str] = mapped_column(String(300))
    template_id: Mapped[str] = mapped_column(String(32))
    # Stays NULL until validated against real profile data (Module 5+).
    confirmed_credential_id: Mapped[str | None] = mapped_column(String(64), default=None)
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    campaign: Mapped[Campaign] = relationship(back_populates="certifications")


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="RESTRICT"))
    source_name: Mapped[str | None] = mapped_column(String(512), default=None)
    imported_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_urls: Mapped[int] = mapped_column(Integer, default=0)
    new_profiles: Mapped[int] = mapped_column(Integer, default=0)
    existing_profiles: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_rows: Mapped[int] = mapped_column(Integer, default=0)
    blank_rows: Mapped[int] = mapped_column(Integer, default=0)
    invalid_urls: Mapped[int] = mapped_column(Integer, default=0)
    import_errors: Mapped[int] = mapped_column(Integer, default=0)
    needs_review: Mapped[int] = mapped_column(Integer, default=0)

    entries: Mapped[list[ImportEntry]] = relationship(back_populates="batch", order_by="ImportEntry.id")


class ImportEntry(Base):
    __tablename__ = "import_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id", ondelete="RESTRICT"), index=True)
    # For CASE_CONFLICT this is the already-stored student the row clashed with.
    student_id: Mapped[int | None] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"))
    submitted_url: Mapped[str] = mapped_column(String(512))
    normalized_url: Mapped[str] = mapped_column(String(512))
    outcome: Mapped[ImportOutcome] = mapped_column(_enum(ImportOutcome))

    batch: Mapped[ImportBatch] = relationship(back_populates="entries")


class CampaignStudent(Base):
    __tablename__ = "campaign_students"

    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="RESTRICT"), primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id", ondelete="RESTRICT"))

    campaign: Mapped[Campaign] = relationship(back_populates="student_links")
    student: Mapped[Student] = relationship(back_populates="campaign_links")


class VerificationRecord(Base):
    __tablename__ = "verification_records"
    __table_args__ = (
        CheckConstraint("(completed_count IS NULL) = (missing_count IS NULL)", name="counts_paired"),
        CheckConstraint(
            "completed_count IS NULL OR (completed_count >= 0 AND missing_count >= 0)",
            name="counts_non_negative",
        ),
        CheckConstraint(
            f"completed_count IS NULL OR status IN ({_READ_SQL})", name="counts_only_when_read"
        ),
        CheckConstraint("duration_seconds IS NULL OR duration_seconds >= 0", name="duration_non_negative"),
        Index("ix_verification_records_student_time", "student_id", "verified_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"))
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="RESTRICT"))
    verified_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    mode: Mapped[VerificationMode] = mapped_column(_enum(VerificationMode))
    status: Mapped[VerificationStatus] = mapped_column(_enum(VerificationStatus))
    http_status: Mapped[int | None] = mapped_column(Integer, default=None)
    # NULL (never 0) when the profile was not read.
    completed_count: Mapped[int | None] = mapped_column(Integer, default=None)
    missing_count: Mapped[int | None] = mapped_column(Integer, default=None)
    error_category: Mapped[str | None] = mapped_column(String(64), default=None)
    diagnostic_message: Mapped[str | None] = mapped_column(Text, default=None)
    duration_seconds: Mapped[float | None] = mapped_column(Float, default=None)

    student: Mapped[Student] = relationship(back_populates="verifications")
    results: Mapped[list[VerificationResult]] = relationship(
        back_populates="verification", order_by="VerificationResult.id"
    )


class VerificationResult(Base):
    __tablename__ = "verification_results"
    __table_args__ = (
        UniqueConstraint(
            "verification_id", "required_certification_id", name="uq_verification_results_verification_cert"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    verification_id: Mapped[int] = mapped_column(
        ForeignKey("verification_records.id", ondelete="RESTRICT"), index=True
    )
    required_certification_id: Mapped[int] = mapped_column(
        ForeignKey("required_certifications.id", ondelete="RESTRICT")
    )
    is_completed: Mapped[bool] = mapped_column(Boolean)
    matched_credential_id: Mapped[str | None] = mapped_column(String(64), default=None)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True), default=None)

    verification: Mapped[VerificationRecord] = relationship(back_populates="results")
    certification: Mapped[RequiredCertification] = relationship()