"""Prepares a separate database for the UAT run, so testing never touches real data.

Imports the real 311 backlog, creates a tech, a staff and a crew account, gives the crew six
jobs, and writes the IDs and PINs for the test run to the path given.

Run by the UAT's global setup:  CIVICOPS_DB=<test db> py 01_Application/app/scripts/uat_prepare.py <accounts.json>
"""
import contextlib
import io
import json
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CIVICOPS_WARMUP", "0")

if "CIVICOPS_DB" not in os.environ:
    sys.exit("Set CIVICOPS_DB to the test database, so real data is never touched.")
Path(os.environ["CIVICOPS_DB"]).unlink(missing_ok=True)

from app.backend import accounts, backlog, db, service, switches  # noqa: E402


def main(out: str) -> None:
    db.init()
    accounts.init()
    switches.init()
    with contextlib.redirect_stderr(io.StringIO()):
        backlog.import_backlog(150)
    tech = accounts.create_user("tech", "UAT tech lead", "uat")
    staff = accounts.create_user("staff", "UAT dispatcher", "uat")
    crew_id = "CRW-DOT-054"
    crew = accounts.create_user("crew", "UAT road crew", "uat", crew_id)
    jobs = [q["request_id"] for q in service.queue(record=False)["items"]
            if q["agency"] == "DOT" and not q["on_hold"]][:6]
    for rid in jobs:
        service.assign(rid, crew_id, staff["user_id"])
    Path(out).write_text(json.dumps({
        "tech": {"id": tech["user_id"], "pin": tech["pin"]},
        "staff": {"id": staff["user_id"], "pin": staff["pin"]},
        "crew": {"id": crew["user_id"], "pin": crew["pin"], "crew_id": crew_id, "jobs": jobs},
    }, indent=2), encoding="utf-8")
    print(f"UAT database ready: 150 requests, 3 accounts, {len(jobs)} crew jobs")


if __name__ == "__main__":
    main(sys.argv[1])
