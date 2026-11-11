"""Failed / inaccessible / needs-review lists (one reusable screen) with diagnostic inspection."""

from __future__ import annotations

from textual.binding import Binding
from textual.widgets import Button, DataTable, Label, Static
from ui.modals import ConfirmModal, DetailModal

from core.constants import VerificationStatus as V
from ui.formatting import describe_category, fmt_dt, status_text
from ui.screens.base import TrackerScreen
from ui.screens.students import StudentDetailScreen

_KINDS = {
    "inaccessible": ("Inaccessible profiles", {V.PRIVATE_INACCESSIBLE, V.NOT_FOUND},
                     "Private or missing profiles are not retried automatically; ask the student to fix access."),
    "failed": ("Failed records", {V.FAILED}, "Technical failures. Retryable ones can be rerun with mode C (key 5)."),
    "review": ("Needs-review records", {V.NEEDS_REVIEW}, "Ambiguous results that need a manual look."),
}


class RecordsScreen(TrackerScreen):
    BINDINGS = [Binding("d", "inspect", "Diagnostics"), Binding("p", "mark_private", "Mark private")]

    def __init__(self, kind: str) -> None:
        super().__init__()
        self.kind = kind
        self.NAV = kind
        self.records: dict[int, object] = {}

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "mark-private":
            event.stop()
            self.action_mark_private()

    def action_mark_private(self) -> None:
        r = self.records.get(self._current_id())
        if self.kind != "review" or r is None:
            return
        if r.status is V.PRIVATE_INACCESSIBLE:
            self.notify("Already marked as private / inaccessible.")
            return
        body = (f"{r.url}\n\nUse this only after you have checked the profile yourself.\n"
                "It will be recorded as Private / inaccessible and not retried automatically. "
                "The earlier result stays in the history.")
        self.app.push_screen(ConfirmModal("Mark as private?", body, "Mark private"),
                             lambda ok: self._mark(r.student_id) if ok else None)

    def _mark(self, student_id: int) -> None:
        self.run_worker(self._do_mark(student_id), exclusive=True, group="mark")

    async def _do_mark(self, student_id: int) -> None:
        try:
            await self.fetch(self.app.services.service.mark_private, student_id)
        except Exception as exc:
            self.app.report_error(exc, "marking profile as private")
            return
        self.notify("Marked as private / inaccessible.")
        await self.load()

    def compose_body(self):
        title, _, hint = _KINDS[self.kind]
        yield Label(title, id="title")
        yield Static(hint, classes="dim")
        yield Static("", id="r-info")
        yield DataTable(id="records", cursor_type="row", zebra_stripes=True)
        if self.kind == "review":
            yield Button("Mark selected as private / inaccessible (p)", id="mark-private", variant="warning")

    def on_mount(self) -> None:
        self.query_one("#records", DataTable).add_columns("ID", "Profile URL", "Status", "Reason", "HTTP", "Verified")

    async def load(self) -> None:
        rows = await self.fetch(self.app.services.reports.failed_records)
        if not self.is_attached:
            return
        wanted = _KINDS[self.kind][1]
        rows = [r for r in rows if r.status in wanted]
        self.records = {r.student_id: r for r in rows}
        table = self.query_one("#records", DataTable)
        table.clear()
        for r in rows:
            table.add_row(str(r.student_id), r.url, status_text(r.status), describe_category(r.error_category),
                          str(r.http_status or "—"), fmt_dt(r.verified_at), key=str(r.student_id))
        self.query_one("#r-info", Static).update(
            f"{len(rows)} record(s). D = diagnostic details, Enter = student." if rows
            else "Nothing here — no records in this category.")
        table.focus()

    def _current_id(self) -> int | None:
        table = self.query_one("#records", DataTable)
        if not table.row_count:
            return None
        return int(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)

    def action_inspect(self) -> None:
        r = self.records.get(self._current_id())
        if r is None:
            return
        body = (f"Student: {r.student_id}\nURL: {r.url}\nStatus: {r.status.label}\n"
                f"Reason: {describe_category(r.error_category)} ({r.error_category or 'n/a'})\n"
                f"HTTP status: {r.http_status or 'n/a'}\nVerified: {fmt_dt(r.verified_at)}\n\n"
                f"Diagnostic:\n{r.diagnostic or '(none recorded)'}")
        self.app.push_screen(DetailModal("Diagnostic details", body))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.app.push_screen(StudentDetailScreen(int(event.row_key.value)))