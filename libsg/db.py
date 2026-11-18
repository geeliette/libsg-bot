"""SQLite storage for works, watches and availability snapshots."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

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
    PRIMARY KEY (work_id, brn)
);
CREATE INDEX IF NOT EXISTS work_editions_brn ON work_editions(brn);

CREATE TABLE IF NOT EXISTS watches (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    work_id     INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
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
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class WatchInfo:
    watch_id: int
    user_id: int
    work_id: int
    work_title: str
    branches: frozenset[str]  # empty = any branch

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
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # --- works -------------------------------------------------------------

    def create_work(self, user_id: int, title: str, author: str, editions: Iterable[Edition]) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO works (user_id, title, author, created_at) VALUES (?, ?, ?, ?)",
                (user_id, title, author, _now()),
            )
            work_id = cur.lastrowid
            self.conn.executemany(
                "INSERT OR IGNORE INTO work_editions (work_id, brn, title, format_name, year, physical)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [(work_id, e.brn, e.title, e.format_name, e.year, int(e.physical)) for e in editions],
            )
        assert work_id is not None
        return work_id

    def get_work(self, work_id: int) -> Work | None:
        row = self.conn.execute("SELECT * FROM works WHERE id = ?", (work_id,)).fetchone()
        if not row:
            return None
        brns = tuple(
            r["brn"] for r in self.conn.execute(
                "SELECT brn FROM work_editions WHERE work_id = ? ORDER BY brn", (work_id,)
            )
        )
        return Work(row["id"], row["user_id"], row["title"], row["author"], brns)

    def list_works(self, user_id: int) -> list[Work]:
        ids = [r["id"] for r in self.conn.execute("SELECT id FROM works WHERE user_id = ? ORDER BY id", (user_id,))]
        return [w for w in (self.get_work(i) for i in ids) if w]

    def delete_work(self, work_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM works WHERE id = ?", (work_id,))

    # --- watches -----------------------------------------------------------

    def set_watch(self, user_id: int, work_id: int, branches: Iterable[str] = ()) -> int:
        """Create or update the user's watch on a work; replaces its branch set."""
        branches = sorted({b.upper() for b in branches})
        with self.conn:
            self.conn.execute(
                "INSERT INTO watches (user_id, work_id, created_at) VALUES (?, ?, ?)"
                " ON CONFLICT (user_id, work_id) DO NOTHING",
                (user_id, work_id, _now()),
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

    def remove_watch(self, watch_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM watches WHERE id = ?", (watch_id,))

    def _watch_infos(self, where: str, params: tuple) -> list[WatchInfo]:
        rows = self.conn.execute(
            f"SELECT w.id, w.user_id, w.work_id, k.title FROM watches w JOIN works k ON k.id = w.work_id"
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
            out.append(WatchInfo(r["id"], r["user_id"], r["work_id"], r["title"], branches))
        return out

    def list_watches(self, user_id: int) -> list[WatchInfo]:
        return self._watch_infos("w.user_id = ?", (user_id,))

    def watches_for_brn(self, brn: int) -> list[WatchInfo]:
        return self._watch_infos(
            "w.work_id IN (SELECT work_id FROM work_editions WHERE brn = ?)", (brn,)
        )

    def watched_brns(self) -> list[int]:
        """Physical BRNs that belong to at least one watched work -- what the poller must check."""
        return [
            r["brn"] for r in self.conn.execute(
                "SELECT DISTINCT e.brn FROM work_editions e JOIN watches w ON w.work_id = e.work_id"
                " WHERE e.physical = 1 ORDER BY e.brn"
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
