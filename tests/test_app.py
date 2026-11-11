"""TUI tests (Textual pilot). Backend is real; only the network fetcher is mocked."""

from __future__ import annotations

import asyncio

import pytest
from textual.widgets import Button, DataTable, Input, ProgressBar, Static

from tests.ui_support import build_env, make_csv, seed, seed_data, url
from ui.app import CampaignTrackerApp
from ui.diagnostics import DiagnosticsScreen
from ui.modals import ConfirmModal, DetailModal
from ui.screens.courses import CoursesScreen
from ui.screens.dashboard import DashboardScreen
from ui.screens.import_csv import ImportScreen
from ui.screens.records import RecordsScreen
from ui.screens.students import StudentDetailScreen, StudentsScreen
from ui.screens.verify import VerifyScreen


@pytest.fixture
def env(tmp_path):
    e = build_env(tmp_path)
    yield e
    e.db.close()


def make_app(env):
    return CampaignTrackerApp(env.campaign, env.services)


def text(app, selector):
    return str(app.screen.query_one(selector).render())


async def settle(pilot):
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


# ---- dashboard ---------------------------------------------------------------------
async def test_dashboard_statistics_and_course_overview(env):
    await seed(env)
    async with make_app(env).run_test() as pilot:
        await settle(pilot)
        assert isinstance(pilot.app.screen, DashboardScreen)
        card = lambda c: text(pilot.app, f"#card-{c}")
        assert "5" in card("registered") and "1" in card("completed") and "1" in card("failed")
        assert "1" in card("private") and "1" in card("not_found") and "50.0%" in card("percent")
        table = pilot.app.screen.query_one("#courses", DataTable)
        assert table.row_count == env.campaign.required_count == 10


async def test_dashboard_empty_state(env):
    async with make_app(env).run_test() as pilot:
        await settle(pilot)
        assert pilot.app.screen.query_one("#empty", Static).display is True
        assert "import" in text(pilot.app, "#empty").lower()


# ---- navigation --------------------------------------------------------------------
@pytest.mark.parametrize("key,cls", [("2", ImportScreen), ("3", VerifyScreen), ("4", VerifyScreen),
                                     ("5", VerifyScreen), ("6", StudentsScreen), ("7", RecordsScreen),
                                     ("8", RecordsScreen), ("9", RecordsScreen), ("c", CoursesScreen),
                                     ("1", DashboardScreen)])
async def test_keyboard_navigation(env, key, cls):
    async with make_app(env).run_test() as pilot:
        await settle(pilot)
        await pilot.press(key)
        await settle(pilot)
        assert isinstance(pilot.app.screen, cls)


async def test_sidebar_navigation_and_export_settings(env):
    async with make_app(env).run_test() as pilot:
        await settle(pilot)
        await pilot.press("e")
        await settle(pilot)
        assert "Nothing to export" in text(pilot.app, "#e-info")
        await pilot.press("s")
        await settle(pilot)
        assert pilot.app.screen.query_one("#settings", DataTable).row_count > 0
        await pilot.press("1")
        await settle(pilot)
        assert isinstance(pilot.app.screen, DashboardScreen)


# ---- CSV import --------------------------------------------------------------------
async def test_csv_import_preview_confirm_and_reverify(env, tmp_path):
    env.service.import_csv(make_csv("a"), is_text=True)
    f = tmp_path / "in.csv"
    f.write_text(make_csv("a", "b", "b") + "not-a-url\n")
    async with make_app(env).run_test() as pilot:
        await settle(pilot)
        await pilot.press("2")
        await settle(pilot)
        scr = pilot.app.screen
        assert isinstance(scr, ImportScreen)
        scr.query_one("#csv-path", Input).value = str(f)
        await pilot.click("#preview")
        await settle(pilot)
        summary = text(pilot.app, "#csv-summary")
        assert "New: 1" in summary and "Existing: 1" in summary and "Duplicates: 1" in summary
        assert scr.query_one("#csv-rows", DataTable).row_count == 4
        assert not scr.query_one("#confirm", Button).disabled
        with env.db.session_scope() as s:  # preview stored nothing
            from database import StudentRepository
            assert StudentRepository(s).count() == 1
        await pilot.click("#confirm")
        await settle(pilot)
        with env.db.session_scope() as s:
            assert StudentRepository(s).count() == 2
        assert env.fetcher.calls == []  # importing never scrapes
        assert not scr.query_one("#reverify", Button).disabled
        await pilot.click("#reverify")
        await settle(pilot)
        assert isinstance(pilot.app.screen, VerifyScreen)
        assert pilot.app.screen.preview.eligible_count == 1  # only the pre-existing student


async def test_invalid_csv_shows_notification_not_traceback(env, tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("Name,Email\nx,y\n")
    async with make_app(env).run_test() as pilot:
        await settle(pilot)
        await pilot.press("2")
        await settle(pilot)
        pilot.app.screen.query_one("#csv-path", Input).value = str(bad)
        await pilot.click("#preview")
        await settle(pilot)
        assert any(n.title == "Invalid CSV" for n in pilot.app._notifications)
        assert "Traceback" not in " ".join(n.message for n in pilot.app._notifications)
        assert pilot.app.diagnostics and "Traceback" in pilot.app.diagnostics[0].trace
        await pilot.press("f2")
        await pilot.pause()
        assert isinstance(pilot.app.screen, DiagnosticsScreen)


async def test_missing_csv_file_error(env):
    async with make_app(env).run_test() as pilot:
        await settle(pilot)
        await pilot.press("2")
        await settle(pilot)
        pilot.app.screen.query_one("#csv-path", Input).value = "/nope/missing.csv"
        await pilot.click("#preview")
        await settle(pilot)
        assert any(n.title == "Invalid CSV" for n in pilot.app._notifications)
        assert pilot.app.screen.query_one("#confirm", Button).disabled


# ---- verification --------------------------------------------------------------------
async def test_verification_preview_and_confirmation_gate(env):
    seed_data(env)
    async with make_app(env).run_test() as pilot:
        await settle(pilot)
        await pilot.press("3")
        await settle(pilot)
        scr = pilot.app.screen
        assert "Eligible: 5" in text(pilot.app, "#v-summary")
        await pilot.press("b")
        await pilot.pause()
        assert isinstance(pilot.app.screen, ConfirmModal)
        await pilot.press("escape")  # decline -> no requests
        await settle(pilot)
        assert env.fetcher.calls == [] and not pilot.app.verification_active
        await pilot.press("b")
        await pilot.pause()
        await pilot.press("y")
        await settle(pilot)
        assert len(env.fetcher.calls) == 5
        snap = pilot.app.progress
        assert snap.state == "finished" and snap.processed == 5 and snap.remaining == 0
        assert (snap.completed, snap.incomplete, snap.failed, snap.inaccessible, snap.not_found) == (1, 1, 1, 1, 1)
        assert scr.query_one("#v-bar", ProgressBar).progress == 5


async def test_mode_b_and_c_previews_show_exclusions(env):
    await seed(env)
    async with make_app(env).run_test() as pilot:
        await settle(pilot)
        await pilot.press("4")
        await settle(pilot)
        assert "Eligible: 1" in text(pilot.app, "#v-summary") and "Excluded: 4" in text(pilot.app, "#v-summary")
        assert pilot.app.screen.query_one("#v-exclusions", DataTable).row_count >= 2
        await pilot.press("5")
        await settle(pilot)
        assert "Eligible: 1" in text(pilot.app, "#v-summary")  # only the 503 is retryable


async def test_progress_updates_and_safe_cancellation(env):
    seed_data(env)
    env.fetcher.gate = asyncio.Event()
    async with make_app(env).run_test() as pilot:
        await settle(pilot)
        await pilot.press("3")
        await settle(pilot)
        await pilot.press("b")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        scr = pilot.app.screen
        assert pilot.app.verification_active and scr.query_one("#begin", Button).disabled
        assert not scr.query_one("#cancel", Button).disabled
        await pilot.press("x")  # cancel while the first request is in flight
        env.fetcher.gate.set()
        await settle(pilot)
        snap = pilot.app.progress
        assert snap.state == "cancelled" and snap.processed == 1 and snap.remaining == 4
        assert len(env.fetcher.calls) == 1
        assert scr.query_one("#v-bar", ProgressBar).progress == 1
        assert not pilot.app.verification_active
        assert any(n.title == "Verification cancelled" for n in pilot.app._notifications)


async def test_ui_stays_responsive_during_verification(env):
    seed_data(env)
    env.fetcher.gate = asyncio.Event()
    async with make_app(env).run_test() as pilot:
        await settle(pilot)
        await pilot.press("3")
        await settle(pilot)
        await pilot.press("b")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        await pilot.press("6")  # navigate away mid-run
        await pilot.pause()
        assert isinstance(pilot.app.screen, StudentsScreen) and pilot.app.verification_active
        env.fetcher.gate.set()
        await pilot.app.workers.wait_for_complete()
        assert pilot.app.progress.state == "finished"


async def test_database_error_during_run_is_reported(env, monkeypatch):
    seed_data(env)
    from core.exceptions import DatabaseError
    from database import VerificationRepository
    monkeypatch.setattr(VerificationRepository, "record", lambda *a, **k: (_ for _ in ()).throw(DatabaseError("x")))
    async with make_app(env).run_test() as pilot:
        await settle(pilot)
        await pilot.press("3")
        await settle(pilot)
        await pilot.press("b")
        await pilot.pause()
        await pilot.press("y")
        await settle(pilot)
        assert any(n.title == "Database error" for n in pilot.app._notifications)
        assert not pilot.app.verification_active


async def test_verify_empty_state(env):
    async with make_app(env).run_test() as pilot:
        await settle(pilot)
        await pilot.press("3")
        await settle(pilot)
        assert "Eligible: 0" in text(pilot.app, "#v-summary")
        assert pilot.app.screen.query_one("#begin", Button).disabled


# ---- directory / details / records / courses ----------------------------------------------
async def test_student_directory_search_sort_and_details(env):
    await seed(env)
    async with make_app(env).run_test() as pilot:
        await settle(pilot)
        await pilot.press("6")
        await settle(pilot)
        scr = pilot.app.screen
        table = scr.query_one("#students", DataTable)
        assert table.row_count == 5
        scr.query_one("#search", Input).value = "done"
        await pilot.pause()
        assert table.row_count == 1
        scr.query_one("#search", Input).value = "zzz"
        await pilot.pause()
        assert table.row_count == 0 and "No students match" in text(pilot.app, "#s-info")
        scr.query_one("#search", Input).value = ""
        await pilot.pause()
        scr.action_toggle_order()
        first = table.get_row_at(0)[0]
        assert first == "5"  # descending by ID
        scr.action_cycle_sort()
        table.focus()
        await pilot.press("enter")
        await settle(pilot)
        assert isinstance(pilot.app.screen, StudentDetailScreen)
        assert pilot.app.screen.query_one("#d-courses", DataTable).row_count == 10
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(pilot.app.screen, StudentsScreen)


async def test_student_details_complete_vs_never_evaluated(env):
    await seed(env)
    with env.db.session_scope() as s:
        from database import StudentRepository
        ids = {st.normalized_url: st.id for st in StudentRepository(s).list_all()}
    async with make_app(env).run_test() as pilot:
        await settle(pilot)
        await pilot.app.push_screen(StudentDetailScreen(ids[url("done")]))
        await settle(pilot)
        rows = [str(pilot.app.screen.query_one("#d-courses", DataTable).get_row_at(i)[1]) for i in range(10)]
        assert all("Completed" in r for r in rows)
        await pilot.app.push_screen(StudentDetailScreen(ids[url("priv")]))
        await settle(pilot)
        rows = [str(pilot.app.screen.query_one("#d-courses", DataTable).get_row_at(i)[1]) for i in range(10)]
        assert all("Not evaluated" in r for r in rows)  # failure never shown as "missing"
        await pilot.app.push_screen(StudentDetailScreen(9999))
        await settle(pilot)
        assert "not found" in text(pilot.app, "#d-info").lower()


async def test_records_screens_and_diagnostic_modal(env):
    await seed(env)
    async with make_app(env).run_test() as pilot:
        await settle(pilot)
        for key, expected in (("7", 2), ("8", 1), ("9", 0)):
            await pilot.press(key)
            await settle(pilot)
            assert pilot.app.screen.query_one("#records", DataTable).row_count == expected
        assert "Nothing here" in text(pilot.app, "#r-info")
        await pilot.press("8")
        await settle(pilot)
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(pilot.app.screen, DetailModal)
        assert "server_error" in str(pilot.app.screen.query_one("#detail-body").render())


async def test_course_progress_denominator_excludes_failures(env):
    await seed(env)
    async with make_app(env).run_test() as pilot:
        await settle(pilot)
        await pilot.press("c")
        await settle(pilot)
        assert "2" in text(pilot.app, "#c-denominator")  # only done + part were evaluated
        excl = text(pilot.app, "#c-excluded")
        assert "Failed: 1" in excl and "Inaccessible: 1" in excl and "Not found: 1" in excl
        first = pilot.app.screen.query_one("#courses", DataTable).get_row_at(0)
        assert tuple(first[1:3]) in (("2", "0"), ("1", "1"))


async def test_students_empty_state(env):
    async with make_app(env).run_test() as pilot:
        await settle(pilot)
        await pilot.press("6")
        await settle(pilot)
        assert "No students yet" in text(pilot.app, "#s-info")


async def test_reports_error_becomes_notification(env, monkeypatch):
    monkeypatch.setattr(type(env.services.reports), "statistics",
                        lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    async with make_app(env).run_test() as pilot:
        await settle(pilot)
        assert any(n.title == "Unexpected error" for n in pilot.app._notifications)