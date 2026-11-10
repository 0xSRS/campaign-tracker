from core.constants import VerificationStatus
from core.exceptions import (
    CampaignConfigError,
    CampaignTrackerError,
    ConfigurationError,
    ProfileFetchError,
    ScraperError,
)


def test_there_are_six_statuses():
    assert len(VerificationStatus) == 6


def test_status_value_is_plain_string():
    assert VerificationStatus.FAILED == "FAILED"


def test_every_status_has_a_label():
    for status in VerificationStatus:
        assert status.label


def test_only_completed_and_incomplete_count_as_read():
    readable = {s for s in VerificationStatus if s.profile_was_read}
    assert readable == {VerificationStatus.COMPLETED, VerificationStatus.INCOMPLETE}


def test_failed_is_never_a_successful_read():
    assert VerificationStatus.FAILED.profile_was_read is False


def test_exception_hierarchy():
    assert issubclass(CampaignConfigError, ConfigurationError)
    assert issubclass(ConfigurationError, CampaignTrackerError)
    assert issubclass(ProfileFetchError, ScraperError)
    assert issubclass(ScraperError, CampaignTrackerError)