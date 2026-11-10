from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import text

from campaign.loader import load_campaign
from core.constants import VerificationMode, VerificationStatus
from core.exceptions import DatabaseError, InvalidRecordError
from database import (
    SCHEMA_VERSION,
    CampaignRepository,
    CertificationOutcome,
    Database,
    DatabaseKnownProfileLookup,
    ImportRepository,
    ProfileInput,
    StudentRepository,
    VerificationInput,
    VerificationRepository,
)
from database.models import VerificationRecord
from services.profile_url import parse_profile_url

BASE = "https://www.skills.google/public_profiles/"


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    database.initialize()
    yield database
    database.close()


@pytest.fixture
def config():
    return load_campaign()


def profile(pid: str) -> ProfileInput:
    p = parse_profile_url(BASE + pid)
    return ProfileInput(p.original, p.normalized, p.profile_id)


def seed(db, config, pids=("abc1",)):
    with db.session_scope() as s:
        campaign = CampaignRepository(s).sync_from_config(config)
        cert_ids = [c.id for c in CampaignRepository(s).active_certifications(campaign.id)]
        ImportRepository(s).import_profiles(campaign_id=campaign.id, profiles=[profile(p) for p in pids])
        student_ids = [st.id for st in StudentRepository(s).list_all()]
        return campaign.id, cert_ids, student_ids


def rec(student_id, campaign_id, status, cert_ids=(), done=0, **kw):
    outcomes = ()
    if status.profile_was_read:
        outcomes = tuple(
            CertificationOutcome(
                cid,
                i < done,
                matched_credential_id=f"cred{i}" if i < done else None,
                evidence={"src": "test"} if i < done else None,
            )
            for i, cid in enumerate(cert_ids)
        )
    return VerificationInput(student_id=student_id, campaign_id=campaign_id, status=status, outcomes=outcomes, **kw)


# --- initialization -------------------------------------------------------

def test_initialize_creates_schema_and_is_idempotent(db, tmp_path):
    db.initialize()
    with db.session_scope() as s:
        tables = {r[0] for r in s.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        assert {"students", "campaigns", "required_certifications", "campaign_students",
                "import_batches", "import_entries", "verification_records",
                "verification_results"} <= tables
        assert s.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert s.execute(text("PRAGMA user_version")).scalar() == SCHEMA_VERSION


def test_newer_schema_version_is_rejected(tmp_path):
    path = tmp_path / "future.db"
    first = Database(path)
    first.initialize()
    first.close()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()
    second = Database(path)
    try:
        with pytest.raises(DatabaseError):
            second.initialize()
    finally:
        second.close()


# --- students and constraints --------------------------------------------

def test_insert_and_retrieve_student(db):
    p = profile("abc1")
    with db.session_scope() as s:
        repo = StudentRepository(s)
        student = repo.add(original_url=p.original_url, normalized_url=p.normalized_url, profile_id=p.profile_id)
        sid = student.id
    with db.session_scope() as s:
        found = StudentRepository(s).get_by_normalized_url(p.normalized_url)
        assert found.id == sid
        assert found.original_url == p.original_url
        assert found.latest_status is None
        assert found.created_at.tzinfo is not None


def test_unique_normalized_url_and_rollback_of_scope(db):
    p = profile("abc1")
    with pytest.raises(DatabaseError):
        with db.session_scope() as s:
            repo = StudentRepository(s)
            repo.add(original_url=p.original_url, normalized_url=p.normalized_url, profile_id=p.profile_id)
            repo.add(original_url=p.original_url, normalized_url=p.normalized_url, profile_id=p.profile_id)
    with db.session_scope() as s:
        assert StudentRepository(s).count() == 0


def test_foreign_keys_enforced(db, config):
    campaign_id, _, _ = seed(db, config)
    with pytest.raises(DatabaseError):
        with db.session_scope() as s:
            s.add(VerificationRecord(student_id=9999, campaign_id=campaign_id,
                                     mode=VerificationMode.MANUAL, status=VerificationStatus.FAILED))


def test_history_rows_cannot_be_orphaned(db, config):
    campaign_id, _, (sid,) = seed(db, config)
    with db.session_scope() as s:
        VerificationRepository(s).record(rec(sid, campaign_id, VerificationStatus.FAILED))
    with pytest.raises(DatabaseError):
        with db.session_scope() as s:
            s.execute(text("DELETE FROM students WHERE id = :i"), {"i": sid})


# --- campaign / relationships --------------------------------------------

def test_campaign_sync_creates_and_resyncs(db, config):
    seed(db, config)
    with db.session_scope() as s:
        repo = CampaignRepository(s)
        again = repo.sync_from_config(config)
        certs = repo.active_certifications(again.id)
        assert len(certs) == config.required_count
        assert [c.position for c in certs] == list(range(len(certs)))
        assert all(c.confirmed_credential_id is None for c in certs)


def test_removed_course_is_deactivated_not_deleted(db, config):
    campaign_id, cert_ids, _ = seed(db, config)
    shrunk = config.model_copy(update={"courses": config.courses[:-1]})
    with db.session_scope() as s:
        repo = CampaignRepository(s)
        repo.sync_from_config(shrunk)
        assert len(repo.active_certifications(campaign_id)) == len(cert_ids) - 1
        assert len(repo.get(campaign_id).certifications) == len(cert_ids)


def test_relationships(db, config):
    campaign_id, cert_ids, (sid,) = seed(db, config, ("abc1",))
    with db.session_scope() as s:
        VerificationRepository(s).record(rec(sid, campaign_id, VerificationStatus.INCOMPLETE, cert_ids, done=3))
    with db.session_scope() as s:
        student = StudentRepository(s).get(sid)
        assert len(student.verifications) == 1
        v = student.verifications[0]
        assert len(v.results) == len(cert_ids)
        assert (v.completed_count, v.missing_count) == (3, len(cert_ids) - 3)
        assert v.results[0].certification.campaign_id == campaign_id
        assert v.results[0].evidence == {"src": "test"}
        campaign = CampaignRepository(s).get(campaign_id)
        assert len(campaign.certifications) == len(cert_ids)
        assert [link.student_id for link in campaign.student_links] == [sid]


# --- verification history -------------------------------------------------

def test_history_preserved_and_latest_attempt_vs_evaluated(db, config):
    campaign_id, cert_ids, (sid,) = seed(db, config)
    with db.session_scope() as s:
        repo = VerificationRepository(s)
        repo.record(rec(sid, campaign_id, VerificationStatus.INCOMPLETE, cert_ids, done=4))
        repo.record(rec(sid, campaign_id, VerificationStatus.FAILED, http_status=500,
                        error_category="network", diagnostic_message="timeout", duration_seconds=1.5))
    with db.session_scope() as s:
        repo = VerificationRepository(s)
        history = repo.history(sid)
        assert [h.status for h in history] == [VerificationStatus.FAILED, VerificationStatus.INCOMPLETE]
        assert repo.latest_attempt(sid).status is VerificationStatus.FAILED
        assert repo.latest_evaluated(sid).status is VerificationStatus.INCOMPLETE
        assert repo.latest_evaluated(sid).completed_count == 4
        student = StudentRepository(s).get(sid)
        assert student.latest_status is VerificationStatus.FAILED
        assert student.latest_evaluated_status is VerificationStatus.INCOMPLETE

    with db.session_scope() as s:
        repo = VerificationRepository(s)
        repo.record(rec(sid, campaign_id, VerificationStatus.COMPLETED, cert_ids, done=len(cert_ids)))
    with db.session_scope() as s:
        repo = VerificationRepository(s)
        assert len(repo.history(sid)) == 3
        assert len(repo.history(sid, limit=2)) == 2
        assert repo.latest_evaluated(sid).status is VerificationStatus.COMPLETED
        old = repo.history(sid)[-1]
        assert old.completed_count == 4 and len(repo.results(old.id)) == len(cert_ids)
        counts = StudentRepository(s).count_by_latest_status(campaign_id=campaign_id)
        assert counts == {VerificationStatus.COMPLETED: 1}


def test_failed_fetch_never_stored_as_zero(db, config):
    campaign_id, cert_ids, (sid,) = seed(db, config)
    with db.session_scope() as s:
        record = VerificationRepository(s).record(rec(sid, campaign_id, VerificationStatus.FAILED))
        assert record.completed_count is None and record.missing_count is None
        assert record.results == []


@pytest.mark.parametrize("bad", ["failed_with_results", "completed_with_missing",
                                 "incomplete_with_none_missing", "partial_results", "unknown_student"])
def test_invalid_verification_rejected(db, config, bad):
    campaign_id, cert_ids, (sid,) = seed(db, config)
    outcomes_all = tuple(CertificationOutcome(c, True) for c in cert_ids)
    cases = {
        "failed_with_results": VerificationInput(sid, campaign_id, VerificationStatus.FAILED, outcomes=outcomes_all),
        "completed_with_missing": rec(sid, campaign_id, VerificationStatus.COMPLETED, cert_ids, done=2),
        "incomplete_with_none_missing": VerificationInput(sid, campaign_id, VerificationStatus.INCOMPLETE,
                                                          outcomes=outcomes_all),
        "partial_results": VerificationInput(sid, campaign_id, VerificationStatus.INCOMPLETE,
                                             outcomes=outcomes_all[:3]),
        "unknown_student": VerificationInput(9999, campaign_id, VerificationStatus.FAILED),
    }
    with pytest.raises(InvalidRecordError):
        with db.session_scope() as s:
            VerificationRepository(s).record(cases[bad])
    with db.session_scope() as s:
        assert VerificationRepository(s).history(sid) == []
        assert StudentRepository(s).get(sid).latest_status is None


def test_db_check_blocks_zero_counts_for_unread_status(db, config):
    campaign_id, _, (sid,) = seed(db, config)
    with pytest.raises(DatabaseError):
        with db.session_scope() as s:
            s.add(VerificationRecord(student_id=sid, campaign_id=campaign_id,
                                     mode=VerificationMode.MANUAL, status=VerificationStatus.FAILED,
                                     completed_count=0, missing_count=0))


# --- transactions ---------------------------------------------------------

def test_exception_in_scope_rolls_back(db):
    p = profile("abc1")
    with pytest.raises(RuntimeError):
        with db.session_scope() as s:
            StudentRepository(s).add(original_url=p.original_url, normalized_url=p.normalized_url,
                                     profile_id=p.profile_id)
            raise RuntimeError("boom")
    with db.session_scope() as s:
        assert StudentRepository(s).count() == 0


def test_failed_import_rolls_back_whole_batch(db, config):
    with db.session_scope() as s:
        campaign = CampaignRepository(s).sync_from_config(config)
        cid = campaign.id
    with pytest.raises(InvalidRecordError):
        with db.session_scope() as s:
            ImportRepository(s).import_profiles(campaign_id=cid, profiles=[profile("abc1")])
            ImportRepository(s).import_profiles(campaign_id=9999, profiles=[profile("abc2")])
    with db.session_scope() as s:
        assert StudentRepository(s).count() == 0
        assert ImportRepository(s).latest_batches() == []


# --- import handling ------------------------------------------------------

def test_duplicate_import_handling_and_history(db, config):
    with db.session_scope() as s:
        campaign_id = CampaignRepository(s).sync_from_config(config).id

    with db.session_scope() as s:
        r1 = ImportRepository(s).import_profiles(
            campaign_id=campaign_id, profiles=[profile("abc1"), profile("abc2")], source_name="a.csv")
    assert (r1.added, r1.existing, r1.held_for_review, r1.linked_to_campaign) == (2, 0, 0, 2)

    with db.session_scope() as s:
        r2 = ImportRepository(s).import_profiles(
            campaign_id=campaign_id, profiles=[profile("abc1"), profile("abc2"), profile("abc3")],
            source_name="b.csv")
    assert (r2.added, r2.existing, r2.linked_to_campaign) == (1, 2, 1)

    with db.session_scope() as s:
        assert StudentRepository(s).count() == 3
        batches = ImportRepository(s).latest_batches()
        assert [b.source_name for b in batches] == ["b.csv", "a.csv"]
        assert len(ImportRepository(s).entries(r2.batch_id)) == 3
        # import never triggers verification
        assert all(StudentRepository(s).get(i).latest_status is None for i in (1, 2, 3))


def test_repeated_row_inside_one_call_is_not_duplicated(db, config):
    with db.session_scope() as s:
        cid = CampaignRepository(s).sync_from_config(config).id
        r = ImportRepository(s).import_profiles(campaign_id=cid, profiles=[profile("abc1"), profile("abc1")])
        assert (r.added, r.existing) == (1, 1)
        assert StudentRepository(s).count() == 1


def test_case_variant_held_for_review_and_not_inserted(db, config):
    with db.session_scope() as s:
        cid = CampaignRepository(s).sync_from_config(config).id
        ImportRepository(s).import_profiles(campaign_id=cid, profiles=[profile("AbC1")])
    with db.session_scope() as s:
        r = ImportRepository(s).import_profiles(campaign_id=cid, profiles=[profile("abc1")])
        assert (r.added, r.existing, r.held_for_review, r.linked_to_campaign) == (0, 0, 1, 0)
        assert StudentRepository(s).count() == 1
        entry = ImportRepository(s).entries(r.batch_id)[0]
        assert entry.outcome.value == "case_conflict"
        assert entry.submitted_url.endswith("abc1")


def test_known_profile_lookup(db, config):
    seed(db, config, ("abc1", "abc2"))
    known = DatabaseKnownProfileLookup(db).known_normalized_urls()
    assert known == {BASE + "abc1", BASE + "abc2"}


# --- persistence ----------------------------------------------------------

def test_reopen_database_preserves_everything(tmp_path, config):
    path = tmp_path / "persist.db"
    first = Database(path)
    first.initialize()
    campaign_id, cert_ids, (sid,) = seed(first, config)
    with first.session_scope() as s:
        repo = VerificationRepository(s)
        repo.record(rec(sid, campaign_id, VerificationStatus.INCOMPLETE, cert_ids, done=5))
        repo.record(rec(sid, campaign_id, VerificationStatus.PRIVATE_INACCESSIBLE, http_status=200))
    first.close()

    second = Database(path)
    try:
        second.initialize()
        with second.session_scope() as s:
            student = StudentRepository(s).get(sid)
            assert student.latest_status is VerificationStatus.PRIVATE_INACCESSIBLE
            assert student.latest_evaluated_status is VerificationStatus.INCOMPLETE
            repo = VerificationRepository(s)
            assert len(repo.history(sid)) == 2
            assert repo.latest_evaluated(sid).completed_count == 5
            assert len(CampaignRepository(s).active_certifications(campaign_id)) == len(cert_ids)
    finally:
        second.close()