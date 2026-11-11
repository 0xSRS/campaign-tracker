"""Application entry point."""

import logging
import sys

from campaign.loader import load_campaign
from core.exceptions import CampaignTrackerError
from core.logging_setup import setup_logging
from services.bootstrap import build_services
from ui.app import CampaignTrackerApp

logger = logging.getLogger(__name__)


def main() -> int:
    setup_logging()
    try:
        campaign = load_campaign()
        services = build_services(campaign)
    except CampaignTrackerError as exc:
        logger.error("Startup failed: %s", exc)
        print(f"Startup failed: {exc}", file=sys.stderr)
        return 1

    CampaignTrackerApp(campaign, services).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())