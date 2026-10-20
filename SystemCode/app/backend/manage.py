"""The tech team's command line. Nothing here is reachable from the web.

  py -m app.backend.manage create-user --role tech --name "Your name"
  py -m app.backend.manage create-user --role staff --name "Dispatcher A"
  py -m app.backend.manage create-user --role crew --name "Crew lead" --crew CRW-DOT-054
  py -m app.backend.manage reset-pin STF-XXXX-XXXX
  py -m app.backend.manage list-users
  py -m app.backend.manage import-backlog --count 150
  py -m app.backend.manage team-accounts > credentials.txt   (one tech, one staff, and the crews with a route)
  py -m app.backend.manage demo-accounts      (the fixed demo IDs and PINs in demo_accounts.local.json, kept off git)
  py -m app.backend.manage reset --yes        (empties every request; accounts are kept)

The first tech account has to come from here; after that the tech portal creates the others.
A PIN is printed once and never stored in readable form.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import sys

from . import accounts, backlog, db, service, switches


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="py -m app.backend.manage", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    cu = sub.add_parser("create-user")
    cu.add_argument("--role", choices=["staff", "crew", "tech"], required=True)
    cu.add_argument("--name", required=True)
    cu.add_argument("--crew")
    rp = sub.add_parser("reset-pin")
    rp.add_argument("user_id")
    sub.add_parser("list-users")
    ib = sub.add_parser("import-backlog")
    ib.add_argument("--count", type=int, default=150)
    ib.add_argument("--force", action="store_true")
    ta = sub.add_parser("team-accounts")
    ta.add_argument("--crews", type=int, default=6)
    da = sub.add_parser("demo-accounts")
    da.add_argument("--file", default="demo_accounts.local.json", help="the file with the fixed accounts, or - to read it from standard input")
    rs = sub.add_parser("reset")
    rs.add_argument("--yes", action="store_true")
    a = p.parse_args(argv)

    db.init()
    accounts.init()
    switches.init()
    try:
        if a.cmd == "create-user":
            u = accounts.create_user(a.role, a.name, "command line", a.crew)
            print(f"Created {u['role']} account for {u['name']}{' (' + u['crew_id'] + ')' if u['crew_id'] else ''}")
            print(f"  user ID: {u['user_id']}")
            print(f"  PIN:     {u['pin']}   (shown once, write it down now)")
        elif a.cmd == "reset-pin":
            u = accounts.reset_pin(a.user_id, "command line")
            print(f"New PIN for {u['user_id']}: {u['pin']}   (shown once)")
        elif a.cmd == "list-users":
            info = accounts.list_users()
            for u in info["accounts"]:
                print(f"{u['id']:<16} {u['role']:<6} {'active' if u['active'] else 'off':<7} {u['name']}"
                      f"{' · ' + u['crew_id'] if u['crew_id'] else ''}")
            print(f"{info['residents']} resident IDs")
        elif a.cmd == "import-backlog":
            print(f"Running {a.count} real NYC 311 requests through every component...")
            with contextlib.redirect_stderr(io.StringIO()):
                info = backlog.import_backlog(a.count, a.force)
            print(f"Imported {info['imported']} requests, {info['per_borough']} per borough, each borough's most recent "
                  "recorded requests, replayed so each borough's newest is a few minutes old.")
        elif a.cmd == "team-accounts":
            team_accounts(a.crews)
        elif a.cmd == "demo-accounts":
            demo_accounts(a.file)
        elif a.cmd == "reset":
            if not a.yes:
                print("This empties every request, job and outcome. Run again with --yes to confirm.")
                return 1
            service.reset_all()
            with db.conn() as c:
                c.execute("delete from meta where key = 'backlog_import'")
            print("Emptied. Accounts and component switches are kept.")
    except (accounts.AuthError, RuntimeError, ValueError) as e:
        print(f"Not done: {e}", file=sys.stderr)
        return 1
    return 0


def team_accounts(crews: int) -> None:
    """Accounts for teammates trying the app: one tech, one staff, and crew accounts for the
    crews Crew deployment gives the longest routes, so a crew has stops once staff approve them.
    Printed once, as a credentials sheet to redirect into a file kept off git."""
    plan = service.crew_plan(wait=60).get("plan") or {}
    routed = [c["crew_id"] for c in sorted(plan.get("crews", []), key=lambda c: -len(c["stops"]))][:crews]
    made = [accounts.create_user("tech", "Tech lead", "command line"),
            accounts.create_user("staff", "Dispatcher", "command line")]
    made += [accounts.create_user("crew", f"Crew {cid}", "command line", cid) for cid in routed]
    print("Civic Operations, team test accounts. Keep this file private: it is not in git.")
    print("Open http://localhost:8080, then the footer link 'City staff, crew and tech team sign in'.")
    print("Residents need no account here: register with any email on the home page.\n")
    print(f"{'Role':<6} {'User ID':<16} {'PIN':<7} Crew")
    for u in made:
        print(f"{u['role']:<6} {u['user_id']:<16} {u['pin']:<7} {u['crew_id'] or ''}")
    if routed:
        print("\nTo see a crew's stops: as staff, open Crew plan and approve that crew's route, then sign in as the crew.")


def demo_accounts(path: str) -> None:
    """The same IDs and PINs every time, so a demo can be rehearsed and run from one sheet.
    The first run writes the file with new PINs; later runs, here or in Docker, reuse it."""
    import json
    import secrets
    from pathlib import Path
    if path == "-":
        spec = json.load(sys.stdin)
    else:
        f = Path(path)
        if not f.exists():
            pin = lambda: f"{secrets.randbelow(10 ** 6):06d}"
            spec = {"note": "Fixed demo accounts. Keep this file private: it is not in git.",
                    "accounts": [{"user_id": "TEC-DEMO-0001", "role": "tech", "name": "Tech lead", "pin": pin()},
                                 {"user_id": "STF-DEMO-0001", "role": "staff", "name": "Dispatcher", "pin": pin()}]
                    + [{"user_id": f"CRW-DEMO-{n:04d}", "role": "crew", "name": f"Crew CRW-DOT-{n:03d}",
                        "crew_id": f"CRW-DOT-{n:03d}", "pin": pin()} for n in range(27, 33)]}
            f.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
            print(f"Wrote {f} with new PINs.")
        spec = json.loads(f.read_text(encoding="utf-8"))
    for u in spec["accounts"]:
        accounts.set_fixed(u["user_id"], u["role"], u["name"], str(u["pin"]), "command line", u.get("crew_id"))
    print(f"{len(spec['accounts'])} demo accounts are ready. Their IDs and PINs are in the file.")


if __name__ == "__main__":
    sys.exit(main())
