"""Router configuration.

All thresholds that could change a routing outcome live here, with documented
defaults, so they can be set explicitly by the integrating team rather than
being buried in rule code (business rule R09 requires a configurable,
documented review threshold).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RouterConfig:
    """Behavioural switches for :class:`~civicops_agency.router.AgencySkillRouter`.

    Attributes:
        min_confidence: If set (0-1), requests whose CO1 ``confidence`` is
            below it are HELD for review (rule R09). Default ``None``: CO1
            already turns its confidence into ``ready_to_submit`` and
            ``needs_human``, and no data is available here to calibrate a
            second threshold (the historical records carry no intake
            confidence). Enable it only with a threshold agreed with CO1.
        review_on_elevated_referral_risk: If true, an ``ELEVATED`` historical
            referral advisory (rule R14) HOLDS the request for review instead
            of being informational. Default ``False`` because shared rule 7
            says nobody is refused automatically and the advisory is based on
            history, not on facts about this request; see the benchmark for
            the review-rate trade-off.
        normalize_request_type: strip whitespace and upper-case the request
            type before lookup (``" rt022 "`` -> ``"RT022"``), with a warning.
        strict_catalog: refuse to start if the catalog contains conflicts,
            incomplete entries or malformed codes, instead of resolving only
            the affected codes to ``UNRESOLVED``.
        use_referral_knowledge: attach R14 advisories when a knowledge file is
            available.
        allow_stale_knowledge: use a knowledge file built for a different
            catalog version. Default ``False``.
    """

    min_confidence: Optional[float] = None
    review_on_elevated_referral_risk: bool = False
    normalize_request_type: bool = True
    strict_catalog: bool = False
    use_referral_knowledge: bool = True
    allow_stale_knowledge: bool = False

    def __post_init__(self) -> None:
        if self.min_confidence is not None and not 0.0 <= float(self.min_confidence) <= 1.0:
            raise ValueError("min_confidence must be between 0 and 1, or None")
