import pytest

from libsg.db import Store
from libsg.grouping import suggest_works
from libsg.models import parse_search

USER, OTHER = 111, 222


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def emma_work(emma_search):
    return suggest_works(parse_search(emma_search))[0]


def test_work_round_trip(store, emma_work):
    wid = store.create_work(USER, emma_work.title, emma_work.author, emma_work.editions)
    w = store.get_work(wid)
    assert w.title == "Emma" and w.user_id == USER
    assert set(w.brns) == set(emma_work.brns)
    assert [x.id for x in store.list_works(USER)] == [wid]
    assert store.list_works(OTHER) == []


def test_set_watch_replaces_branches(store, emma_work):
    wid = store.create_work(USER, emma_work.title, emma_work.author, emma_work.editions)
    w1 = store.set_watch(USER, wid, ["tppl", "PRPL"])
    w2 = store.set_watch(USER, wid, ["CLL"])
    assert w1 == w2
    (info,) = store.list_watches(USER)
    assert info.branches == {"CLL"}
    assert info.covers("CLL") and not info.covers("TPPL")


def test_empty_branch_set_means_any(store, emma_work):
    wid = store.create_work(USER, emma_work.title, emma_work.author, emma_work.editions)
    store.set_watch(USER, wid)
    (info,) = store.list_watches(USER)
    assert info.branches == frozenset() and info.covers("ANYPL")


def test_watched_brns_are_physical_and_watched_only(store, emma_work):
    wid = store.create_work(USER, emma_work.title, emma_work.author, emma_work.editions)
    assert store.watched_brns() == []
    store.set_watch(USER, wid)
    assert store.watched_brns() == [4171279, 300123287, 300172872]
    assert [w.work_id for w in store.watches_for_brn(4171279)] == [wid]
    assert store.watches_for_brn(999) == []


def test_delete_work_cascades(store, emma_work):
    wid = store.create_work(USER, emma_work.title, emma_work.author, emma_work.editions)
    store.set_watch(USER, wid, ["CLL"])
    store.delete_work(wid)
    assert store.list_watches(USER) == []
    assert store.watched_brns() == []
    assert store.conn.execute("SELECT COUNT(*) FROM watch_branches").fetchone()[0] == 0


def test_snapshot_none_vs_empty(store):
    assert store.get_snapshot(1) is None
    store.save_snapshot(1, {})
    assert store.get_snapshot(1) == {}
    store.save_snapshot(1, {"CLL": 2, "TPPL": 0})
    assert store.get_snapshot(1) == {"CLL": 2, "TPPL": 0}
    store.save_snapshot(1, {"CLL": 1})
    assert store.get_snapshot(1) == {"CLL": 1}


def test_persists_to_file(tmp_path, emma_work):
    path = tmp_path / "t.db"
    with Store(path) as s:
        wid = s.create_work(USER, emma_work.title, emma_work.author, emma_work.editions)
        s.set_watch(USER, wid, ["CLL"])
    with Store(path) as s:
        assert s.list_watches(USER)[0].branches == {"CLL"}


def test_branches_round_trip(store, branches_data):
    from libsg.branches import parse_branches

    store.save_branches(parse_branches(branches_data))
    assert len(store.branches(open_only=True)) == 22
    assert "AMKPL" in {b.code for b in store.branches()} - {b.code for b in store.branches(open_only=True)}
    assert store.branches_updated_at()
    store.save_branches([])  # an empty fetch never wipes the list
    assert len(store.branches(open_only=True)) == 22


def test_edition_label_saved(store, emma_work):
    wid = store.create_work(USER, emma_work.title, emma_work.author, emma_work.editions)
    labels = {r["brn"]: r["label"] for r in store.work_editions(wid)}
    assert labels[4171279] == "1981 · Indonesian · Gramedia · 185 pages"


def test_migrates_old_database(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE works (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT NOT NULL,"
        " author TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);"
        "CREATE TABLE work_editions (work_id INTEGER NOT NULL, brn INTEGER NOT NULL, title TEXT NOT NULL,"
        " format_name TEXT NOT NULL DEFAULT '', year INTEGER, physical INTEGER NOT NULL DEFAULT 1,"
        " PRIMARY KEY (work_id, brn));"
        "INSERT INTO works VALUES (1, 5, 'Emma', '', 'x'); INSERT INTO work_editions VALUES (1, 42, 'Emma', 'Book', 2020, 1);"
    )
    old.commit()
    old.close()
    with Store(path) as s:
        (row,) = s.work_editions(1)
        assert row["label"] == "" and row["brn"] == 42


def test_unticked_editions_kept_but_not_watched(store, emma_work):
    keep = {300172872}
    wid = store.create_work(USER, emma_work.title, emma_work.author, emma_work.editions, watched=keep)
    store.set_watch(USER, wid)
    assert store.get_work(wid).brns == (300172872,)
    assert store.watched_brns() == [300172872]
    assert store.watches_for_brn(4171279) == []  # unticked: no alerts
    editions, watched = store.edition_choices(wid)
    assert [e.brn for e in editions] == [e.brn for e in emma_work.editions]  # original order, all candidates
    assert watched == keep
    rebuilt = {e.brn: e for e in editions}
    original = {e.brn: e for e in emma_work.editions}
    assert rebuilt[4171279] == original[4171279]  # full Edition round-trips
    assert store.works_sharing_editions(USER, [4171279]) == [wid]  # still "the same book"


def test_last_checked(store):
    assert store.last_checked([]) is None
    store.save_snapshot(1, {"CLL": 1})
    assert store.last_checked([1]) is not None
    assert store.last_checked([1, 2]) is None  # 2 never checked
    store.save_snapshot(2, {})
    first = store.conn.execute("SELECT checked_at FROM checked_editions WHERE brn = 1").fetchone()[0]
    assert store.last_checked([1, 2]) == first  # the oldest


def test_checked_times(store):
    assert store.checked_times([]) == {}
    store.save_snapshot(1, {})
    times = store.checked_times([1, 2])
    assert set(times) == {1}
