"""People you know: your LinkedIn connections, matched to the companies you watch.

LinkedIn lets you download your own data (Settings & Privacy, Data privacy, Get a copy of your
data, Connections). Import that Connections.csv here; it stays in postings.db on your computer.
"""
from __future__ import annotations

import io

from .base import *  # noqa: F401,F403
from .tracked import connect


def _table(db):
    db.execute("""CREATE TABLE IF NOT EXISTS connections (
        id INTEGER PRIMARY KEY AUTOINCREMENT, first TEXT, last TEXT, url TEXT, company TEXT,
        company_key TEXT, position TEXT, connected TEXT)""")
    db.execute("CREATE INDEX IF NOT EXISTS connections_company ON connections(company_key)")


def company_key(name):
    """Company names compared loosely: 'Shopify Inc.' and 'Shopify' match."""
    n = re.sub(r"\b(inc|incorporated|llc|ltd|limited|corp|corporation|co|company|gmbh|plc|technologies|"
               r"technology|labs|canada|usa|group|holdings)\b\.?", " ", (name or "").lower())
    return norm_name(n)


def import_csv(cfg, data):
    """Replace your saved connections with this export. Returns how many were saved."""
    text = data.decode("utf-8-sig", errors="replace") if isinstance(data, bytes) else data
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.lower().startswith("first name")), None)
    if start is None:
        raise ValueError("That isn't LinkedIn's Connections.csv (no First Name column).")
    rows = list(csv.DictReader(io.StringIO("\n".join(lines[start:]))))
    db = connect(cfg)
    try:
        with db:
            _table(db)
            db.execute("DELETE FROM connections")
            db.executemany(
                "INSERT INTO connections (first, last, url, company, company_key, position, connected) VALUES (?,?,?,?,?,?,?)",
                [(r.get("First Name", ""), r.get("Last Name", ""), r.get("URL", ""), r.get("Company", ""),
                  company_key(r.get("Company", "")), r.get("Position", ""), r.get("Connected On", ""))
                 for r in rows if (r.get("Company") or "").strip()])
            return db.execute("SELECT COUNT(*) FROM connections").fetchone()[0]
    finally:
        db.close()


def counts(cfg, companies):
    db = connect(cfg)
    try:
        _table(db)
        have = dict(db.execute("SELECT company_key, COUNT(*) FROM connections GROUP BY company_key").fetchall())
    finally:
        db.close()
    return {c: have.get(company_key(c), 0) for c in companies if have.get(company_key(c))}


def people_at(cfg, company):
    db = connect(cfg)
    try:
        _table(db)
        return [dict(r) for r in db.execute(
            "SELECT first, last, url, position, connected FROM connections WHERE company_key=? ORDER BY last, first",
            (company_key(company),))]
    finally:
        db.close()


def total(cfg):
    db = connect(cfg)
    try:
        _table(db)
        return db.execute("SELECT COUNT(*) FROM connections").fetchone()[0]
    finally:
        db.close()


def clear(cfg):
    db = connect(cfg)
    try:
        with db:
            _table(db)
            db.execute("DELETE FROM connections")
    finally:
        db.close()


__all__ = ["import_csv", "counts", "people_at", "total", "clear", "company_key"]
