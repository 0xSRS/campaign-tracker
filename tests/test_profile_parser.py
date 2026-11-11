"""Parser tests: offline, HTML built from the verified structure. No network."""

from pathlib import Path

from scraper.parser import PARSER_VERSION, ExtractionStatus as S, parse_profile
from scraper.parser.normalize import normalize_title
from tests.profile_html import REAL, build, campaign

C = campaign()
FIXTURE = Path(__file__).parent / "fixtures" / "profile_full.html"


def tids(r):
    return {m.template_id for m in r.matched}


def test_all_required_present_from_saved_fixture():
    r = parse_profile(FIXTURE.read_text("utf-8"), C)
    assert r.status is S.SUCCESS_WITH_CREDENTIALS and r.success
    assert len(r.credentials) == 10 and len(r.matched) == 10
    assert r.missing_template_ids == () and not r.unmatched and not r.warnings
    assert r.parser_version == PARSER_VERSION
    assert all(m.match_method == "template_id" for m in r.matched)
    first = r.credentials[0]
    assert first.credential_id == "29358117" and first.template_id == 959  # ids differ
    assert first.earned_text == "Earned Nov 3, 2026 EST" and "profile-badge" in first.evidence


def test_some_present_reports_missing():
    r = parse_profile(build(REAL[:4]), C)
    assert len(r.matched) == 4 and len(r.missing_template_ids) == 6
    assert tids(r) == {959, 658, 756, 1586}


def test_no_credentials_is_success_empty():
    r = parse_profile(build([]), C)
    assert r.status is S.SUCCESS_EMPTY and r.success and r.credentials == ()
    assert len(r.missing_template_ids) == 10


def test_duplicate_credentials():
    same_id = parse_profile(build([REAL[0], REAL[0]]), C)
    assert len(same_id.credentials) == 1 and len(same_id.matched) == 1
    assert any("duplicate_credential_ignored" in w for w in same_id.warnings)
    # Same course awarded twice under different ids: counted once, extra id kept.
    twice = parse_profile(build([REAL[0], ("Explore Generative AI in Agent Platform", "999", 959)]), C)
    assert len(twice.credentials) == 2 and len(twice.matched) == 1
    assert twice.matched[0].extra_credential_ids == ("999",)


def test_unknown_credentials_not_counted():
    r = parse_profile(build([("Some Other Badge", "111", 5000), REAL[0]]), C)
    assert len(r.matched) == 1 and [c.title for c in r.unmatched] == ["Some Other Badge"]


def test_missing_credential_id_is_none_not_invented():
    r = parse_profile(build([REAL[0]], card_kw={"with_id": False}), C)
    cred = r.credentials[0]
    assert cred.credential_id is None and "credential_id_missing" in cred.warnings
    assert len(r.matched) == 1 and r.matched[0].credential_id is None


def test_missing_dialog_falls_back_to_exact_title_with_warning():
    r = parse_profile(build([REAL[0]], with_dialogs=False), C)
    assert r.matched[0].match_method == "title" and r.credentials[0].template_id is None
    assert any("matched_by_title_only" in w for w in r.warnings)


def test_template_id_beats_title_and_conflicts_not_counted():
    # Right template id, renamed title: still matched, with warning.
    r = parse_profile(build([("Renamed Basics Course", "1", 754)]), C)
    assert tids(r) == {754} and any("title_mismatch" in w for w in r.warnings)
    # Required title but a different template id: not counted.
    r2 = parse_profile(build([("The Basics of Google Cloud Compute", "2", 99999)]), C)
    assert not r2.matched and len(r2.unmatched) == 1 and any("conflict" in w for w in r2.warnings)


def test_changed_structure_is_unsupported_not_empty():
    html = build(REAL[:2]).replace("class='profile-badge'", "class='badge-card'")
    r = parse_profile(html, C)
    assert r.status is S.UNSUPPORTED_STRUCTURE and r.credentials is None and not r.success
    r2 = parse_profile(build(REAL[:2]).replace("profile-badges", "badges-list"), C)
    assert r2.status is S.INCOMPLETE_OR_UNEXPECTED and r2.credentials is None


def test_container_with_unrecognised_children_is_unsupported():
    r = parse_profile(build([], extra_in_container="<section>new layout</section>"), C)
    assert r.status is S.UNSUPPORTED_STRUCTURE and r.credentials is None


def test_dynamic_content_not_in_static_html():
    shell = "<html><head><script src='app.js'></script></head><body><div id='root'></div></body></html>"
    r = parse_profile(shell, C)
    assert r.status is S.UNSUPPORTED_STRUCTURE and r.credentials is None
    assert "JavaScript" in r.warnings[0]


def test_complete_page_without_badge_section_is_empty_with_warning():
    r = parse_profile(build([], container=False), C)
    assert r.status is S.SUCCESS_EMPTY and r.credentials == ()
    assert "badge_section_absent_assumed_empty" in r.warnings[0]


def test_truncated_page_without_badge_section_is_not_empty():
    html = build([], container=False).replace("</main>", "").replace("</html>", "")
    r = parse_profile(html, C)
    assert r.status is S.INCOMPLETE_OR_UNEXPECTED and r.credentials is None


def test_partially_unreadable_cards_are_incomplete():
    html = build(REAL[:3]).replace("<span class='ql-title-medium l-mts'>\nStore", "<span class='x'>\nStore")
    r = parse_profile(html, C)
    assert r.status is S.INCOMPLETE_OR_UNEXPECTED and r.credentials is None
    assert len(r.partial_credentials) == 2 and not r.matched


def test_malformed_html_never_raises_or_fakes_empty():
    for bad in ("<div class='profile-badges'><div class='profile-badge'><span", "<<<>>>", "\x00\x01", "   ", None, 42):
        r = parse_profile(bad, C)
        assert r.credentials is None and not r.success, bad
    broken = FIXTURE.read_text("utf-8")[:3000]  # truncated page
    assert parse_profile(broken, C).credentials is None


def test_parser_exception_becomes_parser_error():
    r = parse_profile(build(REAL[:1]), object())  # campaign without .courses
    assert r.status is S.PARSER_ERROR and r.credentials is None and r.error


def test_ambiguous_title_matching():
    from types import SimpleNamespace as N
    camp = N(courses=[N(name="Intro Course", template_id=1), N(name="intro  course", template_id=2)])
    r = parse_profile(build([("Intro Course", "5", 3)], with_dialogs=False), camp)
    assert not r.matched and len(r.unmatched) == 1 and any("ambiguous_title" in w for w in r.warnings)


def test_normalisation_is_controlled_not_fuzzy():
    assert normalize_title("Store, Process \u2013 Console") == normalize_title("store,  process - console")
    assert normalize_title("Using the Google Cloud Speech API") != normalize_title("Using the Google Cloud Speech APIs")
    r = parse_profile(build([("Build a Website on Google Cloud Platform", "7", 1)], with_dialogs=False), C)
    assert not r.matched  # near-miss title must not match