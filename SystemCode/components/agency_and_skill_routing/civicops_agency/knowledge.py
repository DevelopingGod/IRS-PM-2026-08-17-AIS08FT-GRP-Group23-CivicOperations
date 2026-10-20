"""Optional historical referral knowledge (business rule R14).

``referral_knowledge.json`` is produced offline by
``research/method_comparison.py`` from the *training* split of the historical
311 data. It is a small, static table keyed by RT code (and borough when the
validation experiment justified it), so the router never needs the Parquet
file, pandas or any model library at run time.

What the knowledge is used for: attaching an explainable advisory to an
assignment whose RT code was frequently referred onward by its first agency in
the past (for example derelict vehicles with plates, DSNY -> NYPD). What it is
*not* used for: changing the catalog agency or skills. That would override the
shared catalog without an agreed correction policy (DECISIONS.md, D-05).

The artifact records the SHA-256 of the catalog it was built against. If the
loaded catalog differs, the knowledge is treated as stale and disabled by
default, because referral rates for a re-defined code would be misleading.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Union

from .schemas import ReferralAdvisory

log = logging.getLogger(__name__)

SUPPORTED_SCHEMA_VERSIONS = (1,)


class KnowledgeError(Exception):
    """The knowledge file exists but cannot be used."""


@dataclass(frozen=True)
class ReferralKnowledge:
    """Read-only referral statistics per RT code."""

    codes: Dict[str, Dict[str, Any]]
    elevated_threshold: float
    min_support: int
    uses_borough: bool
    catalog_sha256: str
    train_window: tuple
    source: Optional[Path] = None

    @classmethod
    def load(cls, path: Union[str, Path]) -> "ReferralKnowledge":
        p = Path(path)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise KnowledgeError(f"Cannot read referral knowledge {p}: {exc}") from exc
        if data.get("artifact") != "civicops_agency.referral_knowledge":
            raise KnowledgeError(f"{p} is not a civicops_agency referral knowledge file")
        if data.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
            raise KnowledgeError(f"Unsupported knowledge schema_version {data.get('schema_version')!r}")
        return cls(
            codes=data["codes"],
            elevated_threshold=float(data["elevated_threshold"]),
            min_support=int(data.get("min_support", 0)),
            uses_borough=bool(data.get("uses_borough_slot", False)),
            catalog_sha256=str(data.get("catalog_sha256", "")),
            train_window=tuple(data.get("train_window", ())),
            source=p,
        )

    def advisory(self, code: str, borough: Optional[str], review_on_elevated: bool) -> Optional[ReferralAdvisory]:
        """Return the advisory for ``code`` (and ``borough`` if known), or ``None``.

        The borough-specific rate is used only when the artifact was built with
        borough cells and CO1 supplied a recognised borough; otherwise the
        code-level rate applies. Codes below ``min_support`` training records
        are reported as ``INSUFFICIENT_EVIDENCE`` and never flagged.
        """
        info = self.codes.get(code)
        if info is None:
            return None
        rate = float(info["referral_rate_smoothed"])
        basis = "RT"
        by_borough = info.get("referral_rate_by_borough") or {}
        if self.uses_borough and borough and borough in by_borough:
            rate = float(by_borough[borough])
            basis = "RT+BOROUGH"
        n = int(info.get("train_records", 0))
        if n < self.min_support:
            level = "INSUFFICIENT_EVIDENCE"
        else:
            level = "ELEVATED" if rate >= self.elevated_threshold else "TYPICAL"
        alternatives = [a for a in info.get("matrix_agencies", []) if a != info.get("catalog_agency")]
        effect = "REVIEW_REQUIRED" if (level == "ELEVATED" and review_on_elevated) else "INFORMATIONAL"
        return ReferralAdvisory(
            risk_level=level,
            historical_referral_rate=round(rate, 4),
            basis=basis,
            elevated_threshold=self.elevated_threshold,
            train_records=n,
            matrix_relationship=str(info.get("matrix_relationship", "")),
            documented_alternative_agencies=alternatives,
            context_dimension=info.get("context_dimension"),
            observed_referral_destinations={k: int(v) for k, v in
                                            (info.get("observed_referral_destinations") or {}).items()},
            effect=effect,
            note=_note(code, info, level, rate, borough if basis == "RT+BOROUGH" else None),
        )


def _note(code: str, info: Dict[str, Any], level: str, rate: float, borough: Optional[str]) -> str:
    """Plain-English sentence that accompanies the advisory."""
    if level == "INSUFFICIENT_EVIDENCE":
        return f"Too few historical {code} requests to estimate a referral rate."
    scope = f"in {borough} " if borough else ""
    text = (f"In the training period, about {rate:.0%} of {code} requests {scope}were referred onward by "
            f"{info.get('catalog_agency')}.")
    if level != "ELEVATED":
        return text + " This is within the typical range."
    dests = info.get("observed_referral_destinations") or {}
    if dests:
        top = max(dests, key=dests.get)
        text += f" The most common recorded destination was {top}."
    if info.get("context_dimension"):
        text += (f" The agency matrix says the responsible agency depends on: {info['context_dimension']}, "
                 "which the CO1 contract does not capture.")
    return text + " The catalog assignment is unchanged."
