"""A single SQLite file. Git ignores it. All times are New York time with the offset written in."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB = Path(os.environ.get("CIVICOPS_DB", Path(__file__).resolve().parent / "civicops.db"))

SCHEMA = """
create table if not exists sessions (id text primary key, data text not null);
create table if not exists requests (
  id text primary key, created_at text not null, status text not null default 'OPEN',
  data text not null);
create table if not exists plugin_calls (
  request_id text, plugin text, outcome text, ms real, warm integer, note text, at text);
create table if not exists emergency_events (at text, category text, action text);
create table if not exists citizen_outcomes (
  citizen_key text not null, outcome text not null, closed_at text not null, request_type text);
create table if not exists meta (key text primary key, value text);
create table if not exists crew_jobs (
  request_id text primary key, crew_id text not null, assigned_at text not null,
  outcome text, closed_at text);
"""


@contextmanager
def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    DB.parent.mkdir(parents=True, exist_ok=True)     # a fresh server or test run may not have the folder yet
    with conn() as c:
        c.executescript(SCHEMA)


TABLES = {"sessions", "requests"}      # the only tables stored as JSON documents


def _table(name: str) -> str:
    """Table names cannot be query parameters, so only known names are allowed in a query."""
    if name not in TABLES:
        raise ValueError(f"unknown table {name!r}")
    return name


def save_json(table: str, key: str, data: dict, **cols) -> None:
    fields = ["id", "data", *cols]
    values = [key, json.dumps(data, default=str), *cols.values()]
    with conn() as c:
        c.execute(f"insert or replace into {_table(table)} ({', '.join(fields)}) "
                  f"values ({', '.join('?' * len(fields))})", values)


def load_json(table: str, key: str) -> dict | None:
    with conn() as c:
        row = c.execute(f"select data from {_table(table)} where id = ?", (key,)).fetchone()  # nosec B608: allow listed name
    return json.loads(row["data"]) if row else None


def citizen_outcomes(key: str) -> list[dict]:
    """Outcomes crews recorded in this app for a reporter, oldest first."""
    with conn() as c:
        rows = c.execute("select outcome, closed_at, request_type from citizen_outcomes "
                         "where citizen_key = ? order by closed_at", (key,)).fetchall()
    return [dict(r) for r in rows]
