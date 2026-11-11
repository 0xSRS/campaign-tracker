"""Export and Settings screens."""

from __future__ import annotations

from pathlib import Path

from textual.widgets import Button, DataTable, Label, Static

from services.exporter import ReportExporter, ReportKind
from ui.navigation import NAV
from ui.screens.base import TrackerScreen


class ExportScreen(TrackerScreen):
    NAV = "export"

    def compose_body(self):
        yield Label("Export reports", id="title")
        yield Static("Loading…", id="e-info", markup=False)
        yield DataTable(id="e-table", cursor_type="row")
        yield Button("Export all reports (CSV)", id="export", disabled=True)
        yield Static("", id="e-result", markup=False)

    def on_mount(self) -> None:
        self.query_one("#e-table", DataTable).add_columns("Report", "Rows ready")

    def _exporter(self) -> ReportExporter:
        s = self.app.services.settings
        folder = getattr(s, "export_dir", None) or Path(s.database_path).parent / "exports"
        return ReportExporter(self.app.services.reports, folder)

    async def load(self) -> None:
        exporter = self._exporter()
        data = await self.fetch(lambda: {k: len(exporter.build(k)[1]) for k in ReportKind})
        if not self.is_attached:
            return
        table = self.query_one("#e-table", DataTable)
        table.clear()
        for kind, n in data.items():
            table.add_row(kind.label, str(n))
        empty = data[ReportKind.STUDENT_SUMMARY] == 0
        self.query_one("#e-info", Static).update(
            "Nothing to export yet — import and verify students first." if empty
            else f"Reports are saved as CSV in: {exporter.output_dir}. Existing files are never overwritten.")
        self.query_one("#export", Button).disabled = empty

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "export":
            return
        event.stop()
        event.button.disabled = True
        try:
            results = await self.fetch(self._exporter().export_all)
        except Exception as exc:  # shown as a notification; details go to diagnostics
            self.app.report_error(exc, "exporting reports")
        else:
            self.query_one("#e-result", Static).update(
                "Saved:\n" + "\n".join(f"{r.path}  ({r.rows} rows)" for r in results))
            self.notify(f"Exported {len(results)} reports.")
        finally:
            event.button.disabled = False


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