"""The status dialogue reads what a resident asks about a request they already filed."""
import pytest

from components.intake_bot import status_dialogue as s


@pytest.mark.parametrize("text,intent", [
    ("Where is my request?", "STATUS"),
    ("any update on CO-8E774C", "STATUS"),
    ("co 8e774c", "STATUS"),
    ("The pothole is still there", "PERSISTS"),
    ("It was not fixed, the crew never came", "PERSISTS"),
    ("it hasn't been fixed", "PERSISTS"),
    ("the flooding came back after the rain", "PERSISTS"),
    ("It's fixed now, thank you", "FIXED"),
    ("show me my reports", "LIST"),
    ("hello", "GREETING"),
    ("purple elephants", "UNKNOWN"),
])
def test_intent(text, intent):
    assert s.read(text).intent == intent


def test_reference_is_normalised():
    assert s.read("status of co-8e774c please").reference == "CO-8E774C"
    assert s.read("status of CO 8E774C").reference == "CO-8E774C"


@pytest.mark.parametrize("bad", ["", None, 42, "x" * 5000, "\x00"])
def test_never_crashes(bad):
    assert s.read(bad).intent in ("UNKNOWN", "STATUS")
