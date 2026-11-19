from libsg import messages as M
from libsg.branches import BRANCHES, UNVERIFIED, VERIFIED, short_name
from libsg.grouping import suggest_works
from libsg.models import parse_search
from libsg.watcher import Alert


def _emma(emma_search):
    return suggest_works(parse_search(emma_search), physical_only=True)[0]


def _callbacks(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def test_branches_table():
    assert not set(VERIFIED) & set(UNVERIFIED)
    assert "TRL" in BRANCHES and BRANCHES["TRL"] == "Tampines Library"
    assert not any("Reference" in n or "Repository" in n for n in BRANCHES.values())
    assert short_name("TPPL") == "Toa Payoh"
    assert short_name("CLL") == "Central Library"
    assert short_name("CNPL") == "library@chinatown"


def test_short_author():
    assert M.short_author("Austen, Jane, 1775-1817") == "Jane Austen"
    assert M.short_author("Southam, B. C") == "B. C Southam"
    assert M.short_author("Plato") == "Plato"


def test_work_summary(emma_search):
    text = M.work_summary(_emma(emma_search))
    assert text.startswith("<b>Emma</b>\nJane Austen\n3 print editions (1981–2025)")
    assert "on shelf" in text


def test_summary_escapes_html():
    from libsg.grouping import SuggestedWork
    from libsg.models import Edition

    w = SuggestedWork("Cats & <Dogs>", "", (Edition(1, "Cats & <Dogs>", "", "1", "Book", 2020),))
    assert "Cats &amp; &lt;Dogs&gt;" in M.work_summary(w)


def test_callback_data_fits_telegram_limit(emma_search):
    emma = _emma(emma_search)
    markups = [
        M.work_keyboard(999999, 4),
        M.editions_keyboard(emma.editions, {emma.editions[0].brn}),
        M.branches_keyboard({"TRL"}),
        M.watch_keyboard(10**9),
    ]
    for mk in markups:
        assert all(len(d.encode()) <= 64 for d in _callbacks(mk))


def test_editions_keyboard_ticks(emma_search):
    emma = _emma(emma_search)
    mk = M.editions_keyboard(emma.editions, {300172872})
    labels = [b.text for row in mk.inline_keyboard for b in row]
    assert labels[0] == "✅ 2025 · Book · 300172872"
    assert labels[1].startswith("⬜")
    assert _callbacks(mk)[-1] == "en"


def test_branches_keyboard():
    any_on = M.branches_keyboard(set())
    assert any_on.inline_keyboard[0][0].text == "✅ Any library"
    picked = M.branches_keyboard({"TRL"})
    texts = [b.text for row in picked.inline_keyboard for b in row]
    assert "⬜ Any library" in texts and "✅ Tampines" in texts
    assert set(_callbacks(picked)) >= {"b:*", "bs"} | {f"b:{c}" for c in BRANCHES}
    assert all(len(row) <= 3 for row in picked.inline_keyboard)


def test_watch_saved_text():
    t = M.watch_saved_text("Emma", {"TRL", "PRPL"}, {"TRL": 1})
    assert "Watching: <b>Emma</b> at Pasir Ris, Tampines." in t
    assert "On shelf right now: Tampines (1)." in t
    assert "No copies on the shelf" in M.watch_saved_text("Emma", set(), {})
    assert "any library" in M.watch_saved_text("Emma", set(), {})
    assert "couldn't check" in M.watch_saved_text("Emma", set(), None)
    assert M.watch_saved_text("Emma", set(), {}, replaced=True).startswith("Updated")


def test_alert_text():
    alerts = [
        Alert(1, 10, 100, "Emma", 300172872, "TRL", "Tampines Library", 1),
        Alert(1, 10, 100, "Emma", 300123287, "PRPL", "Pasir Ris Library", 2),
    ]
    eds = {300172872: (2025, "Book"), 300123287: (2024, "Book")}
    assert M.alert_text("Emma", alerts, eds) == (
        "📗 <b>Emma</b> is back on the shelf\n"
        "• Pasir Ris Library: 2 copies – 2024 Book (BRN 300123287)\n"
        "• Tampines Library: 1 copy – 2025 Book (BRN 300172872)"
    )
