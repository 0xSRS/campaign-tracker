"""Verification screen for modes A/B/C: preview -> confirm -> live progress -> safe cancel."""

from __future__ import annotations

from textual.binding import Binding
from textual.containers import Grid, Horizontal
from textual.widgets import Button, DataTable, Label, ProgressBar, Static

from services.verification_models import ExclusionReason, ProgressSnapshot, SelectionMode
from ui.modals import ConfirmModal
from ui.screens.base import TrackerScreen
from ui.widgets import StatCard

_TITLES = {SelectionMode.ALL: "Verify all students", SelectionMode.INCOMPLETE: "Verify incomplete students",
           SelectionMode.RETRY_FAILED: "Retry failed profiles"}
_NAV = {SelectionMode.ALL: "verify_all", SelectionMode.INCOMPLETE: "verify_incomplete",
        SelectionMode.RETRY_FAILED: "retry_failed"}
_COUNTERS = (("processed", "Processed", "info"), ("completed", "Completed", "ok"),
             ("incomplete", "Incomplete", "warn"), ("failed", "Failed", "bad"),
             ("inaccessible", "Inaccessible", "warn"), ("not_found", "Not found", "bad"),
             ("needs_review", "Needs review", "info"), ("remaining", "Remaining", "info"))
_STATE_TEXT = {"idle": "Ready", "running": "Running…", "finished": "Finished", "cancelled": "Cancelled",
               "stopped_rate_limited": "Stopped: rate limited by the server", "error": "Stopped by an error"}


class VerifyScreen(TrackerScreen):
    BINDINGS = [Binding("b", "begin", "Begin"), Binding("x", "cancel_run", "Cancel run")]

    def __init__(self, mode: SelectionMode, student_ids=None) -> None:
        super().__init__()
        self.mode, self.student_ids = mode, student_ids
        self.NAV = _NAV[mode]
        self.preview = None

    def compose_body(self):
        subset = " (selected existing students only)" if self.student_ids is not None else ""
        yield Label(_TITLES[self.mode] + subset, id="title")
        yield Static("Loading preview…", id="v-summary")
        yield DataTable(id="v-exclusions", cursor_type="row", zebra_stripes=True)
        with Horizontal(classes="row"):
            yield Button("Begin verification (B)", id="begin", variant="success", disabled=True)
            yield Button("Cancel run (X)", id="cancel", variant="error", disabled=True)
        yield Static("Ready", id="v-state")
        yield ProgressBar(total=1, show_eta=False, id="v-bar")
        with Grid(id="v-counts"):
            for cid, label, variant in _COUNTERS:
                yield StatCard(label, variant, id=f"v-{cid}")
        yield Static("", id="v-current")

    def on_mount(self) -> None:
        self.query_one("#v-exclusions", DataTable).add_columns("Excluded because", "Students", "Example")
        app = self.app
        if app.progress is not None:  # re-entering during/after a run
            self.show_progress(app.progress)
        self.set_running(app.verification_active)

    async def load(self) -> None:
        ids = self.student_ids
        self.preview = await self.fetch(lambda: self.app.services.service.preview(self.mode, student_ids=ids))
        if not self.is_attached:
            return
        p = self.preview
        self.query_one("#v-summary", Static).update(
            f"Mode: [b]{_TITLES[self.mode]}[/]   Registered: {p.total_registered}   "
            f"Eligible: [b green]{p.eligible_count}[/]   Excluded: [b yellow]{p.excluded_count}[/]")
        table = self.query_one("#v-exclusions", DataTable)
        table.clear()
        examples: dict[ExclusionReason, str] = {}
        for row in p.excluded:
            examples.setdefault(row.reason, row.detail)
        for reason, n in sorted(p.exclusion_counts.items(), key=lambda kv: -kv[1]):
            table.add_row(reason.value.replace("_", " ").capitalize(), str(n), examples[reason])
        self.set_running(self.app.verification_active)

    # ---- state ---------------------------------------------------------------
    def set_running(self, running: bool) -> None:
        eligible = self.preview.eligible_count if self.preview else 0
        self.query_one("#begin", Button).disabled = running or eligible == 0
        self.query_one("#cancel", Button).disabled = not running

    def show_progress(self, snap: ProgressSnapshot) -> None:
        bar = self.query_one("#v-bar", ProgressBar)
        bar.update(total=max(snap.total_selected, 1), progress=snap.processed)
        for cid, _, _ in _COUNTERS:
            self.query_one(f"#v-{cid}", StatCard).set_value(getattr(snap, cid))
        state = _STATE_TEXT.get(snap.state, snap.state)
        if snap.error:
            state += f" — {snap.error}"
        self.query_one("#v-state", Static).update(f"State: [b]{state}[/]")
        self.query_one("#v-current", Static).update(
            f"Last processed profile: {snap.last_profile}" if snap.last_profile else "")

    def run_finished(self) -> None:
        self.set_running(False)
        self.reload()

    # ---- actions -------------------------------------------------------------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        (self.action_begin if event.button.id == "begin" else self.action_cancel_run)()

    def action_begin(self) -> None:
        p = self.preview
        if p is None or p.eligible_count == 0 or self.app.verification_active:
            return
        gap = self.app.services.settings.request_delay_seconds
        body = (f"Mode: {_TITLES[self.mode]}\nEligible students: {p.eligible_count}\n"
                f"Excluded students: {p.excluded_count}\n\n"
                f"One request will be sent per eligible student, about {gap:g}s apart.\nStart verification?")
        self.app.push_screen(ConfirmModal("Confirm verification", body, "Start"), self._confirmed)

    def _confirmed(self, ok: bool | None) -> None:
        if ok and self.preview is not None:
            self.app.start_verification(self.preview)
            self.set_running(True)

    def action_cancel_run(self) -> None:
        if self.app.verification_active:
            self.app.services.service.cancel()
            self.query_one("#v-state", Static).update("State: [b]Cancelling after the request in flight…[/]")
            self.query_one("#cancel", Button).disabled = True