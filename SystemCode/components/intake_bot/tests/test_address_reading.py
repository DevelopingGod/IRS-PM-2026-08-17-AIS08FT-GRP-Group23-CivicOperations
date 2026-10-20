"""The Intake bot reads addresses the way New Yorkers write them, and never crashes on input.

Each case below failed before the 18 Oct repair, or guards a fix made then. Duplicate
detection matches reports by address text, so a cut-off address means a missed duplicate.
"""
import pytest

from components.intake_bot import c01_nlp_bot as c


@pytest.fixture(params=["with the word tagger", "patterns only"])
def mode(request, monkeypatch):
    """Every case runs twice: the word tagger model is too large for git, so a fresh
    clone reads addresses with the patterns alone, and that must work as well."""
    if request.param == "patterns only":
        monkeypatch.setattr(c, "_tagged_slots", lambda text: None)
    return request.param


def norm(x):
    return " ".join("".join(ch for ch in (x or "").lower() if ch.isalnum() or ch in " -").split())


@pytest.mark.parametrize("text,address,borough", [
    ("Huge pothole in front of 450 Atlantic Avenue, Brooklyn", "450 Atlantic Avenue", "BROOKLYN"),
    ("pothole at 450 atlantic ave. brooklyn", "450 atlantic ave", "BROOKLYN"),
    ("Pothole at 450 ATLANTIC AVENUE BROOKLYN NY 11217", "450 ATLANTIC AVENUE", "BROOKLYN"),
    ("No heat at 37-12 85th Street, Queens", "37-12 85th Street", "QUEENS"),
    ("Broken streetlight outside 120 W 42nd St, Manhattan", "120 W 42nd St", "MANHATTAN"),
    ("Water leaking at 2400 Grand Concourse in the Bronx", "2400 Grand Concourse", "BRONX"),
    ("Blocked hydrant at 45 East 14th Street, Manhattan.", "45 East 14th Street", "MANHATTAN"),
    ("Dead tree at 10 Richmond Terrace, Staten Island", "10 Richmond Terrace", "STATEN ISLAND"),
    ("Noise all night from 99 Bedford Ave apt 4B Brooklyn", "99 Bedford Ave", "BROOKLYN"),
    ("Garbage not collected on Nostrand Avenue, Brooklyn", "Nostrand Avenue", "BROOKLYN"),
    ("illegal parking at 300 Broadway manhattan", "300 Broadway", "MANHATTAN"),
    ("Sidewalk cracked at 1 Ocean Pkwy, Bklyn", "1 Ocean Pkwy", "BROOKLYN"),
    ("Pothole on Manhattan Ave in Greenpoint, Brooklyn", "Manhattan Ave", "BROOKLYN"),
])
def test_address_and_borough(text, address, borough, mode):
    r = c.parse(text)
    assert norm(r.slots.incident_address) == norm(address)
    assert r.slots.borough == borough


def test_slots_carry_no_trailing_punctuation(mode):
    r = c.parse("Street light out at the corner of 5th Ave and 23rd St, Manhattan")
    assert r.slots.incident_address == "5th Ave" and r.slots.cross_street == "23rd St"


def test_a_neighbourhood_is_not_a_cross_street(mode):
    assert c.parse("Pothole on Manhattan Ave in Greenpoint, Brooklyn").slots.cross_street is None


@pytest.mark.parametrize("bad", ["", "   ", "\U0001F642" * 3, "a" * 5000, "123", "\x00\n\t", None, 42])
def test_never_crashes_on_odd_input(bad):
    r = c.parse(bad)
    assert r.ready_to_submit is False


def test_history_as_plain_text_is_accepted(mode):
    r = c.parse("It is in Brooklyn", history="There is a pothole at 450 Atlantic Ave")
    assert r.slots.borough == "BROOKLYN" and norm(r.slots.incident_address) == "450 atlantic ave"
