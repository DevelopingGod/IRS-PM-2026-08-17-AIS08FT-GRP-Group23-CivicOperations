"""CivicOps AI - External Factors: Critical Facility Proximity.

Determines whether a geolocated service request is adjacent to a relevant critical or sensitive
facility (hospital, school, childcare, senior care, fire station, police station), using a
local OpenStreetMap cache, and returns a binary factor (1 / 0 / null) with spatial evidence for
the Urgency component. It does not compute urgency, priority or dispatch decisions.
"""

__version__ = "1.0.0"

from .evaluator import CriticalFacilityEvaluator, to_integer_factor  # noqa: E402
from .osm_loader import OSMCacheError  # noqa: E402
from .proximity_rules import ProximityRuleset, RulesetError  # noqa: E402
from .schemas import EvaluationResult, RequestContext  # noqa: E402

__all__ = ["CriticalFacilityEvaluator", "EvaluationResult", "RequestContext", "ProximityRuleset",
           "RulesetError", "OSMCacheError", "to_integer_factor", "__version__"]
