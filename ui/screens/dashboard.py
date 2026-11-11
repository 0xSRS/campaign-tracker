"""Dashboard: campaign statistics + course overview."""

from __future__ import annotations

from textual.containers import Grid
from textual.widgets import Label, Static

from ui.formatting import fmt_pct
from ui.screens.base import TrackerScreen
from ui.widgets import CourseProgressTable, StatCard

_CARDS = (
    ("registered", "Registered students", "info"),
    ("completed", "Completed", "ok"),
    ("incomplete", "Incomplete", "warn"),
    ("failed", "Failed verifications", "bad"),
    ("private", "Private / inaccessible", "warn"),
    ("not_found", "Not found", "bad"),
    ("review", "Needs review", "info"),
    ("percent", "Campaign completion", "ok"),
)
_STATUS_KEY = {"completed": "COMPLETED", "incomplete": "INCOMPLETE", "failed": "FAILED",
               "private": "PRIVATE_INACCESSIBLE", "not_found": "NOT_FOUND", "review": "NEEDS_REVIEW"}


class DashboardScreen(TrackerScreen):
    NAV = "dashboard"

    def compose_body(self):
        yield Label(f"Campaign: {self.app.campaign.campaign_name}", id="title")
        yield Static("", id="empty", classes="notice")
        with Grid(id="cards"):
            for cid, label, variant in _CARDS:
                yield StatCard(label, variant, id=f"card-{cid}")
        yield Label("Course-wise progress", classes="section")
        yield CourseProgressTable(id="courses")
        yield Static("", id="dash-note", classes="dim")

    def on_resize(self, event) -> None:
        self.query_one("#cards", Grid).styles.grid_size_columns = 4 if event.size.width >= 110 else 2

    async def load(self) -> None:
        reports = self.app.services.reports
        stats, courses = await self.fetch(lambda: (reports.statistics(), reports.course_completion()))
        if not self.is_attached:
            return
        by = stats.by_latest_status
        self.query_one("#card-registered", StatCard).set_value(stats.registered)
        for cid, key in _STATUS_KEY.items():
            self.query_one(f"#card-{cid}", StatCard).set_value(by.get(key, 0))
        self.query_one("#card-percent", StatCard).set_value(fmt_pct(stats.completion_rate))
        self.query_one("#courses", CourseProgressTable).show(courses)
        empty = self.query_one("#empty", Static)
        empty.display = stats.registered == 0
        empty.update("No students yet. Press [b]2[/b] to import a CSV of profile URLs.")
        self.query_one("#dash-note", Static).update(
            f"Completion % = completed / successfully evaluated ({stats.evaluated_students}). "
            f"Not yet verified: {stats.never_verified}. Failed/inaccessible profiles are excluded from percentages.")