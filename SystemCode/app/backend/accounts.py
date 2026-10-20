"""Who is using the system, and what they may see.

Four roles, each with its own user ID:
* resident  registers with an email only and receives a user ID to keep. Later sign ins use the
            ID alone. Only a keyed hash of the email is stored, to stop one email registering
            twice; the email itself is never stored, so a resident is pseudonymous.
* staff     city staff: the ranked queue, crew assignment, Admin questions, alerts.
* crew      a field crew: only its own jobs.
* tech      the tech team: component health, switching components on and off, accounts.
Staff, crew and tech sign in with their ID and a 6 digit PIN, because they see residents'
reports and can change the system. PINs are stored with PBKDF2 and a salt per user, and five
wrong PINs lock the ID for 15 minutes.

Sessions are random tokens. Only their hash is stored.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta

from . import data, db

SECRET = os.environ.get("CIVICOPS_SALT", "local-development-only").encode()
ROLES = ("resident", "staff", "crew", "tech")
PREFIX = {"resident": "NYC", "staff": "STF", "crew": "CRW", "tech": "TEC"}
SESSION_HOURS = {"resident": 24 * 30, "staff": 12, "crew": 12, "tech": 8}
MAX_TRIES, LOCK_MINUTES = 5, 15
# No 0/O or 1/I/L, so an ID read aloud or copied by hand is not misread.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"

SCHEMA = """
create table if not exists users (
  id text primary key, role text not null, email_hash text unique, pin_hash text,
  name text, crew_id text, created_at text not null, created_by text, last_seen text,
  active integer not null default 1, failed integer not null default 0, locked_until text);
create table if not exists sessions_auth (
  token_hash text primary key, user_id text not null, role text not null,
  created_at text not null, expires_at text not null);
"""


class AuthError(ValueError):
    """A sign in or registration that cannot go ahead, with a message safe to show."""


def init() -> None:
    with db.conn() as c:
        c.executescript(SCHEMA)


def _now() -> datetime:
    return data.now_new_york()


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def _keyed(text: str) -> str:
    return hmac.new(SECRET, text.encode(), hashlib.sha256).hexdigest()


def _pin_hash(pin: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 200_000)
    return f"{salt}${dk.hex()}"


def _pin_ok(pin: str, stored: str | None) -> bool:
    if not stored or "$" not in stored:
        return False
    salt, _ = stored.split("$", 1)
    return hmac.compare_digest(_pin_hash(pin, salt), stored)


def _new_id(role: str) -> str:
    with db.conn() as c:
        while True:
            body = "".join(secrets.choice(ALPHABET) for _ in range(8))
            uid = f"{PREFIX[role]}-{body[:4]}-{body[4:]}"
            if not c.execute("select 1 from users where id = ?", (uid,)).fetchone():
                return uid


def normalise_email(email: str) -> str:
    e = (email or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", e):
        raise AuthError("Please enter a complete email address.")
    return e


def normalise_id(user_id: str) -> str:
    return re.sub(r"[^A-Z0-9-]", "", (user_id or "").upper().replace(" ", "-"))


def _session(uid: str, role: str) -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    with db.conn() as c:
        c.execute("insert into sessions_auth values (?,?,?,?,?)",
                  (_keyed(token), uid, role, _iso(now), _iso(now + timedelta(hours=SESSION_HOURS[role]))))
        c.execute("update users set last_seen = ? where id = ?", (_iso(now), uid))
    return token


# ------------------------------------------------------------------ residents
def register_resident(email: str) -> dict:
    """Email in, user ID out. The ID is shown once; the resident must keep it."""
    h = _keyed("email|" + normalise_email(email))
    with db.conn() as c:
        if c.execute("select 1 from users where email_hash = ?", (h,)).fetchone():
            raise AuthError("This email already has a user ID. Please sign in with the ID you saved.")
    uid = _new_id("resident")
    with db.conn() as c:
        c.execute("insert into users (id, role, email_hash, created_at, created_by) values (?,?,?,?,?)",
                  (uid, "resident", h, _iso(_now()), "self"))
    return {"user_id": uid, "role": "resident", "token": _session(uid, "resident")}


def sign_in(user_id: str, pin: str | None = None) -> dict:
    uid = normalise_id(user_id)
    with db.conn() as c:
        u = c.execute("select * from users where id = ?", (uid,)).fetchone()
    if not u or not u["active"]:
        raise AuthError("We do not recognise that user ID.")
    if u["role"] == "resident":
        return {"user_id": uid, "role": "resident", "token": _session(uid, "resident")}
    now = _now()
    if u["locked_until"] and datetime.fromisoformat(u["locked_until"]) > now:
        raise AuthError("Too many wrong PINs. This ID is locked for 15 minutes.")
    if not _pin_ok(pin or "", u["pin_hash"]):
        failed = u["failed"] + 1
        locked = _iso(now + timedelta(minutes=LOCK_MINUTES)) if failed >= MAX_TRIES else None
        with db.conn() as c:
            c.execute("update users set failed = ?, locked_until = ? where id = ?",
                      (0 if locked else failed, locked, uid))
        raise AuthError("That ID and PIN do not match.")
    with db.conn() as c:
        c.execute("update users set failed = 0, locked_until = null where id = ?", (uid,))
    return {"user_id": uid, "role": u["role"], "name": u["name"], "crew_id": u["crew_id"],
            "token": _session(uid, u["role"])}


def from_token(token: str | None) -> dict | None:
    """The signed in user, or None for a missing, unknown or expired token."""
    if not token:
        return None
    with db.conn() as c:
        s = c.execute("select * from sessions_auth where token_hash = ?", (_keyed(token),)).fetchone()
        if not s:
            return None
        if datetime.fromisoformat(s["expires_at"]) < _now():
            c.execute("delete from sessions_auth where token_hash = ?", (_keyed(token),))
            return None
        u = c.execute("select id, role, name, crew_id, active from users where id = ?", (s["user_id"],)).fetchone()
    return dict(u) if u and u["active"] else None


def sign_out(token: str | None) -> None:
    if token:
        with db.conn() as c:
            c.execute("delete from sessions_auth where token_hash = ?", (_keyed(token),))


# ------------------------------------------------------------------ staff, crew and tech accounts
def create_user(role: str, name: str, created_by: str, crew_id: str | None = None) -> dict:
    """The tech team creates staff, crew and tech IDs. The PIN is shown once, then only its hash is kept."""
    if role not in ("staff", "crew", "tech"):
        raise AuthError("Only staff, crew and tech accounts are created here.")
    name = (name or "").strip()[:60]
    if not name:
        raise AuthError("Please give the account a name.")
    if role == "crew":
        if not crew_id or crew_id not in data.crews():
            raise AuthError("Choose the crew this account belongs to.")
    uid = _new_id(role)
    pin = f"{secrets.randbelow(10 ** 6):06d}"
    with db.conn() as c:
        c.execute("insert into users (id, role, pin_hash, name, crew_id, created_at, created_by) "
                  "values (?,?,?,?,?,?,?)", (uid, role, _pin_hash(pin), name, crew_id if role == "crew" else None,
                                             _iso(_now()), created_by))
    return {"user_id": uid, "role": role, "name": name, "crew_id": crew_id, "pin": pin}


def set_fixed(user_id: str, role: str, name: str, pin: str, created_by: str, crew_id: str | None = None) -> dict:
    """An account with an ID and PIN chosen beforehand, for a rehearsed demo. Made again with the
    same values it only refreshes them. Never used by the portals, only by the command line."""
    uid = normalise_id(user_id)
    if role not in ("staff", "crew", "tech") or not uid.startswith(PREFIX[role] + "-"):
        raise AuthError(f"{user_id}: a {role} ID starts with {PREFIX.get(role, '?')}-.")
    if not (pin.isdigit() and len(pin) == 6):
        raise AuthError(f"{uid}: the PIN must be 6 digits.")
    if role == "crew" and crew_id not in data.crews():
        raise AuthError(f"{uid}: no crew {crew_id}.")
    with db.conn() as c:
        c.execute("insert into users (id, role, pin_hash, name, crew_id, created_at, created_by) values (?,?,?,?,?,?,?) "
                  "on conflict(id) do update set role = excluded.role, pin_hash = excluded.pin_hash, name = excluded.name, "
                  "crew_id = excluded.crew_id, failed = 0, locked_until = null",
                  (uid, role, _pin_hash(pin), name, crew_id if role == "crew" else None, _iso(_now()), created_by))
    return {"user_id": uid, "role": role, "name": name, "crew_id": crew_id, "pin": pin}


def reset_pin(user_id: str, by: str) -> dict:
    uid = normalise_id(user_id)
    pin = f"{secrets.randbelow(10 ** 6):06d}"
    with db.conn() as c:
        n = c.execute("update users set pin_hash = ?, failed = 0, locked_until = null where id = ? and role != 'resident'",
                      (_pin_hash(pin), uid)).rowcount
        c.execute("delete from sessions_auth where user_id = ?", (uid,))
    if not n:
        raise AuthError("No staff, crew or tech account with that ID.")
    return {"user_id": uid, "pin": pin}


def set_active(user_id: str, active: bool) -> None:
    with db.conn() as c:
        c.execute("update users set active = ? where id = ? and role != 'resident'", (1 if active else 0, normalise_id(user_id)))
        if not active:
            c.execute("delete from sessions_auth where user_id = ?", (normalise_id(user_id),))


def list_users() -> dict:
    with db.conn() as c:
        rows = c.execute("select id, role, name, crew_id, created_at, created_by, last_seen, active "
                         "from users where role != 'resident' order by role, created_at").fetchall()
        residents = c.execute("select count(*) n from users where role = 'resident'").fetchone()["n"]
    return {"accounts": [dict(r) for r in rows], "residents": residents}


# ------------------------------------------------------------------ a resident's record
def history(uid: str | None, exclude_request: str | None = None) -> list[dict]:
    """Every crew verdict on this reporter's reports, oldest first. For a report imported from the
    NYC 311 backlog, the reporter is a synthetic person whose real 311 outcomes are used."""
    if not uid:
        return []
    out = []
    if uid.startswith("CIT-"):
        out += data.reporter_history(uid, exclude_request)
    for h in db.citizen_outcomes(uid):
        out.append({"outcome": h["outcome"], "closed_at": datetime.fromisoformat(h["closed_at"]).replace(tzinfo=None),
                    "request_type": h["request_type"]})
    return out


def resident_profile(uid: str) -> dict:
    hist = history(uid)
    with db.conn() as c:
        u = c.execute("select created_at from users where id = ?", (uid,)).fetchone()
    return {"user_id": uid, "member_since": u["created_at"][:10] if u else None,
            "checked_by_crews": len(hist), "confirmed_by_crews": sum(h["outcome"] == "GENUINE" for h in hist)}
