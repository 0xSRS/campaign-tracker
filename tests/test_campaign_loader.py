import json

import pytest

from campaign.loader import load_campaign
from core.exceptions import CampaignConfigError

EXPECTED_IDS = {754, 702, 700, 1426, 633, 638, 1586, 658, 756, 959}


def write_json(tmp_path, data):
    path = tmp_path / "courses.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_real_file_has_ten_courses():
    config = load_campaign()
    assert config.required_count == 10


def test_real_file_has_expected_template_ids():
    assert load_campaign().template_ids == EXPECTED_IDS


def test_missing_file(tmp_path):
    with pytest.raises(CampaignConfigError, match="not found"):
        load_campaign(tmp_path / "nope.json")


def test_invalid_json(tmp_path):
    path = tmp_path / "courses.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(CampaignConfigError, match="not valid JSON"):
        load_campaign(path)


def test_empty_course_list(tmp_path):
    path = write_json(tmp_path, {"campaign_name": "X", "courses": []})
    with pytest.raises(CampaignConfigError):
        load_campaign(path)


def test_duplicate_template_id(tmp_path):
    path = write_json(tmp_path, {
        "campaign_name": "X",
        "courses": [
            {"name": "A", "template_id": 1},
            {"name": "B", "template_id": 1},
        ],
    })
    with pytest.raises(CampaignConfigError, match="Duplicate template_id"):
        load_campaign(path)


def test_duplicate_name_ignores_case(tmp_path):
    path = write_json(tmp_path, {
        "campaign_name": "X",
        "courses": [
            {"name": "Same Name", "template_id": 1},
            {"name": "same name", "template_id": 2},
        ],
    })
    with pytest.raises(CampaignConfigError, match="Duplicate course name"):
        load_campaign(path)


def test_non_positive_template_id(tmp_path):
    path = write_json(tmp_path, {
        "campaign_name": "X",
        "courses": [{"name": "A", "template_id": 0}],
    })
    with pytest.raises(CampaignConfigError):
        load_campaign(path)