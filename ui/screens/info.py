"""Export (preparation only; files arrive in Module 10) and Settings screens."""

from __future__ import annotations

from textual.widgets import Button, DataTable, Label, Static

from ui.navigation import NAV
from ui.screens.base import TrackerScreen


class ExportScreen(TrackerScreen):
    NAV = "export"

    def compose_body(self):
        yield Label("Export reports", id="title")
        yield Static("Loading…", id="e-info")
        yield DataTable(id="e-table", cursor_type="row")
        yield Button("Export (coming in Module 10)", id="export", disabled=True)

    def on_mount(self) -> None:
        self.query_one("#e-table", DataTable).add_columns("Report", "Rows ready")

    async def load(self) -> None:
        r = self.app.services.reports
        data = await self.fetch(lambda: {
            "Student summary": len(r.student_summary()), "Course-wise completion": len(r.course_completion()),
            "Failed verification records": len(r.failed_records()),
            "Missing certifications": len(r.missing_certifications())})
        if not self.is_attached:
            return
        table = self.query_one("#e-table", DataTable)
        table.clear()
        for name, n in data.items():
            table.add_row(name, str(n))
        empty = data["Student summary"] == 0
        self.query_one("#e-info", Static).update(
            "Nothing to export yet — import and verify students first." if empty
            else "Data is ready. File export is implemented in Module 10.")


class SettingsScreen(TrackerScreen):
    NAV = "settings"

    def compose_body(self):
        yield Label("Settings (read-only; change via CT_* environment variables or .env)", id="title")
        yield DataTable(id="settings", cursor_type="row", zebra_stripes=True)
        yield Label("Keyboard shortcuts", classes="section")
        yield Static("\n".join(f"{i.key:>2}  {i.label}" for i in NAV)
                     + "\n q  Quit    F2  Diagnostics    r  Refresh    Esc  Back / close dialog", markup=False)

    def on_mount(self) -> None:
        table = self.query_one("#settings", DataTable)
        table.add_columns("Setting", "Value")
        s = self.app.services.settings
        for k in ("database_path", "log_file", "log_level", "request_delay_seconds", "max_concurrency",
                  "request_timeout_seconds", "max_retries", "rate_limit_default_pause_seconds"):
            table.add_row(k, str(getattr(s, k)))