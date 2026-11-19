from libsg.ratelimit import FRESH_FOR, SpamGuard


def test_normal_use_is_never_blocked():
    g = SpamGuard(limit=6, window=60, cooldown=300)
    # a press every 15 s for an hour
    assert all(g.check(1, now=t) == 0 for t in range(0, 3600, 15))


def test_spamming_pauses_then_recovers():
    g = SpamGuard(limit=6, window=60, cooldown=300)
    assert [g.check(1, now=t) for t in range(6)] == [0] * 6
    assert g.check(1, now=6) == 300  # 7th press within a minute
    assert g.check(1, now=100) == 206  # still paused, presses don't extend it
    assert g.check(1, now=306) == 0  # back to normal
    assert g.check(1, now=307) == 0


def test_per_user():
    g = SpamGuard(limit=1, window=60, cooldown=300)
    g.check(1, now=0)
    assert g.check(1, now=1) > 0
    assert g.check(2, now=1) == 0


def test_minutes_and_freshness():
    assert SpamGuard.minutes(1) == 1 and SpamGuard.minutes(61) == 2
    assert FRESH_FOR == 120
