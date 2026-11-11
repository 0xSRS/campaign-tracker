"""Builds profile HTML mirroring the verified Google Skills structure (no network)."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from types import SimpleNamespace

PROFILE = "00000000-0000-4000-8000-000000000000"  # dummy; real fixture was sanitised

# (title, awarded_id, template_id) taken from the real saved profile.
REAL = [
    ("Explore Generative AI in Agent Platform", "29358117", 959),
    ("Store, Process, and Manage Data on Google Cloud - Console", "29357827", 658),
    ("Using the Google Cloud Speech API", "29355816", 756),
    ("Create Your First Gemini Enterprise Application", "29354838", 1586),
    ("Build a Website on Google Cloud", "29354625", 638),
    ("Analyze Images with the Cloud Vision API", "29324396", 633),
    ("Develop AI-Powered Prototypes in Google AI Studio", "29324043", 1426),
    ("Implement Speech and Language Solutions with Pre-trained APIs", "29323360", 700),
    ("Configure Service Accounts and IAM Roles for Google Cloud", "29315986", 702),
    ("The Basics of Google Cloud Compute", "29315446", 754),
]


def campaign():
    data = json.loads((Path(__file__).parent.parent / "campaign" / "courses.json").read_text("utf-8"))
    try:
        from campaign.schemas import CampaignConfig
        return CampaignConfig.model_validate(data)
    except ImportError:  # pydantic missing in this environment; parser is duck-typed
        return SimpleNamespace(courses=[SimpleNamespace(**c) for c in data["courses"]])


def card(i, title, cid, *, with_id=True, with_modal=True, earned="Earned Nov  3, 2026 EST"):
    href = f"https://www.skills.google/public_profiles/{PROFILE}/badges/{cid}"
    anchor = f'<a class="badge-image" href="{href}"><img role="presentation" src="x" />\n</a>' if with_id \
        else '<a class="badge-image"><img role="presentation" src="x" />\n</a>'
    btn = (f"<ql-button ariaLabel='Learn more about {escape(title)}' label='Learn more' "
           f"modal='public-profile-award-modal-{i}' outlined tip='Learn more'></ql-button>") if with_modal else ""
    return (f"<div class='profile-badge'>\n{anchor}<span class='ql-title-medium l-mts'>\n{escape(title)}\n</span>\n"
            f"<span class='ql-body-medium l-mbs'>\n{earned}\n</span>\n{btn}\n</div>\n")


def dialog(i, title, tid, *, with_link=True):
    link = f"<ql-button href='/course_templates/{tid}' label='Learn more' method='get' slot='action' text>" \
           "Learn more\n</ql-button>" if with_link else ""
    return (f"<ql-dialog dismissalLabel='Dismiss' headline='{escape(title)}' id='public-profile-award-modal-{i}'>"
            f"<p>desc</p>\n{link}\n</ql-dialog>\n")


def build(badges, *, container=True, heading=True, with_dialogs=True, extra_in_container="",
          card_kw=None, dialog_kw=None):
    """badges: list of (title, awarded_id, template_id)."""
    h1 = "<h1 class='ql-display-small'>\nTest Learner\n</h1>" if heading else ""
    cards = "".join(card(i, t, c, **(card_kw or {})) for i, (t, c, _) in enumerate(badges))
    box = f"<div class='profile-badges'>\n{cards}{extra_in_container}</div>" if container else ""
    dialogs = "".join(dialog(i, t, tid, **(dialog_kw or {})) for i, (t, _, tid) in enumerate(badges)) \
        if with_dialogs else ""
    return (f"<!DOCTYPE html><html lang='en'><head><title>Test Learner | Google Skills</title>"
            f"<script>window.gon={{}};</script></head><body><main id='jump-content'>"
            f"<div class='text--center'>{h1}<p>Member since 2026</p>{box}</div></main>\n{dialogs}</body></html>")


if __name__ == "__main__":  # regenerate the saved fixture
    out = Path(__file__).parent / "fixtures"
    out.mkdir(exist_ok=True)
    (out / "profile_full.html").write_text(build(REAL), encoding="utf-8")