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
