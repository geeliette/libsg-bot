from types import SimpleNamespace as NS

import pytest

from libsg.bot import _baseline_age, _stale_brns
from libsg.db import Store


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


def test_stale_brns(store):
    store.save_snapshot(1, {"CLL": 1})  # checked just now
    store.conn.execute("INSERT INTO checked_editions (brn, checked_at) VALUES (2, '2026-01-01T00:00:00+00:00')")
    # 3 never checked
    assert _stale_brns(store, [1, 2, 3], max_age=1800) == [2, 3]
    assert _stale_brns(store, [1, 2, 3], max_age=float("inf")) == [3]  # only never-checked
    assert _stale_brns(store, [1, 2, 3], max_age=0) == [1, 2, 3]
    store.conn.execute("UPDATE checked_editions SET checked_at = datetime('now') WHERE brn = 1")  # no timezone
    assert _stale_brns(store, [1], max_age=1800) == []  # treated as UTC, no crash


def test_baseline_age_is_one_poll_interval():
    assert _baseline_age(NS(bot_data={"poll_minutes": 30})) == 1800
