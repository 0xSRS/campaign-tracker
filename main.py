"""Application entry point."""

import logging
import sys

from campaign.loader import load_campaign
from core.exceptions import CampaignTrackerError
from core.logging_setup import setup_logging
from ui.app import CampaignTrackerApp

logger = logging.getLogger(__name__)


def main() -> int:
    setup_logging()
    try:
        campaign = load_campaign()
    except CampaignTrackerError as exc:
        logger.error("Startup failed: %s", exc)
        print(f"Startup failed: {exc}", file=sys.stderr)
        return 1

    CampaignTrackerApp(campaign).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())