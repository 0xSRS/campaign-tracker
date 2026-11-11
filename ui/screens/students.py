"""Student directory (search/sort) and student details."""

from __future__ import annotations

from rich.text import Text
from textual.binding import Binding
from textual.widgets import DataTable, Input, Label, Static

from ui.formatting import bar, describe_category, fmt_dt, fmt_pct, status_text
from ui.screens.base import TrackerScreen

_COLUMNS = ("ID", "Profile URL", "Status", "Completed", "Missing", "Progress", "Last verified")
_SORT_KEYS = ("id", "url", "status", "completed", "progress", "verified")


class StudentsScreen(TrackerScreen):
    NAV = "students"
    BINDINGS = [Binding("/", "search", "Search"), Binding("o", "cycle_sort", "Sort column"),
                Binding("t", "toggle_order", "Reverse")]

    def __init__(self) -> None:
        super().__init__()
        self.summaries: list = []
        self.sort_index, self.descending = 0, False

    def compose_body(self):
        yield Label("Student directory", id="title")
        yield Input(placeholder="Search by ID, URL or status ( / )", id="search")
        yield Static("", id="s-info", classes="dim")
        yield DataTable(id="students", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#students", DataTable).add_columns(*_COLUMNS)

    async def load(self) -> None:
        self.summaries = await self.fetch(self.app.services.reports.student_summary)
        if self.is_attached:
            self.render_rows()
            self.query_one("#students", DataTable).focus()

    # ---- rendering -------------------------------------------------------------
    def _progress(self, s) -> float | None:
        n = self.app.campaign.required_count
        return None if s.completed_count is None else 100 * s.completed_count / n

    def _sort_value(self, s):
        return {
            "id": s.student_id, "url": s.url,
            "status": s.latest_status.value if s.latest_status else "",
            "completed": s.completed_count if s.completed_count is not None else -1,
            "progress": self._progress(s) if self._progress(s) is not None else -1,
            "verified": s.last_verified_at.timestamp() if s.last_verified_at else 0,
        }[_SORT_KEYS[self.sort_index]]

    def visible(self) -> list:
        q = self.query_one("#search", Input).value.strip().casefold()
        rows = [s for s in self.summaries if not q or q in str(s.student_id) or q in s.url.casefold()
                or q in (s.latest_status.label.casefold() if s.latest_status else "not verified")]
        return sorted(rows, key=self._sort_value, reverse=self.descending)

    def render_rows(self) -> None:
        table = self.query_one("#students", DataTable)
        table.clear()
        n = self.app.campaign.required_count
        rows = self.visible()
        for s in rows:
            ev = s.completed_count is not None
            table.add_row(
                str(s.student_id), s.url, status_text(s.latest_status),
                f"{s.completed_count}/{n}" if ev else "—", str(s.missing_count) if ev else "—",
                Text(f"{fmt_pct(self._progress(s))} {bar(self._progress(s), 8)}") if ev else "—",
                fmt_dt(s.last_verified_at), key=str(s.student_id))
        info = self.query_one("#s-info", Static)
        if not self.summaries:
            info.update("No students yet. Press [b]2[/b] to import a CSV.")
        elif not rows:
            info.update("No students match your search.")
        else:
            info.update(f"{len(rows)} of {len(self.summaries)} students — sorted by {_SORT_KEYS[self.sort_index]}"
                        f" ({'desc' if self.descending else 'asc'}). Enter = details.")

    # ---- interaction -----------------------------------------------------------
    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self.render_rows()

    def action_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_cycle_sort(self) -> None:
        self.sort_index = (self.sort_index + 1) % len(_SORT_KEYS)
        self.render_rows()

    def action_toggle_order(self) -> None:
        self.descending = not self.descending
        self.render_rows()

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        by_column = {0: 0, 1: 1, 2: 2, 3: 3, 5: 4, 6: 5}
        if event.column_index in by_column:
            idx = by_column[event.column_index]
            self.descending = (not self.descending) if idx == self.sort_index else False
            self.sort_index = idx
            self.render_rows()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.app.push_screen(StudentDetailScreen(int(event.row_key.value)))


class StudentDetailScreen(TrackerScreen):
    NAV = "students"
    BINDINGS = [Binding("escape", "back", "Back")]

    def __init__(self, student_id: int) -> None:
        super().__init__()
        self.student_id = student_id

    def compose_body(self):
        yield Label(f"Student #{self.student_id}", id="title")
        yield Static("Loading…", id="d-info")
        yield Label("Required courses", classes="section")
        yield DataTable(id="d-courses", cursor_type="row", zebra_stripes=True)
        yield Label("Verification history (newest first)", classes="section")
        yield DataTable(id="d-history", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#d-courses", DataTable).add_columns("Course", "State")
        self.query_one("#d-history", DataTable).add_columns("When", "Status", "Reason")

    def action_back(self) -> None:
        self.app.pop_screen()

    async def load(self) -> None:
        d = await self.fetch(self.app.services.reports.student_detail, self.student_id)
        if not self.is_attached:
            return
        info = self.query_one("#d-info", Static)
        if d is None:
            info.update("[b red]Student not found.[/]")
            return
        lines = [f"URL: {d.url}", f"Latest status: {d.latest_status.label if d.latest_status else 'Not verified'}"
                 f"   Last verified: {fmt_dt(d.last_verified_at)}"]
        if d.latest_status is not None and not d.latest_status.profile_was_read:
            lines.append(f"[yellow]Latest attempt did not read the profile: {describe_category(d.error_category)}.[/]")
            if d.evaluated_status:
                lines.append("Courses below come from the last successful evaluation.")
        info.update("\n".join(lines))
        courses = self.query_one("#d-courses", DataTable)
        courses.clear()
        for c in d.courses:
            state = {True: Text("✔ Completed", style="green"), False: Text("✘ Missing", style="red"),
                     None: Text("— Not evaluated", style="dim")}[c.completed]
            courses.add_row(c.course_name, state)
        hist = self.query_one("#d-history", DataTable)
        hist.clear()
        for h in d.history:
            hist.add_row(fmt_dt(h.verified_at), status_text(h.status), describe_category(h.error_category))