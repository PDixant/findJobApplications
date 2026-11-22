"""The local store of every posting from every board (postings.db)."""
from __future__ import annotations

from .base import *  # noqa: F401,F403


# ----------------------------------------------------------------- local postings store
class PostingStore:
    """Every posting from every board, kept locally in SQLite. Filtering reads from here,
    so changing your rules or re-running the filters never touches the job boards."""

    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=30)  # the app may be saving an edit
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS postings (
                key TEXT PRIMARY KEY, board TEXT, company TEXT, provider TEXT, title TEXT,
                locations TEXT, url TEXT, posted TEXT, first_seen TEXT, last_seen TEXT,
                closed TEXT, open INTEGER, baseline INTEGER, text BLOB);
            CREATE INDEX IF NOT EXISTS postings_board ON postings(board);
            CREATE INDEX IF NOT EXISTS postings_open ON postings(open);""")

    def keys_with_text(self):
        return {r[0] for r in self.db.execute("SELECT key FROM postings WHERE text IS NOT NULL")}

    def boards(self):
        return {r[0] for r in self.db.execute("SELECT DISTINCT board FROM postings")}

    def save_board(self, board, company, provider, postings, now, complete, first_time):
        stamp = now.isoformat()
        existing = {r["key"]: r for r in self.db.execute(
            "SELECT key, locations, text IS NOT NULL AS has_text FROM postings WHERE board = ?", (board,))}
        new = 0
        for p in postings:
            locations = json.dumps(p.get("locations") or [])
            posted = p["posted"].isoformat() if p.get("posted") else None
            old = existing.get(p["key"])
            text = None
            if p.get("text") is not None and not (old and old["has_text"]):
                text = zlib.compress(p["text"][:50000].encode("utf-8"))
            if old:
                if not p.get("locations"):
                    locations = old["locations"]  # keep locations learned from a detail page
                self.db.execute(
                    "UPDATE postings SET company=?, provider=?, title=?, locations=?, url=?,"
                    " posted=COALESCE(posted, ?), last_seen=?, open=1, closed=NULL,"
                    " text=COALESCE(text, ?) WHERE key=?",
                    (company, provider, p["title"], locations, p["url"], posted, stamp, text, p["key"]))
            else:
                new += 1
                self.db.execute(
                    "INSERT OR REPLACE INTO postings (key, board, company, provider, title, locations, url,"
                    " posted, first_seen, last_seen, open, baseline, text) VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?)",
                    (p["key"], board, company, provider, p["title"], locations, p["url"], posted,
                     stamp, stamp, 1 if first_time else 0, text))
        if complete:  # a full list proves anything missing from it was taken down
            self.db.execute("UPDATE postings SET open=0, closed=? WHERE board=? AND open=1 AND last_seen<?",
                            (stamp, board, stamp))
        self.db.commit()
        return new

    def open_postings(self):
        return self.db.execute(
            "SELECT key, board, company, provider, title, locations, url, posted, first_seen, baseline,"
            " text IS NOT NULL AS has_text FROM postings WHERE open=1").fetchall()

    def text_of(self, key):
        row = self.db.execute("SELECT text FROM postings WHERE key=?", (key,)).fetchone()
        return zlib.decompress(row[0]).decode("utf-8") if row and row[0] is not None else None

    def status_of(self, keys):
        found = {}
        keys = list(keys)
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            q = f"SELECT key, open FROM postings WHERE key IN ({','.join('?' * len(chunk))})"
            found.update({r["key"]: bool(r["open"]) for r in self.db.execute(q, chunk)})
        return found

    def counts(self):
        r = self.db.execute("SELECT COUNT(*), SUM(open) FROM postings").fetchone()
        return r[0] or 0, r[1] or 0

    def export_csv(self, path, tracked):
        """The 'all postings' list: every open posting, newest first, readable in Excel."""
        rows = self.db.execute(
            "SELECT first_seen, posted, company, title, locations, provider, url, key FROM postings"
            " WHERE open=1 ORDER BY COALESCE(posted, first_seen) DESC").fetchall()
        tmp = path.with_name(path.stem + ".saving.csv")
        try:
            with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["First seen", "Posted", "Company", "Title", "Location", "Board",
                            "In your tracker", "Link"])
                for r in rows:
                    first, posted = to_local_naive(r["first_seen"]), to_local_naive(r["posted"])
                    w.writerow([f"{first:%Y-%m-%d %H:%M}" if first else "",
                                f"{posted:%Y-%m-%d}" if posted else "", r["company"], r["title"],
                                "; ".join(json.loads(r["locations"] or "[]")), r["provider"],
                                "yes" if r["key"] in tracked else "", r["url"]])
            replace_file(tmp, path)
        except PermissionError:
            log.warning("%s is open in another program; it will be updated next run.", path.name)
            tmp.unlink(missing_ok=True)

    def close(self):
        self.db.close()


__all__ = [n for n in list(globals()) if not n.startswith("__")]
