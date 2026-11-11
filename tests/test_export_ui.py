"""TUI export screen: real backend, mocked fetcher."""

from __future__ import annotations

import pytest
from textual.widgets import Button, DataTable, Static

from tests.ui_support import build_env, seed
from ui.app import CampaignTrackerApp


@pytest.fixture
def env(tmp_path):
    e = build_env(tmp_path)
    yield e
    e.db.close()


async def settle(pilot):
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


async def test_export_button_disabled_when_nothing_to_export(env):
    async with CampaignTrackerApp(env.campaign, env.services).run_test() as pilot:
        await settle(pilot)
        await pilot.press("e")
        await settle(pilot)
        assert pilot.app.screen.query_one("#export", Button).disabled


async def test_export_button_writes_all_reports_and_lists_paths(env, tmp_path):
    await seed(env)
    async with CampaignTrackerApp(env.campaign, env.services).run_test() as pilot:
        await settle(pilot)
        await pilot.press("e")
        await settle(pilot)
        screen = pilot.app.screen
        assert screen.query_one("#e-table", DataTable).row_count == 5
        button = screen.query_one("#export", Button)
        assert not button.disabled
        button.press()
        await settle(pilot)
        files = sorted((tmp_path / "exports").glob("*.csv"))
        assert len(files) == 5
        assert str(files[0].parent) in str(screen.query_one("#e-result", Static).render())