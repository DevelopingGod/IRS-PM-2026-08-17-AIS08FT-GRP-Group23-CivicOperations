"""The adapters for teammates' components: shared fields in, their code called, fields out."""
from app.backend.plugins import registry

POTHOLE = {"request_type": "RT001", "citizen_key": "trusted", "text": "deep pothole",
           "slots": {"incident_address": "450 Atlantic Ave", "borough": "BROOKLYN"}}


def earlier(**extra):
    return {**POTHOLE, "request_id": "CO-1", "citizen_key": "typical", "status": "OPEN",
            "created_at": "2026-10-18T09:00:00-04:00", **extra}


def test_duplicate_detection_links_an_open_report_at_the_same_address():
    out = registry._duplicate({**POTHOLE, "created_at": "2026-10-18T09:30:00-04:00",
                               "duplicate_candidates": [earlier()]})
    assert out["duplicate_relation"] == "DUPLICATE" and out["duplicate_of"] == "CO-1"
    assert out["is_primary"] is False and out["corroboration_count"] == 2


def test_duplicate_detection_never_links_different_problems():
    other = earlier(request_type="RT021")
    out = registry._duplicate({**POTHOLE, "created_at": "2026-10-18T09:30:00-04:00",
                               "duplicate_candidates": [other]})
    assert out["duplicate_relation"] == "NEW" and out["is_primary"] is True


def test_a_closed_report_at_the_same_place_is_a_recurrence_for_credibility():
    closed = earlier(status="CLOSED", closed_at="2026-10-15T12:00:00-04:00",
                     created_at="2026-10-14T09:00:00-04:00", closures_same_place_and_type=["CO-0"])
    out = registry._duplicate({**POTHOLE, "created_at": "2026-10-18T09:30:00-04:00",
                               "duplicate_candidates": [closed]})
    assert out["duplicate_relation"] == "RECURRENCE"
    assert out["closures_same_place_and_type"] == ["CO-0", "CO-1"]


def test_external_factors_without_coordinates_says_it_could_not_decide():
    out = registry._external(POTHOLE)
    assert out["external_factor"] is None and out["external_incomplete_reason"] == "MISSING_COORDINATES"


def test_fake_request_detection_never_rejects():
    out = registry._fake({**POTHOLE, "credibility_score": 20.0, "observation_count": 9,
                          "recent_reports_by_citizen_1h": 8})
    assert out["fake_action"] in ("PROCEED", "LOWER_PRIORITY", "HOLD_FOR_REVIEW")


def test_only_hold_for_review_counts_as_a_suspected_fake_for_urgency():
    msgs = {a: registry.urgency_message({**POTHOLE, "fake_action": a})["suspected_fake_request"]
            for a in ("PROCEED", "LOWER_PRIORITY", "HOLD_FOR_REVIEW")}
    assert msgs == {"PROCEED": False, "LOWER_PRIORITY": False, "HOLD_FOR_REVIEW": True}
