"""Main Textual application.

Architecture: App owns the AppServices (service layer) and the single verification worker. Screens are thin
views that call services via `app.services.call` (thread) and render results. Verification runs as an app-level
async worker, so navigating between screens never interrupts it; screens re-attach to `app.progress`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from textual.app import App
from textual.binding import Binding
from textual.screen import ModalScreen

from campaign.schemas import CampaignConfig
from core.constants import APP_NAME, APP_VERSION
from services.bootstrap import AppServices
from services.verification_models import ProgressSnapshot, SelectionMode, VerificationPreview
from ui.diagnostics import DiagnosticsScreen
from ui.errors import STATE_MESSAGES, Diagnostic, describe_error, make_diagnostic
from ui.modals import ConfirmModal
from ui.navigation import NAV
from ui.screens.courses import CoursesScreen
from ui.screens.dashboard import DashboardScreen
from ui.screens.import_csv import ImportScreen
from ui.screens.info import ExportScreen, SettingsScreen
from ui.screens.records import RecordsScreen
from ui.screens.students import StudentsScreen
from ui.screens.verify import VerifyScreen

logger = logging.getLogger(__name__)

_FACTORIES = {
    "dashboard": lambda **kw: DashboardScreen(),
    "import": lambda **kw: ImportScreen(),
    "verify_all": lambda **kw: VerifyScreen(SelectionMode.ALL, kw.get("student_ids")),
    "verify_incomplete": lambda **kw: VerifyScreen(SelectionMode.INCOMPLETE),
    "retry_failed": lambda **kw: VerifyScreen(SelectionMode.RETRY_FAILED),
    "students": lambda **kw: StudentsScreen(),
    "inaccessible": lambda **kw: RecordsScreen("inaccessible"),
    "failed": lambda **kw: RecordsScreen("failed"),
    "review": lambda **kw: RecordsScreen("review"),
    "courses": lambda **kw: CoursesScreen(),
    "export": lambda **kw: ExportScreen(),
    "settings": lambda **kw: SettingsScreen(),
}


class CampaignTrackerApp(App):
    TITLE = APP_NAME
    SUB_TITLE = f"v{APP_VERSION}"
    CSS_PATH = Path(__file__).parent / "styles" / "app.tcss"
    BINDINGS = [
        Binding("q", "request_quit", "Quit"),
        Binding("f2", "diagnostics", "Diagnostics"),
        *[Binding(i.key, f"goto('{i.name}')", i.label, show=False) for i in NAV],
    ]

    def __init__(self, campaign: CampaignConfig, services: AppServices) -> None:
        super().__init__()
        self.campaign = campaign
        self.services = services
        self.diagnostics: list[Diagnostic] = []
        self.progress: ProgressSnapshot | None = None
        self.verification_active = False

    def on_mount(self) -> None:
        self.theme = "textual-dark"
        self.push_screen(DashboardScreen())

    async def on_unmount(self) -> None:
        await self.services.close()

    # ---- navigation ------------------------------------------------------------
    def goto(self, name: str, **kw) -> None:
        if isinstance(self.screen, ModalScreen):
            return  # global shortcuts are inert while a dialog is open
        while len(self.screen_stack) > 2:
            self.pop_screen()
        self.switch_screen(_FACTORIES[name](**kw))

    def action_goto(self, name: str) -> None:
        self.goto(name)

    def action_diagnostics(self) -> None:
        if not isinstance(self.screen, DiagnosticsScreen):
            self.push_screen(DiagnosticsScreen())

    def action_request_quit(self) -> None:
        if not self.verification_active:
            self.exit()
            return

        def answer(ok: bool | None) -> None:
            if ok:
                self.services.service.cancel()
                self.exit()

        self.push_screen(ConfirmModal("Quit during verification?",
                                      "A verification run is active. Completed students are already saved; "
                                      "quitting stops the rest.", "Quit"), answer)

    # ---- errors ----------------------------------------------------------------
    def report_error(self, exc: BaseException, context: str = "") -> None:
        msg = describe_error(exc)
        logger.error("%s: %s", context or msg.title, exc, exc_info=exc)
        self.diagnostics.append(make_diagnostic(exc, context))
        self.notify(msg.text, title=msg.title, severity=msg.severity, timeout=8)

    # ---- verification ----------------------------------------------------------
    def start_verification(self, preview: VerificationPreview) -> None:
        if self.verification_active:
            return
        self.verification_active = True
        self.progress = None
        self.run_worker(self._run_verification(preview), group="verification", exit_on_error=False)

    def _on_progress(self, snap: ProgressSnapshot) -> None:
        self.progress = snap
        screen = self.screen
        if hasattr(screen, "show_progress") and screen.is_attached:
            screen.show_progress(snap)

    async def _run_verification(self, preview: VerificationPreview) -> None:
        try:
            summary = await self.services.service.run(preview, confirmed=True, on_progress=self._on_progress)
            msg = STATE_MESSAGES.get(summary.progress.state)
            if msg:
                self.notify(f"{msg.text} ({summary.progress.processed}/{summary.progress.total_selected} processed)",
                            title=msg.title, severity=msg.severity, timeout=8)
        except Exception as exc:
            self.report_error(exc, "verification run")
        finally:
            self.verification_active = False
            screen = self.screen
            if hasattr(screen, "run_finished"):
                screen.run_finished()