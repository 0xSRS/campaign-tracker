"""CSV import: select -> preview/validate -> review duplicates/invalid -> confirm. Never scrapes."""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, Input, Label, Static

from ui.modals import FilePickerModal
from ui.native_dialog import pick_csv_file
from ui.screens.base import TrackerScreen


class ImportScreen(TrackerScreen):
    NAV = "import"
    BINDINGS = [Binding("f3", "browse", "Browse"), Binding("f4", "preview", "Preview"),
                Binding("f5", "confirm", "Import")]

    def __init__(self) -> None:
        super().__init__()
        self.result = None
        self.path: str | None = None
        self.last_report = None

    def compose_body(self):
        yield Label("Import CSV (single column: Google Skills Profile URL)", id="title")
        with Horizontal(classes="row"):
            yield Input(placeholder="Path to CSV file", id="csv-path")
            yield Button("Browse", id="browse")
            yield Button("Preview", id="preview", variant="primary")
        yield Static("Choose a file and press Preview. Nothing is stored until you confirm.", id="csv-summary")
        yield DataTable(id="csv-rows", cursor_type="row", zebra_stripes=True)
        with Horizontal(classes="row"):
            yield Button("Confirm import", id="confirm", variant="success", disabled=True)
            yield Button("Reverify existing students", id="reverify", disabled=True)
        yield Static("", id="csv-status")

    def on_mount(self) -> None:
        self.query_one("#csv-rows", DataTable).add_columns("Row", "Result", "Value / note")

    # ---- actions -------------------------------------------------------------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        {"browse": self.action_browse, "preview": self.action_preview, "confirm": self.action_confirm,
         "reverify": self._reverify}[event.button.id]()

    def on_input_submitted(self, _: Input.Submitted) -> None:
        self.action_preview()

    def action_browse(self) -> None:
        self.run_worker(self._browse(), exclusive=True, group="browse")

    async def _browse(self) -> None:
        try:
            path = await asyncio.to_thread(pick_csv_file)
        except Exception as exc:
            self.app.notify(str(exc), title="Explorer dialog unavailable", severity="warning")
            downloads = Path.home() / "Downloads"
            start = downloads if downloads.is_dir() else Path.home()
            self.app.push_screen(FilePickerModal(start), self._picked)
            return
        self._picked(path)

    def _picked(self, path: str | None) -> None:
        if path:
            self.query_one("#csv-path", Input).value = path
            self.action_preview()

    def action_preview(self) -> None:
        self.run_worker(self._preview(), exclusive=True, group="csv")

    def action_confirm(self) -> None:
        if not self.query_one("#confirm", Button).disabled:
            self.run_worker(self._import(), exclusive=True, group="csv")

    async def _preview(self) -> None:
        path = self.query_one("#csv-path", Input).value.strip().strip('"')
        self.result = None
        self.query_one("#confirm", Button).disabled = True
        table = self.query_one("#csv-rows", DataTable)
        table.clear()
        try:
            result = await self.fetch(self.app.services.service.preview_csv, path)
        except Exception as exc:
            self.query_one("#csv-summary", Static).update("[b red]Invalid CSV.[/] See the notification for details.")
            self.app.report_error(exc, f"previewing CSV {path!r}")
            return
        self.result, self.path = result, path
        s = result.summary
        self.query_one("#csv-summary", Static).update(
            f"Rows: {s.total_rows}   New: [b green]{s.new_profiles}[/]   Existing: {s.existing_profiles}   "
            f"Duplicates: {s.duplicate_rows}   Invalid: [b red]{s.invalid_urls + s.import_errors}[/]   "
            f"Blank: {s.blank_rows}   Needs review: {s.needs_review}")
        rows = [(p.row_number, "EXISTING" if p.already_known else "NEW", p.original_url) for p in result.profiles]
        rows += [(d.row_number, f"DUPLICATE ({d.kind})", f"{d.original_url} (first seen row {d.first_row_number})")
                 for d in result.duplicates]
        rows += [(i.row_number, "INVALID", f"{i.raw_value} — {i.reason}") for i in result.invalid_rows]
        rows += [(i.row_number, "ERROR", f"{i.raw_value} — {i.reason}") for i in result.error_rows]
        rows += [(i.row_number, "REVIEW", f"{i.raw_value} — {i.reason}") for i in result.review_items]
        for r in sorted(rows, key=lambda r: r[0]):
            table.add_row(str(r[0]), r[1], r[2])
        can = s.new_profiles + s.needs_review > 0
        self.query_one("#confirm", Button).disabled = not can
        self.query_one("#csv-status", Static).update(
            "Press Confirm import to store the new profiles." if can else "Nothing new to import.")

    async def _import(self) -> None:
        try:
            report = await self.fetch(self.app.services.service.import_csv, self.path)
        except Exception as exc:
            self.app.report_error(exc, f"importing CSV {self.path!r}")
            return
        self.last_report = report
        self.query_one("#confirm", Button).disabled = True
        self.query_one("#reverify", Button).disabled = not report.existing_student_ids
        msg = (f"Imported {report.stored.added} new profile(s); skipped {report.stored.existing} existing; "
               f"held for review {report.stored.held_for_review}. No profiles were scraped.")
        self.query_one("#csv-status", Static).update(msg)
        self.app.notify(msg, title="Import complete")

    def _reverify(self) -> None:
        if self.last_report and self.last_report.existing_student_ids:
            self.app.goto("verify_all", student_ids=frozenset(self.last_report.existing_student_ids))