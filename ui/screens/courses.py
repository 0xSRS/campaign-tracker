from __future__ import annotations

from textual.widgets import Label, Static

from ui.screens.base import TrackerScreen
from ui.widgets import CourseProgressTable


class CoursesScreen(TrackerScreen):
    NAV = "courses"

    def compose_body(self):
        yield Label("Course-wise progress", id="title")
        yield Static("", id="c-denominator")
        yield CourseProgressTable(id="courses")
        yield Static("", id="c-excluded", classes="notice")

    async def load(self) -> None:
        reports = self.app.services.reports
        stats, courses = await self.fetch(lambda: (reports.statistics(), reports.course_completion()))
        if not self.is_attached:
            return
        by = stats.by_latest_status
        self.query_one("#courses", CourseProgressTable).show(courses)
        self.query_one("#c-denominator", Static).update(
            f"Successfully evaluated students (denominator): [b]{stats.evaluated_students}[/b] of {stats.registered}"
            if stats.registered else "No students yet. Press [b]2[/b] to import a CSV.")
        self.query_one("#c-excluded", Static).update(
            "Excluded from percentages — "
            f"Failed: {by.get('FAILED', 0)}   Inaccessible: {by.get('PRIVATE_INACCESSIBLE', 0)}   "
            f"Not found: {by.get('NOT_FOUND', 0)}   Needs review: {by.get('NEEDS_REVIEW', 0)}   "
            f"Not verified: {stats.never_verified}")