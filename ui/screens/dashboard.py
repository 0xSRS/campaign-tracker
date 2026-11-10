"""Placeholder dashboard. Real stats arrive in later modules."""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from campaign.schemas import CampaignConfig


class DashboardScreen(Screen):
    def __init__(self, campaign: CampaignConfig) -> None:
        super().__init__()
        self.campaign = campaign

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="dashboard"):
            yield Static(f"Campaign: {self.campaign.campaign_name}", id="title")
            yield Static(
                f"Required courses: {self.campaign.required_count}",
                classes="card",
            )
            yield Static("Profiles imported: 0\nVerified: 0", classes="card")
            yield Static("Dashboard placeholder. More screens coming soon.", classes="card")
        yield Footer()