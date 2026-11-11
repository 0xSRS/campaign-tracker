"""Pure tests: eligibility rules and progress tracking. No DB, no network."""

from core.constants import VerificationStatus as V
from services.progress import ProgressTracker
from services.selection import RETRYABLE_FAILURE_CATEGORIES, select_students
from services.verification_models import ExclusionReason as R, SelectionMode as M, StudentSnapshot as S


def snaps():
    return [
        S(1, "u1", None), S(2, "u2", V.COMPLETED), S(3, "u3", V.INCOMPLETE),
        S(4, "u4", V.FAILED, "timeout"), S(5, "u5", V.FAILED, "server_error"), S(6, "u6", V.FAILED, "ssl_error"),
        S(7, "u7", V.FAILED, "parser_error"), S(8, "u8", V.PRIVATE_INACCESSIBLE, "redirected_to_login"),
        S(9, "u9", V.NOT_FOUND), S(10, "u10", V.NEEDS_REVIEW), S(11, "u11", V.FAILED, "rate_limited"),
    ]


def ids(rows):
    return [r.student_id for r in rows]


def reasons(excluded):
    return {r.student_id: r.reason for r in excluded}


def test_mode_a_all_including_completed():
    e, x = select_students(M.ALL, snaps())
    assert ids(e) == list(range(1, 12)) and x == []


def test_mode_b_only_successful_incomplete():
    e, x = select_students(M.INCOMPLETE, snaps())
    assert ids(e) == [3]
    r = reasons(x)
    assert r[1] is R.NEVER_VERIFIED and r[2] is R.ALREADY_COMPLETED
    assert all(r[i] is R.LATEST_NOT_INCOMPLETE for i in (4, 5, 6, 7, 8, 9, 10, 11))


def test_mode_c_only_retryable_failures():
    e, x = select_students(M.RETRY_FAILED, snaps())
    assert ids(e) == [4, 5, 11]
    r = reasons(x)
    assert r[6] is R.NON_RETRYABLE_FAILURE and r[7] is R.NON_RETRYABLE_FAILURE
    assert r[8] is R.PRIVATE_OR_INACCESSIBLE and r[9] is R.NOT_FOUND and r[10] is R.NEEDS_REVIEW
    assert r[2] is R.LATEST_EVALUATED_OK and r[3] is R.LATEST_EVALUATED_OK and r[1] is R.NEVER_VERIFIED
    assert all(x_.detail for x_ in x)


def test_subset_restricts_selection():
    e, x = select_students(M.ALL, snaps(), subset={2, 3})
    assert ids(e) == [2, 3] and reasons(x)[1] is R.NOT_IN_SUBSET


def test_retryable_set_excludes_permanent_categories():
    assert not {"ssl_error", "invalid_url", "parser_error", "response_too_large"} & RETRYABLE_FAILURE_CATEGORIES


def test_progress_counts_and_remaining():
    seen = []
    t = ProgressTracker(M.ALL, 6, seen.append)
    t.start()
    for s in (V.COMPLETED, V.INCOMPLETE, V.FAILED, V.PRIVATE_INACCESSIBLE, V.NOT_FOUND, V.NEEDS_REVIEW):
        t.record(s)
    snap = t.finish("finished")
    assert (snap.completed, snap.incomplete, snap.failed, snap.inaccessible, snap.not_found,
            snap.needs_review) == (1, 1, 1, 1, 1, 1)
    assert snap.processed == 6 and snap.remaining == 0 and snap.total_selected == 6
    assert seen[0].state == "running" and seen[1].remaining == 5 and seen[-1].state == "finished"


def test_broken_listener_never_breaks_tracking():
    def boom(_):
        raise RuntimeError("ui died")
    t = ProgressTracker(M.ALL, 2, boom)
    t.start()
    t.record(V.COMPLETED)
    assert t.snapshot().processed == 1


def test_stopped_run_keeps_remaining():
    t = ProgressTracker(M.INCOMPLETE, 5)
    t.start()
    t.record(V.INCOMPLETE)
    snap = t.finish("cancelled")
    assert snap.remaining == 4 and snap.state == "cancelled"