"""Shared screen chrome: header, navigation sidebar, footer, safe async loading."""

from __future__ import annotations

from collections.abc import Iterable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Footer, Header, Static

from ui.widgets import NavSidebar


class TrackerScreen(Screen):
    """Subclasses set NAV, implement compose_body() and async load(). Screens never touch the DB:
    they call `self.app.services` (the service layer) through `self.fetch(...)`."""

    NAV = ""
    BINDINGS = [Binding("r", "refresh", "Refresh")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="shell"):
            yield NavSidebar(self.NAV)
            with Vertical(id="content"):
                yield from self.compose_body()
        yield Footer()

    def compose_body(self) -> Iterable[Widget]:
        yield Static()

    def on_mount(self) -> None:
        self.reload()

    def on_resize(self, event) -> None:
        self.set_class(event.size.width < 90, "-narrow")

    def action_refresh(self) -> None:
        self.reload()

    def reload(self) -> None:
        self.run_worker(self._guarded_load(), exclusive=True, group="load")

    async def _guarded_load(self) -> None:
        try:
            await self.load()
        except Exception as exc:  # shown as a notification; traceback goes to diagnostics
            self.app.report_error(exc, f"loading {self.NAV or type(self).__name__}")

    async def load(self) -> None: ...

    async def fetch(self, fn, *args, **kwargs):
        """Run blocking service work in a thread (keeps the UI responsive)."""
        return await self.app.services.call(fn, *args, **kwargs)