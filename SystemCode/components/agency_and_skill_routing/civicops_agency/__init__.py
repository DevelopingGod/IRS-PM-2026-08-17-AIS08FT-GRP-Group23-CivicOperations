"""CivicOps AI - Agency Assignment and Required Skills component.

Receives the structured output of the CO1 intake bot and decides, with
explicit and explainable business rules over the shared request catalog:

* the responsible NYC agency and the required crew skill(s);
* an assignment status (ASSIGNED, HELD, BLOCKED, UNRESOLVED, INVALID);
* whether the request may proceed toward dispatch.

It does not classify text, estimate service or resolution time, rank
priority, choose crews or plan routes.

Public API::

    from civicops_agency import AgencySkillRouter
    router = AgencySkillRouter.from_catalog("data/reference/request_catalog.csv")
    result = router.assign(co1_output)
    result.to_dict()

Runtime dependencies: Python standard library only.
"""

__version__ = "1.1.0"

from .catalog import Catalog, CatalogEntry, CatalogError, CatalogIssue, load_agency_names, load_catalog  # noqa: E402
from .config import RouterConfig  # noqa: E402
from .knowledge import KnowledgeError, ReferralKnowledge  # noqa: E402
from .router import AgencySkillRouter, default_catalog_path  # noqa: E402
from .rules import RULES, BusinessRule  # noqa: E402
from .schemas import (AgencyResolution, AssignmentResult, CO1Request, Finding, Location, NextStep,  # noqa: E402
                      ReferralAdvisory, Severity, Status)
from .validators import validate_co1  # noqa: E402

__all__ = [
    "__version__",
    "AgencySkillRouter",
    "RouterConfig",
    "AssignmentResult",
    "Status",
    "AgencyResolution",
    "NextStep",
    "Severity",
    "Finding",
    "Location",
    "CO1Request",
    "ReferralAdvisory",
    "Catalog",
    "CatalogEntry",
    "CatalogIssue",
    "CatalogError",
    "ReferralKnowledge",
    "KnowledgeError",
    "RULES",
    "BusinessRule",
    "load_catalog",
    "load_agency_names",
    "validate_co1",
    "default_catalog_path",
]
