"""Report export tests: temp DB, mocked fetcher, real parser/evaluator. No network."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from core.constants import VerificationStatus as V
from core.exceptions import ExportError
from database import StudentRepository, VerificationRepository
from scraper.models import AccessClass as A
from services.exporter import ReportExporter, ReportKind, safe_filename
from services.reports import FailedRecord
from services.verification_models import SelectionMode as M
from tests.profile_html import REAL, build
from tests.ui_support import build_env, result, seed, seed_data, url

NOW = datetime(2026, 11, 11, 10, 30, tzinfo=UTC)


@pytest.fixture
def env(tmp_path):
    e = build_env(tmp_path)
    e.exporter = ReportExporter(e.services.reports, tmp_path / "out", clock=lambda: NOW)
    yield e
    e.db.close()


def read(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.reader(fh))


def by_url(path, col=0):
    header, *rows = read(path)
    return header, {r[col]: dict(zip(header, r)) for r in rows}


def metrics(path):
    return {k: v for k, v in read(path)[1:]}


# ---- filenames and file safety --------------------------------------------------------
@pytest.mark.parametrize("raw, expected", [
    ("report", "report.csv"), ("Report.CSV", "Report.csv"), ("../../evil", "evil.csv"),
    ("C:\\temp\\x.csv", "x.csv"), ("my report?*.csv", "my_report.csv"), ("", "d.csv"), (None, "d.csv"),
    ("..", "d.csv"), ("CON", "CON_.csv"), ("a" * 300, "a" * 80 + ".csv"),
])
def test_safe_filename(raw, expected):
    assert safe_filename(raw, "d") == expected


def test_existing_report_is_never_overwritten(env):
    first = env.exporter.export(ReportKind.STUDENT_SUMMARY, filename="r")
    first.path.write_text("precious", encoding="utf-8")
    second = env.exporter.export(ReportKind.STUDENT_SUMMARY, filename="r")
    third = env.exporter.export(ReportKind.STUDENT_SUMMARY, filename="r")
    assert [p.path.name for p in (first, second, third)] == ["r.csv", "r_1.csv", "r_2.csv"]
    assert first.path.read_text(encoding="utf-8") == "precious"


def test_output_dir_and_filename_are_configurable(env, tmp_path):
    r = env.exporter.export(ReportKind.CAMPAIGN_SUMMARY, filename="x/../my sum", output_dir=tmp_path / "a" / "b")
    assert r.path == tmp_path / "a" / "b" / "my_sum.csv" and r.path.exists()


def test_default_filename_has_report_name_and_timestamp(env):
    assert env.exporter.export(ReportKind.FAILED_PROFILES).path.name == "failed_profiles_20261111_103000.csv"


def test_unwritable_location_gives_export_error(env, tmp_path):
    blocker = tmp_path / "file.txt"
    blocker.write_text("x")
    with pytest.raises(ExportError):
        env.exporter.export(ReportKind.STUDENT_SUMMARY, output_dir=blocker)


def test_unknown_campaign_gives_export_error(tmp_path):
    class Boom:
        def student_summary(self):
            raise LookupError("Campaign 'x' has not been created yet.")
    with pytest.raises(ExportError, match="not been created"):
        ReportExporter(Boom(), tmp_path).export(ReportKind.STUDENT_SUMMARY)


def test_spreadsheet_formulas_and_newlines_are_neutralised(tmp_path):
    rec = FailedRecord(1, "https://x/y", V.FAILED, "timeout", "=HYPERLINK(\"evil\")\nsecond line", None, NOW)
    fake = SimpleNamespace(failed_records=lambda: [rec])
    row = read(ReportExporter(fake, tmp_path).export(ReportKind.FAILED_PROFILES).path)[1]
    assert row[3] == "'=HYPERLINK(\"evil\") second line" and row[2] == ""  # no HTTP status -> blank


# ---- empty database -------------------------------------------------------------------
def test_empty_database_exports_headers_only(env):
    results = env.exporter.export_all()
    assert len(results) == 5
    for r in results:
        rows = read(r.path)
        if r.kind is ReportKind.CAMPAIGN_SUMMARY:
            assert metrics(r.path)["Total registered students"] == "0"
            assert metrics(r.path)["Completion percentage (completed / evaluated)"] == ""
        elif r.kind is ReportKind.COURSE_REPORT:
            assert len(rows) == 1 + env.campaign.required_count
        else:
            assert len(rows) == 1 and r.rows == 0


# ---- seeded data ----------------------------------------------------------------------
async def test_all_reports_after_verification(env):
    await seed(env)
    res = {r.kind: r for r in env.exporter.export_all(prefix="t")}
    assert res[ReportKind.STUDENT_SUMMARY].path.name == "t_student_summary.csv"

    header, students = by_url(res[ReportKind.STUDENT_SUMMARY].path, 1)
    assert header == ["Student ID", "Profile URL", "Verification Status", "Completed Count", "Missing Count",
                      "Completion Percentage", "Last Verified (UTC)"]
    assert {u.rsplit("/", 1)[1]: r["Verification Status"] for u, r in students.items()} == {
        "done": "COMPLETED", "part": "INCOMPLETE", "priv": "PRIVATE_INACCESSIBLE",
        "gone": "NOT_FOUND", "down": "FAILED"}
    n = env.campaign.required_count
    done, part, down = students[url("done")], students[url("part")], students[url("down")]
    assert (done["Completed Count"], done["Missing Count"], done["Completion Percentage"]) == (str(n), "0", "100.0")
    assert int(part["Completed Count"]) + int(part["Missing Count"]) == n
    for name in ("priv", "gone", "down"):  # unread profiles: blank, never 0
        r = students[url(name)]
        assert (r["Completed Count"], r["Missing Count"], r["Completion Percentage"]) == ("", "", "")
    assert datetime.fromisoformat(down["Last Verified (UTC)"]).tzinfo is not None  # ISO-8601 with offset
    assert students[url("priv")]["Last Verified (UTC)"] != ""

    _, courses = by_url(res[ReportKind.COURSE_REPORT].path)
    assert len(courses) == n
    for c in courses.values():  # 2 evaluated students: done + part
        assert int(c["Completed Students"]) + int(c["Missing Students"]) == 2
        assert c["Completion Percentage"] in {"50.0", "100.0"}

    _, missing = by_url(res[ReportKind.MISSING_CERTIFICATIONS].path)
    assert set(missing) == {url("part")}
    assert len(missing[url("part")]["Missing Courses"].split("; ")) == int(part["Missing Count"])

    _, failed = by_url(res[ReportKind.FAILED_PROFILES].path)
    assert set(failed) == {url("priv"), url("gone"), url("down")}
    assert failed[url("priv")]["Failure Classification"] == "PRIVATE_INACCESSIBLE / redirected_to_login"
    assert failed[url("gone")]["Failure Classification"] == "NOT_FOUND / not_found"
    assert failed[url("down")]["Failure Classification"] == "FAILED / server_error"
    assert failed[url("down")]["HTTP Status"] == "503" and failed[url("down")]["Diagnostic Message"] == "diag"

    m = metrics(res[ReportKind.CAMPAIGN_SUMMARY].path)
    assert (m["Total registered students"], m["Successfully evaluated students"]) == ("5", "2")
    assert (m["Completed students"], m["Incomplete students"]) == ("1", "1")
    assert (m["Failed students"], m["Inaccessible students"], m["Not-found students"],
            m["Needs-review students"]) == ("1", "1", "1", "0")
    assert m["Completion percentage (completed / evaluated)"] == "50.0"


async def test_failure_after_success_is_not_counted_as_incomplete(env):
    await seed(env)
    env.fetcher.results[url("done")] = result(url("done"), A.FAILED, 503, "server_error")
    await env.service.run(env.service.preview(M.ALL), confirmed=True)

    _, students = by_url(env.exporter.export(ReportKind.STUDENT_SUMMARY).path, 1)
    d = students[url("done")]
    assert d["Verification Status"] == "FAILED" and d["Completed Count"] == "" and d["Missing Count"] == ""
    m = metrics(env.exporter.export(ReportKind.CAMPAIGN_SUMMARY).path)
    assert (m["Completed students"], m["Incomplete students"], m["Failed students"]) == ("0", "1", "2")
    assert url("done") not in by_url(env.exporter.export(ReportKind.MISSING_CERTIFICATIONS).path)[1]


async def test_history_is_preserved_and_reports_follow_latest_attempt(env):
    await seed(env)
    env.fetcher.results[url("down")] = result(url("down"), html=build(REAL))  # server recovered
    await env.service.run(env.service.preview(M.ALL), confirmed=True)

    with env.db.session_scope() as s:
        sid = StudentRepository(s).get_by_normalized_url(url("down")).id
        history = VerificationRepository(s).history(sid)
    assert len(history) == 2 and {h.status for h in history} == {V.FAILED, V.COMPLETED}

    _, students = by_url(env.exporter.export(ReportKind.STUDENT_SUMMARY).path, 1)
    assert students[url("down")]["Verification Status"] == "COMPLETED"
    assert url("down") not in by_url(env.exporter.export(ReportKind.FAILED_PROFILES).path)[1]


async def test_unverified_students_are_exported_as_not_verified(env):
    seed_data(env)  # imported only
    _, students = by_url(env.exporter.export(ReportKind.STUDENT_SUMMARY).path, 1)
    assert {r["Verification Status"] for r in students.values()} == {"NOT_VERIFIED"}
    m = metrics(env.exporter.export(ReportKind.CAMPAIGN_SUMMARY).path)
    assert (m["Total registered students"], m["Not yet verified"], m["Failed students"]) == ("5", "5", "0")