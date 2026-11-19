import asyncio
import copy

import httpx
import pytest

from libsg.db import Store
from libsg.models import Edition
from libsg.nlb_client import NLBError
from libsg.poller import poll_once

A, B, C = 300111959, 300148287, 300172891  # three print P&P editions, ascending


def ed(brn, physical=True):
    return Edition(brn, "Pride and prejudice", "Austen, Jane", "1", "Book", 2025, physical=physical)


def returned(items, branch, brn):
    """Fixture items relabelled as `brn`, with every copy at `branch` on shelf."""
    items = copy.deepcopy(items)
    for it in items:
        it["brn"] = brn
        if it["location"]["code"] == branch:
            it["status"]["code"] = "In"
    return items


def relabel(items, brn):
    items = copy.deepcopy(items)
    for it in items:
        it["brn"] = brn
    return items


class FakeNLB:
    """Serves canned availability per BRN; records calls; can raise on demand."""

    def __init__(self, responses):
        self.responses = responses  # brn -> items | Exception
        self.calls = []

    async def __call__(self, brn):
        self.calls.append(brn)
        r = self.responses[brn]
        if isinstance(r, Exception):
            raise r
        return r


class FakeSleep:
    def __init__(self):
        self.waits = []

    async def __call__(self, s):
        self.waits.append(s)


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


def run(coro):
    return asyncio.run(coro)


def watch(store, user, brns, branches=(), physical=True):
    wid = store.create_work(user, "Pride and prejudice", "Austen, Jane", [ed(b, physical) for b in brns])
    store.set_watch(user, wid, branches)
    return wid


def test_baseline_then_alert_grouped_per_user_and_work(store, emma_avail):
    base = emma_avail["items"]
    w1 = watch(store, 1, [A, B], ["CLL"])
    w2 = watch(store, 2, [A])  # any library
    nlb = FakeNLB({A: relabel(base, A), B: relabel(base, B)})
    sleep = FakeSleep()

    r = run(poll_once(store, nlb, sleep, spacing=10))
    assert (r.checked, r.alerts) == (2, {})
    assert sleep.waits == [10]  # spaced between calls, not before the first

    nlb.responses = {A: returned(base, "CLL", A), B: returned(base, "CLL", B)}
    r = run(poll_once(store, nlb, sleep, spacing=10))
    assert set(r.alerts) == {(1, w1), (2, w2)}
    assert sorted(a.brn for a in r.alerts[(1, w1)]) == [A, B]  # one message, both editions
    assert [a.branch_code for a in r.alerts[(2, w2)]] == ["CLL"]


def test_digital_editions_not_polled(store, emma_avail):
    watch(store, 1, [A])
    watch(store, 1, [999], physical=False)
    nlb = FakeNLB({A: relabel(emma_avail["items"], A)})
    run(poll_once(store, nlb, FakeSleep()))
    assert nlb.calls == [A]


def test_rate_limit_stops_round_and_next_round_starts_with_skipped(store, emma_avail):
    base = emma_avail["items"]
    watch(store, 1, [A, B, C])
    nlb = FakeNLB({A: relabel(base, A), B: NLBError(429, "Too Many Requests", "API calls quota exceeded"),
                   C: relabel(base, C)})
    r = run(poll_once(store, nlb, FakeSleep()))
    assert r.rate_limited and (r.checked, r.skipped) == (1, 2)
    assert nlb.calls == [A, B]  # C never called
    assert store.get_snapshot(B) is None and store.get_snapshot(C) is None

    # Next round: never-checked B and C go before A.
    nlb.responses[B] = relabel(base, B)
    nlb.calls.clear()
    r = run(poll_once(store, nlb, FakeSleep()))
    assert nlb.calls == [B, C, A]
    assert not r.rate_limited and r.checked == 3


def test_other_errors_skip_only_that_edition(store, emma_avail):
    base = emma_avail["items"]
    watch(store, 1, [A, B, C])
    nlb = FakeNLB({A: NLBError(500, "Internal Server Error", "x"), B: httpx.ConnectTimeout("t"),
                   C: relabel(base, C)})
    r = run(poll_once(store, nlb, FakeSleep()))
    assert (r.checked, r.failed, r.rate_limited) == (1, 2, False)
    assert store.get_snapshot(A) is None and store.get_snapshot(C) is not None


def test_copy_returned_while_skipped_still_alerts_later(store, emma_avail):
    base = emma_avail["items"]
    wid = watch(store, 1, [A], ["CLL"])
    nlb = FakeNLB({A: relabel(base, A)})
    run(poll_once(store, nlb, FakeSleep()))  # baseline: Central all on loan

    nlb.responses[A] = NLBError(429, "Too Many Requests", "quota")
    assert run(poll_once(store, nlb, FakeSleep())).alerts == {}

    nlb.responses[A] = returned(base, "CLL", A)
    r = run(poll_once(store, nlb, FakeSleep()))
    assert [a.branch_code for a in r.alerts[(1, wid)]] == ["CLL"]


def test_shared_brn_checked_once(store, emma_avail):
    watch(store, 1, [A])
    watch(store, 2, [A])
    nlb = FakeNLB({A: relabel(emma_avail["items"], A)})
    run(poll_once(store, nlb, FakeSleep()))
    assert nlb.calls == [A]
