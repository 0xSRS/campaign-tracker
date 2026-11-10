import pytest

from core.exceptions import InvalidProfileURLError
from services.profile_url import parse_profile_url

BASE = "https://www.skills.google/public_profiles/"


@pytest.mark.parametrize("raw", [
    BASE + "abc-123",
    BASE + "abc-123/",
    "https://skills.google/public_profiles/abc-123",
    "HTTPS://WWW.SKILLS.GOOGLE/public_profiles/abc-123?utm=x#frag",
    "  " + BASE + "abc-123  ",
])
def test_valid_urls_normalize_to_same_value(raw):
    assert parse_profile_url(raw).normalized == BASE + "abc-123"


def test_profile_id_case_is_preserved():
    parsed = parse_profile_url(BASE + "AbC-9")
    assert parsed.profile_id == "AbC-9"
    assert parsed.normalized.endswith("AbC-9")


def test_original_is_preserved():
    raw = "https://skills.google/public_profiles/abc/?x=1"
    assert parse_profile_url(raw).original == raw


@pytest.mark.parametrize("raw", [
    "",
    "not a url",
    "http://www.skills.google/public_profiles/a",
    "ftp://www.skills.google/public_profiles/a",
    "https://example.com/public_profiles/a",
    "https://www.skills.google/profiles/a",
    "https://www.skills.google/public_profiles/",
    "https://www.skills.google/public_profiles//",
    "https://www.skills.google/public_profiles/a/b",
    "https://www.skills.google:8080/public_profiles/a",
    "https://user@www.skills.google/public_profiles/a",
    "https://www.skills.google/public_profiles/a%20b",
    "https://www.skills.google/public_profiles/a b",
    "https://www.skills.google/public_profiles/é",
    "https://www.skills.google/public_profiles/छात्र",
])
def test_invalid_urls(raw):
    with pytest.raises(InvalidProfileURLError):
        parse_profile_url(raw)