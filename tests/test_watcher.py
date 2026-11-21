import copy as copymod

import pytest

from libsg.db import Store, WatchInfo
from libsg.models import parse_copies
from libsg.watcher import compute_alerts, newly_available, process_edition

BRN = 300172891


def watch(user=1, branches=(), wid=10, work=100, notify=True):
    return WatchInfo(wid, user, work, "Pride and prejudice", frozenset(branches), notify)


def test_first_check_is_baseline():
    assert newly_available(None, {"CLL": 3}) == []
    assert compute_alerts(BRN, None, {"CLL": 3}, [watch()]) == []


def test_zero_to_one_alerts():
    assert newly_available({"CLL": 0}, {"CLL": 1}) == ["CLL"]


def test_absent_to_one_alerts():
    # Branch had no copies at all last time (e.g. a copy was transferred in).
    assert newly_available({"TPPL": 0}, {"TPPL": 0, "CLL": 1}) == ["CLL"]


def test_one_to_two_does_not_alert():
    assert newly_available({"CLL": 1}, {"CLL": 2}) == []


def test_going_unavailable_does_not_alert():
    assert newly_available({"CLL": 1}, {"CLL": 0}) == []
    assert newly_available({"CLL": 1}, {}) == []


def test_only_watched_branches():
    alerts = compute_alerts(BRN, {"CLL": 0, "TPPL": 0}, {"CLL": 1, "TPPL": 1}, [watch(branches={"TPPL"})])
    assert [a.branch_code for a in alerts] == ["TPPL"]


def test_any_branch_watch():
    alerts = compute_alerts(BRN, {"CLL": 0, "TPPL": 0}, {"CLL": 1, "TPPL": 2}, [watch()])
    assert sorted((a.branch_code, a.available) for a in alerts) == [("CLL", 1), ("TPPL", 2)]


def test_multiple_users_and_dedupe():
    ws = [
        watch(user=1, wid=10, work=100, branches={"CLL"}),
        watch(user=1, wid=11, work=101),  # same user, another work with this BRN
        watch(user=2, wid=12, work=102, branches={"CLL"}),
        watch(user=3, wid=13, work=103, branches={"TPPL"}),
    ]
    alerts = compute_alerts(BRN, {"CLL": 0}, {"CLL": 1}, ws, {"CLL": "Central Library"})
    assert sorted(a.user_id for a in alerts) == [1, 2]
    assert all(a.branch_name == "Central Library" for a in alerts)


def test_alerts_off_means_no_alert():
    assert compute_alerts(BRN, {"CLL": 0}, {"CLL": 1}, [watch(notify=False)]) == []


def test_silent_watch_does_not_swallow_a_loud_one():
    # Same user watches the BRN twice: alerts off on one book, on for another. Still one alert.
    ws = [watch(user=1, wid=10, work=100, notify=False), watch(user=1, wid=11, work=101)]
    alerts = compute_alerts(BRN, {"CLL": 0}, {"CLL": 1}, ws)
    assert [(a.user_id, a.watch_id) for a in alerts] == [(1, 11)]


# --- end-to-end with the store and the real fixture --------------------------


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


def _set_status(items, branch_code, code):
    """Return a copy of the raw items with every copy at a branch set to a status code."""
    items = copymod.deepcopy(items)
    for it in items:
        if it["location"]["code"] == branch_code:
            it["status"]["code"] = code
    return items


def test_process_edition_end_to_end(store, emma_avail):
    items = emma_avail["items"]
    wid = store.create_work(1, "Pride and prejudice", "Austen", [])
    store.conn.execute(
        "INSERT INTO work_editions (work_id, brn, title) VALUES (?, ?, ?)", (wid, BRN, "Pride and prejudice")
    )
    store.set_watch(1, wid, ["CLL", "BEPL"])

    # Poll 1: baseline from the real fixture (Central + Bedok all on loan).
    assert process_edition(store, BRN, parse_copies(items)) == []
    assert store.get_snapshot(BRN)["CLL"] == 0

    # Poll 2: nothing changed.
    assert process_edition(store, BRN, parse_copies(items)) == []

    # Poll 3: Central copies returned; Pasir Ris (unwatched) unchanged.
    returned = _set_status(items, "CLL", "In")
    alerts = process_edition(store, BRN, parse_copies(returned))
    assert [(a.branch_code, a.branch_name, a.available) for a in alerts] == [("CLL", "Central Library", 2)]

    # Poll 4: still on shelf -> no repeat alert.
    assert process_edition(store, BRN, parse_copies(returned)) == []

    # Poll 5: borrowed again, Poll 6: returned again -> alerts again.
    assert process_edition(store, BRN, parse_copies(items)) == []
    assert len(process_edition(store, BRN, parse_copies(returned))) == 1


def test_turning_alerts_on_sends_no_backlog(store, emma_avail):
    items = emma_avail["items"]
    wid = store.create_work(1, "Pride and prejudice", "Austen", [])
    store.conn.execute("INSERT INTO work_editions (work_id, brn, title) VALUES (?, ?, ?)", (wid, BRN, "P&P"))
    watch_id = store.set_watch(1, wid, ["CLL"], notify=False)
    returned = _set_status(items, "CLL", "In")

    assert process_edition(store, BRN, parse_copies(items)) == []  # baseline: on loan
    assert process_edition(store, BRN, parse_copies(returned)) == []  # back on shelf, but alerts off
    assert store.get_snapshot(BRN)["CLL"] == 2  # still tracked while silent

    store.set_notify(watch_id, True)
    assert process_edition(store, BRN, parse_copies(returned)) == []  # already on shelf: no backlog alert
    assert process_edition(store, BRN, parse_copies(items)) == []
    assert len(process_edition(store, BRN, parse_copies(returned))) == 1  # next return alerts
