import pytest

from campaign.loader import load_campaign
from ui.app import CampaignTrackerApp
from ui.screens.dashboard import DashboardScreen


@pytest.mark.asyncio
async def test_app_shows_dashboard():
    app = CampaignTrackerApp(load_campaign())
    async with app.run_test():
        assert isinstance(app.screen, DashboardScreen)