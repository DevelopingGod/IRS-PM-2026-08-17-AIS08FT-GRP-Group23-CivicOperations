"""The tech team's on and off switch for each component, read by every part of the app.

A switched off component runs its agreed safe default instead, exactly as if its code were
missing, and every screen says so. The switch is stored in the database, so it applies to
every request, queue view, alert and answer from that moment on, for every user.

Intake bot + Emergency check cannot be switched off: with it off, a 911 situation could be
filed as a routine request. That is a safety rule, not a setting.
"""
from __future__ import annotations

from . import data, db

LOCKED = {"Intake bot + Emergency check":
          "Safety: the emergency check screens every message, so it can never be switched off."}

SCHEMA = """
create table if not exists component_switches (
  name text primary key, enabled integer not null, changed_by text, changed_at text);
"""


def init() -> None:
    with db.conn() as c:
        c.executescript(SCHEMA)


def off() -> set[str]:
    """Names of the components switched off right now."""
    with db.conn() as c:
        return {r["name"] for r in c.execute("select name from component_switches where enabled = 0")}


def is_on(name: str) -> bool:
    return name not in off()


def history() -> dict[str, dict]:
    with db.conn() as c:
        return {r["name"]: dict(r) for r in c.execute("select * from component_switches")}


def set_switch(name: str, enabled: bool, by: str, known: set[str]) -> None:
    if name not in known:
        raise ValueError("No component with that name.")
    if name in LOCKED and not enabled:
        raise ValueError(LOCKED[name])
    with db.conn() as c:
        c.execute("insert or replace into component_switches values (?,?,?,?)",
                  (name, 1 if enabled else 0, by, data.now_new_york().isoformat(timespec="seconds")))
