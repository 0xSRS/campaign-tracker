"""Load and validate the campaign configuration file."""

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from campaign.schemas import CampaignConfig
from core.config import get_settings
from core.exceptions import CampaignConfigError

logger = logging.getLogger(__name__)


def load_campaign(path: Path | None = None) -> CampaignConfig:
    """Read the campaign JSON file and return a validated CampaignConfig.

    Every failure is converted to CampaignConfigError so callers only
    need to handle one exception type.
    """
    path = path or get_settings().campaign_file

    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise CampaignConfigError(f"Campaign file not found: {path}") from exc
    except OSError as exc:
        raise CampaignConfigError(f"Cannot read campaign file {path}: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CampaignConfigError(f"Campaign file is not valid JSON: {exc}") from exc

    try:
        config = CampaignConfig.model_validate(data)
    except ValidationError as exc:
        raise CampaignConfigError(f"Campaign file failed validation:\n{exc}") from exc

    logger.info(
        "Loaded campaign '%s' with %d required courses",
        config.campaign_name,
        config.required_count,
    )
    return config