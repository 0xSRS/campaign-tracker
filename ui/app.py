"""Main Textual application."""

from pathlib import Path

from textual.app import App
from textual.binding import Binding

from campaign.schemas import CampaignConfig
from core.constants import APP_NAME, APP_VERSION
from ui.screens.dashboard import DashboardScreen


class CampaignTrackerApp(App):
    TITLE = APP_NAME
    SUB_TITLE = f"v{APP_VERSION}"
    CSS_PATH = Path(__file__).parent / "styles" / "app.tcss"
    BINDINGS = [Binding("q", "quit", "Quit")]

    def __init__(self, campaign: CampaignConfig) -> None:
        super().__init__()
        self.campaign = campaign

    def on_mount(self) -> None:
        self.push_screen(DashboardScreen(self.campaign))