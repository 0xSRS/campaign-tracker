from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from ui.formatting import fmt_dt


class DiagnosticsScreen(ModalScreen[None]):
    """Technical details of errors seen this session (kept out of the normal UI)."""

    BINDINGS = [Binding("escape", "close", "Close")]

    def compose(self) -> ComposeResult:
        items = self.app.diagnostics
        text = "\n\n".join(f"[{fmt_dt(d.at)}] {d.title} — {d.context}\n{d.summary}\n{d.trace}" for d in reversed(items))
        with Vertical(classes="dialog wide"):
            yield Label("Diagnostics", classes="dialog-title")
            with VerticalScroll():
                yield Static(text or "No errors recorded this session.", markup=False, id="diag-body")
            yield Button("Close (Esc)", id="close")

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, _: Button.Pressed) -> None:
        self.dismiss(None)