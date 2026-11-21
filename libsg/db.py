"""SQLite storage for works, watches and availability snapshots."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from libsg.branches import Branch
from libsg.models import Edition

SCHEMA = """
CREATE TABLE IF NOT EXISTS works (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    title       TEXT NOT NULL,
    author      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS works_user ON works(user_id);

CREATE TABLE IF NOT EXISTS work_editions (
    work_id     INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    brn         INTEGER NOT NULL,
    title       TEXT NOT NULL,
    format_name TEXT NOT NULL DEFAULT '',
    year        INTEGER,
    physical    INTEGER NOT NULL DEFAULT 1,
    label       TEXT NOT NULL DEFAULT '',  -- e.g. "2025 · English · Penguin Books · 357 pages"
    watched     INTEGER NOT NULL DEFAULT 1,  -- 0 = candidate the user unticked (kept so Edit can re-tick it)
    data        TEXT NOT NULL DEFAULT '',  -- the Edition as JSON, to rebuild it for editing
    PRIMARY KEY (work_id, brn)
);
CREATE INDEX IF NOT EXISTS work_editions_brn ON work_editions(brn);

CREATE TABLE IF NOT EXISTS watches (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    work_id     INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
    notify      INTEGER NOT NULL DEFAULT 1,  -- 0 = no alerts; the book just stays on the user's list
    UNIQUE (user_id, work_id)
);

-- No rows for a watch = any branch.
CREATE TABLE IF NOT EXISTS watch_branches (
    watch_id    INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
    branch_code TEXT NOT NULL,
    PRIMARY KEY (watch_id, branch_code)
);

-- One row per BRN we have checked at least once.
CREATE TABLE IF NOT EXISTS checked_editions (
    brn         INTEGER PRIMARY KEY,
    checked_at  TEXT NOT NULL
);

-- Last-seen borrowable count per (BRN, branch). A missing row for a checked
-- BRN means 0 at that branch.
CREATE TABLE IF NOT EXISTS snapshots (
    brn         INTEGER NOT NULL,
    branch_code TEXT NOT NULL,
    available   INTEGER NOT NULL,
    PRIMARY KEY (brn, branch_code)
);

-- Lending libraries from NLB's GetBranches, refreshed daily.
CREATE TABLE IF NOT EXISTS branches (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    short_name  TEXT NOT NULL,
    is_open     INTEGER NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL
);
"""

# Columns added after a table first shipped: (table, column, definition).
MIGRATIONS = [
    ("work_editions", "label", "TEXT NOT NULL DEFAULT ''"),
    ("work_editions", "watched", "INTEGER NOT NULL DEFAULT 1"),
    ("work_editions", "data", "TEXT NOT NULL DEFAULT ''"),
    ("watches", "notify", "INTEGER NOT NULL DEFAULT 1"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class WatchInfo:
    watch_id: int
    user_id: int
    work_id: int
    work_title: str
    branches: frozenset[str]  # empty = any branch
    notify: bool = True  # False: no alerts, the book is only listed in /watches

    def covers(self, branch_code: str) -> bool:
        return not self.branches or branch_code in self.branches


@dataclass(frozen=True)
class Work:
    id: int
    user_id: int
    title: str
    author: str
    brns: tuple[int, ...]


class Store:
    def __init__(self, path: str | Path = "libsg.db"):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        for table, column, definition in MIGRATIONS:
            cols = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # --- works -------------------------------------------------------------

    def create_work(
        self, user_id: int, title: str, author: str, editions: Iterable[Edition],
        watched: Iterable[int] | None = None,
    ) -> int:
        """Save a work with all its candidate editions; `watched` = BRNs the user ticked (default: all)."""
        editions = list(editions)
        watched = {e.brn for e in editions} if watched is None else set(watched)
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO works (user_id, title, author, created_at) VALUES (?, ?, ?, ?)",
                (user_id, title, author, _now()),
            )
            work_id = cur.lastrowid
            self.conn.executemany(
                "INSERT OR IGNORE INTO work_editions"
                " (work_id, brn, title, format_name, year, physical, label, watched, data)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (work_id, e.brn, e.title, e.format_name, e.year, int(e.physical), e.describe(),
                     int(e.brn in watched), json.dumps(asdict(e), ensure_ascii=False))
                    for e in editions
                ],
            )
        assert work_id is not None
        return work_id

    def get_work(self, work_id: int) -> Work | None:
        row = self.conn.execute("SELECT * FROM works WHERE id = ?", (work_id,)).fetchone()
        if not row:
            return None
        brns = tuple(
            r["brn"] for r in self.conn.execute(
                "SELECT brn FROM work_editions WHERE work_id = ? AND watched = 1 ORDER BY brn", (work_id,)
            )
        )
        return Work(row["id"], row["user_id"], row["title"], row["author"], brns)

    def list_works(self, user_id: int) -> list[Work]:
        ids = [r["id"] for r in self.conn.execute("SELECT id FROM works WHERE user_id = ? ORDER BY id", (user_id,))]
        return [w for w in (self.get_work(i) for i in ids) if w]

    def work_editions(self, work_id: int, watched_only: bool = True) -> list[sqlite3.Row]:
        """Rows with brn, title, format_name, year, physical, label, watched -- for building messages."""
        sql = ("SELECT brn, title, format_name, year, physical, label, watched, data"
               " FROM work_editions WHERE work_id = ?")
        if watched_only:
            sql += " AND watched = 1"
        return self.conn.execute(sql + " ORDER BY rowid", (work_id,)).fetchall()

    def edition_choices(self, work_id: int) -> tuple[list[Edition], set[int]]:
        """All candidate editions of a work (in their original order) and the BRNs currently watched."""
        editions, watched = [], set()
        for r in self.work_editions(work_id, watched_only=False):
            if r["data"]:
                d = json.loads(r["data"])
                d["isbns"] = tuple(d.get("isbns") or ())
                e = Edition(**d)
            else:  # saved before full edition data was stored
                e = Edition(r["brn"], r["title"], "", "", r["format_name"], r["year"], physical=bool(r["physical"]))
            editions.append(e)
            if r["watched"]:
                watched.add(e.brn)
        return editions, watched

    def works_sharing_editions(self, user_id: int, brns: Iterable[int]) -> list[int]:
        """IDs of this user's works that contain any of these BRNs (i.e. the same book saved before)."""
        brns = list(brns)
        if not brns:
            return []
        marks = ",".join("?" * len(brns))
        return [
            r["id"] for r in self.conn.execute(
                f"SELECT DISTINCT k.id FROM works k JOIN work_editions e ON e.work_id = k.id"
                f" WHERE k.user_id = ? AND e.brn IN ({marks}) ORDER BY k.id",
                (user_id, *brns),
            )
        ]

    def delete_work(self, work_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM works WHERE id = ?", (work_id,))

    # --- watches -----------------------------------------------------------

    def set_watch(self, user_id: int, work_id: int, branches: Iterable[str] = (), notify: bool = True) -> int:
        """Create or update the user's watch on a work; replaces its branch set and alert setting."""
        branches = sorted({b.upper() for b in branches})
        with self.conn:
            self.conn.execute(
                "INSERT INTO watches (user_id, work_id, created_at, notify) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (user_id, work_id) DO UPDATE SET notify = excluded.notify",
                (user_id, work_id, _now(), int(notify)),
            )
            watch_id = self.conn.execute(
                "SELECT id FROM watches WHERE user_id = ? AND work_id = ?", (user_id, work_id)
            ).fetchone()["id"]
            self.conn.execute("DELETE FROM watch_branches WHERE watch_id = ?", (watch_id,))
            self.conn.executemany(
                "INSERT INTO watch_branches (watch_id, branch_code) VALUES (?, ?)",
                [(watch_id, b) for b in branches],
            )
        return watch_id

    def set_notify(self, watch_id: int, notify: bool) -> None:
        """Turn alerts on/off. The bot refreshes stale snapshots when turning alerts on, so copies
        already on the shelf don't trigger an alert -- only the next 0 -> 1 does."""
        with self.conn:
            self.conn.execute("UPDATE watches SET notify = ? WHERE id = ?", (int(notify), watch_id))

    def remove_watch(self, watch_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM watches WHERE id = ?", (watch_id,))

    def _watch_infos(self, where: str, params: tuple) -> list[WatchInfo]:
        rows = self.conn.execute(
            f"SELECT w.id, w.user_id, w.work_id, k.title, w.notify FROM watches w JOIN works k ON k.id = w.work_id"
            f" WHERE {where} ORDER BY w.id",
            params,
        ).fetchall()
        out = []
        for r in rows:
            branches = frozenset(
                b["branch_code"] for b in self.conn.execute(
                    "SELECT branch_code FROM watch_branches WHERE watch_id = ?", (r["id"],)
                )
            )
            out.append(WatchInfo(r["id"], r["user_id"], r["work_id"], r["title"], branches, bool(r["notify"])))
        return out

    def list_watches(self, user_id: int) -> list[WatchInfo]:
        return self._watch_infos("w.user_id = ?", (user_id,))

    def watches_for_brn(self, brn: int) -> list[WatchInfo]:
        return self._watch_infos(
            "w.work_id IN (SELECT work_id FROM work_editions WHERE brn = ? AND watched = 1)", (brn,)
        )

    def get_watch(self, watch_id: int) -> WatchInfo | None:
        found = self._watch_infos("w.id = ?", (watch_id,))
        return found[0] if found else None

    def watched_brns(self) -> list[int]:
        """Physical BRNs in at least one watched work with alerts on -- what the poller must check.

        Books with alerts off aren't polled; they only update via Check now (or when someone
        else's alerting watch shares the edition).

        Never-checked first, then longest-since-checked, so a round cut short by
        rate limiting doesn't starve the same editions every time.
        """
        return [
            r["brn"] for r in self.conn.execute(
                "SELECT e.brn FROM work_editions e JOIN watches w ON w.work_id = e.work_id"
                " LEFT JOIN checked_editions c ON c.brn = e.brn"
                " WHERE e.physical = 1 AND e.watched = 1 AND w.notify = 1"
                " GROUP BY e.brn"
                " ORDER BY MAX(c.checked_at) IS NOT NULL, MAX(c.checked_at), e.brn"
            )
        ]

    # --- snapshots ---------------------------------------------------------

    def get_snapshot(self, brn: int) -> dict[str, int] | None:
        """Last-seen branch -> available count, or None if this BRN was never checked."""
        if not self.conn.execute("SELECT 1 FROM checked_editions WHERE brn = ?", (brn,)).fetchone():
            return None
        return {
            r["branch_code"]: r["available"]
            for r in self.conn.execute("SELECT branch_code, available FROM snapshots WHERE brn = ?", (brn,))
        }

    def checked_times(self, brns: Iterable[int]) -> dict[int, str]:
        """brn -> last check time (ISO, UTC) for the BRNs that have been checked."""
        brns = list(brns)
        if not brns:
            return {}
        marks = ",".join("?" * len(brns))
        return {
            r["brn"]: r["checked_at"]
            for r in self.conn.execute(f"SELECT brn, checked_at FROM checked_editions WHERE brn IN ({marks})", brns)
        }

    def last_checked(self, brns: Iterable[int]) -> str | None:
        """Oldest check time across these BRNs (ISO, UTC), None if any was never checked."""
        brns = list(brns)
        if not brns:
            return None
        marks = ",".join("?" * len(brns))
        rows = self.conn.execute(
            f"SELECT checked_at FROM checked_editions WHERE brn IN ({marks})", brns
        ).fetchall()
        if len(rows) < len(set(brns)):
            return None
        return min(r["checked_at"] for r in rows)

    def save_snapshot(self, brn: int, counts: dict[str, int]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM snapshots WHERE brn = ?", (brn,))
            self.conn.executemany(
                "INSERT INTO snapshots (brn, branch_code, available) VALUES (?, ?, ?)",
                [(brn, b, n) for b, n in counts.items()],
            )
            self.conn.execute(
                "INSERT INTO checked_editions (brn, checked_at) VALUES (?, ?)"
                " ON CONFLICT (brn) DO UPDATE SET checked_at = excluded.checked_at",
                (brn, _now()),
            )

    # --- branches ----------------------------------------------------------

    def save_branches(self, branches: Iterable[Branch]) -> None:
        """Replace the stored branch list with a fresh one from GetBranches."""
        rows = [(b.code, b.name, b.short_name, int(b.is_open), b.note, _now()) for b in branches]
        if not rows:
            return  # never wipe a good list with an empty response
        with self.conn:
            self.conn.execute("DELETE FROM branches")
            self.conn.executemany(
                "INSERT INTO branches (code, name, short_name, is_open, note, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )

    def branches(self, open_only: bool = False) -> list[Branch]:
        sql = "SELECT code, name, short_name, is_open, note FROM branches"
        if open_only:
            sql += " WHERE is_open = 1"
        return [
            Branch(r["code"], r["name"], r["short_name"], bool(r["is_open"]), r["note"])
            for r in self.conn.execute(sql + " ORDER BY short_name COLLATE NOCASE")
        ]

    def branches_updated_at(self) -> str | None:
        row = self.conn.execute("SELECT MAX(updated_at) AS t FROM branches").fetchone()
        return row["t"] if row else None
