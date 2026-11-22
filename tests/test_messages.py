from libsg import messages as M
from libsg.branches import parse_branches
from libsg.grouping import SuggestedWork, suggest_works
from libsg.models import Edition, parse_search
from libsg.watcher import Alert


def _works(data):
    return suggest_works(parse_search(data), physical_only=True)


def _callbacks(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def _texts(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


def test_help_mentions_interval_and_commands():
    t = M.help_text(30)
    assert "checked every 30 minutes" in t
    for cmd in ("/search", "/books"):
        assert f"<b>{cmd}</b>" in t  # each command has its own section
    assert "all its editions in one place" in t  # what search does
    assert "alerts are on by default" in t and "alert libraries" in t
    assert "🔔" in t and "🔕" in t and "Check now" in t
    assert "ISBN" in t and "NLB app" in t
    assert "/search" in M.START
    assert len(t) < 800  # keep it short
    import re

    for text in (M.START, t):  # impersonal: no "I", "I'll", "I'm"...
        assert not re.search(r"\bI\b|\bI'", text), text
    assert len(M.START) < 200 and "/help" in M.START
    assert "Alerts" not in M.START and "/books" not in M.START  # explained in /help, not repeated


def test_short_author():
    assert M.short_author("Austen, Jane, 1775-1817") == "Jane Austen"
    assert M.short_author("Plato") == "Plato"
    assert M.short_author("陈启淦 | Chen, Qigan") == "陈启淦 (Qigan Chen)"


def test_estimate_text():
    assert M.estimate_text(0, 3) == "about 5 seconds"
    assert M.estimate_text(3, 3) == "about 15 seconds"
    assert M.estimate_text(20, 3) == "about 2 minutes"


# --- results ---------------------------------------------------------------


def test_results_first_page(pp_search):
    works = _works(pp_search)
    t = M.results_text("pride and prejudice", works, 0, has_more=True)
    assert t.startswith("Top 5 matches for “pride and prejudice” (print books only)")
    assert "<b>1. " in t and "<b>5. " in t and "<b>6. " not in t
    assert "Chinese" in t  # language shown when not just English
    assert "author's name" in t
    assert "That's all" not in t


def test_results_later_page_and_end(pp_search):
    works = _works(pp_search)
    t = M.results_text("pp", works, 5, has_more=False)
    assert t.startswith("Matches 6–")
    assert "<b>6. " in t
    assert "That's all the matches." in t


def test_results_keyboard():
    mk = M.results_keyboard(7, 5, 5, has_more=True)
    assert _texts(mk) == ["6", "7", "8", "9", "10", "‹ Previous", "More results ▸"]
    assert _callbacks(mk) == ["w:7:5", "w:7:6", "w:7:7", "w:7:8", "w:7:9", "p:7", "m:7"]
    assert "m:7" not in _callbacks(M.results_keyboard(7, 0, 3, has_more=False))
    assert "p:7" not in _callbacks(M.results_keyboard(7, 0, 5, has_more=True))  # no Previous on page 1
    assert _texts(M.results_keyboard(7, 10, 2, has_more=False))[-1] == "‹ Previous"


def test_escapes_html():
    w = SuggestedWork("Cats & <Dogs>", "", (Edition(1, "Cats & <Dogs>", "", "1", "Book", 2020),))
    assert "Cats &amp; &lt;Dogs&gt;" in M.results_text("<q>", [w], 0, False)
    assert "&lt;q&gt;" in M.results_text("<q>", [w], 0, False)


# --- editions --------------------------------------------------------------


def _biggest(data):
    return max(_works(data), key=lambda w: len(w.editions))


def test_editions_text_details(pp_search):
    w = next(w for w in _works(pp_search) if 200014276 in w.brns)
    t = M.editions_text(w.title, w.author, list(w.editions), set(w.brns))
    assert "✅ <b>1.</b> 2005 · Chinese · Da zhong shu ju · 221 pages" in t
    assert "ISBN 9814185299" in t
    assert 'href="https://catalogue.nlb.gov.sg/search/card?recordId=200014276">View in catalogue</a>' in t
    assert "Cover" not in t
    assert "200014276" not in t.replace("recordId=200014276", "")  # BRN only inside the link


def test_every_edition_has_its_own_catalogue_link(emma_search):
    emma = _works(emma_search)[0]
    t = M.editions_text(emma.title, emma.author, list(emma.editions), set(emma.brns))
    for e in emma.editions:  # incl. the Indonesian edition NLB files separately
        assert f'<a href="https://catalogue.nlb.gov.sg/search/card?recordId={e.brn}">View in catalogue</a>' in t
    assert t.count("View in catalogue</a>") == len(emma.editions)
    assert "several editions on one page" in t


def test_editions_text_ticks(pp_search):
    w = _biggest(pp_search)
    assert len(w.editions) >= 5
    t = M.editions_text(w.title, w.author, list(w.editions), {w.editions[0].brn})
    assert "✅ <b>1.</b>" in t and "⬜ <b>2.</b>" in t


def _alice(alice_search):
    return _biggest(alice_search)


def test_long_edition_lists_are_paged_not_trimmed(alice_search):
    w = _alice(alice_search)
    eds = list(w.editions)
    assert len(eds) == 17
    sel = set(w.brns)
    p1 = M.editions_text(w.title, w.author, eds, sel, start=0)
    p2 = M.editions_text(w.title, w.author, eds, sel, start=10)
    assert "Editions 1–10 of 17 · 17 ticked in total" in p1
    assert "Editions 11–17 of 17" in p2
    assert "<b>1.</b>" in p1 and "<b>10.</b>" in p1 and "<b>11.</b>" not in p1
    assert "<b>11.</b>" in p2 and "<b>17.</b>" in p2
    for page in (p1, p2):
        assert len(page) <= M.MAX_TEXT
        # every edition keeps its detail line (catalogue link, ISBN where known)
        assert page.count("View in catalogue</a>") == page.count("✅ <b>")


def test_short_lists_have_no_paging(pp_search):
    eds = list(_biggest(pp_search).editions)[: M.EDITIONS_PAGE]
    t = M.editions_text("T", "", eds, set())
    assert "Editions 1–" not in t
    assert not any(c.startswith("ep:") for c in _callbacks(M.editions_keyboard(eds, set())))


def test_editions_keyboard_paging(alice_search):
    eds = list(_alice(alice_search).editions)
    k1 = M.editions_keyboard(eds, set(), ("‹ Back to results", "r:1"), start=0)
    assert _texts(k1)[0] == "⬜ 1"
    assert "More editions ▸" in _texts(k1) and "‹ Previous editions" not in _texts(k1)
    assert "ep:10" in _callbacks(k1)
    k2 = M.editions_keyboard(eds, set(), ("‹ Back to results", "r:1"), start=10)
    assert _texts(k2)[0] == "⬜ 11" and _texts(k2)[6] == "⬜ 17"
    assert "‹ Previous editions" in _texts(k2) and "More editions ▸" not in _texts(k2)
    assert "ep:0" in _callbacks(k2)
    assert _callbacks(k2)[-4:] == ["ea", "ec", "r:1", "en"]


def test_editions_text_safety_net_for_huge_entries():
    eds = [Edition(1000 + i, "T", "", "1", "Book", 2000, isbns=("9780000000000",), publisher="P" * 25, pages=300,
                   edition_note="N" * 300) for i in range(10)]
    t = M.editions_text("T", "", eds, {e.brn for e in eds})
    assert len(t) <= M.MAX_TEXT


def test_editions_keyboard(pp_search):
    eds = list(_biggest(pp_search).editions)
    mk = M.editions_keyboard(eds, {eds[0].brn})
    texts = _texts(mk)
    assert texts[0] == "✅ 1" and texts[1] == "⬜ 2"
    assert "⬜ 11" not in texts  # only the first page's buttons
    assert all(len(row) <= 5 for row in mk.inline_keyboard)
    assert _callbacks(mk)[-3:] == ["ea", "ec", "en"]
    assert "ep:10" in _callbacks(mk)  # 15 editions -> a second page
    back = M.editions_keyboard(eds, set(), ("‹ Back to results", "r:3"))
    assert [b.text for b in back.inline_keyboard[-1]] == ["‹ Back to results", "Next →"]
    assert _callbacks(back)[-2:] == ["r:3", "en"]


# --- branches --------------------------------------------------------------


def test_branches_keyboard(branches_data):
    open_branches = [b for b in parse_branches(branches_data) if b.is_open]
    mk = M.branches_keyboard(open_branches, set())
    assert mk.inline_keyboard[0][0].text == "🔔 Alerts on (tap to change)"  # the switch comes first
    assert mk.inline_keyboard[1][0].text == "✅ Any library"
    texts = _texts(M.branches_keyboard(open_branches, {"TRL"}))
    assert "⬜ Any library" in texts and "✅ Tampines" in texts and "Chinatown" in texts
    assert not any(t in texts for t in ("Ang Mo Kio", "Orchard", "library@orchard", "Queenstown"))
    assert all(len(row) <= 3 for row in mk.inline_keyboard)
    assert _callbacks(mk)[0] == "bn" and _callbacks(mk)[-2:] == ["eb", "bs"]


def test_branches_text_without_list():
    assert "couldn't be loaded" in M.branches_text("Emma", has_branches=False)


def test_callback_data_fits_telegram_limit(pp_search, branches_data):
    eds = list(_biggest(pp_search).editions)
    for mk in (M.results_keyboard(999999, 995, 5, True), M.editions_keyboard(eds, set()),
               M.branches_keyboard(parse_branches(branches_data), set()), M.watch_keyboard(10**9)):
        assert all(len(d.encode()) <= 64 for d in _callbacks(mk))


# --- saving / watches / alerts ----------------------------------------------

NAMES = {"TRL": "Tampines", "PRPL": "Pasir Ris", "AMKPL": "Ang Mo Kio"}


def test_checking_text():
    t = M.checking_text("Emma", 3, "about 15 seconds")
    assert "Checking the shelves for 3 editions… this takes about 15 seconds." in t
    assert "/books" in t  # a way back if the check is interrupted
    assert "checked…" not in t
    assert M.checking_text("Emma", 17, "about 1 minute", done=6).endswith("\n6 of 17 checked…")
    assert "checked…" not in M.checking_text("Emma", 17, "about 1 minute", done=17)
    assert M.checking_now_text("Emma", 9, "about 40 seconds", done=3).endswith("\n3 of 9 checked…")


def test_watch_saved_text():
    t = M.watch_saved_text("Emma", {"TRL", "PRPL"}, {"TRL": 1, "BEPL": 2}, {**NAMES, "BEPL": "Bedok"})
    assert t == (
        "Watching: <b>Emma</b>\n"
        "🔔 On the shelf at your alert libraries: Tampines (1)\n"
        "📗 Also on the shelf at 1 library: Bedok (2)\n"
        "\nYou'll get a message when a copy comes back at Pasir Ris, Tampines."
    )
    assert "comes back at any library" in M.watch_saved_text("Emma", set(), {}, NAMES)
    assert "On the shelf: none right now" in M.watch_saved_text("Emma", set(), {}, NAMES)
    assert "Couldn't check" in M.watch_saved_text("Emma", set(), None, NAMES)
    assert M.watch_saved_text("Emma", set(), {}, NAMES, replaced=True).startswith("Updated your watch")


def test_watch_text():
    names = {**NAMES, "BEPL": "Bedok", "CLL": "Central"}
    t = M.watch_text("Emma", "Austen, Jane", 3, {"AMKPL", "TRL"}, names, {"AMKPL"},
                     {"TRL": 2, "BEPL": 1, "CLL": 3, "PRPL": 0}, "5 min ago")
    assert t == (
        "<b>Emma</b> – Jane Austen\n"
        "Watching 3 editions\n"
        "🔔 Alerts at Ang Mo Kio (closed), Tampines\n"
        "🔔 On the shelf at your alert libraries: Tampines (2)\n"
        "📗 Also on the shelf at 2 libraries: Bedok (1), Central (3)\n"  # every library is shown
        "<i>Last checked 5 min ago</i>"
    )
    none_mine = M.watch_text("Emma", "", 1, {"TRL"}, names, on_shelf={"BEPL": 1})
    assert "🔔 Not on the shelf at Tampines right now" in none_mine
    assert "📗 Also on the shelf at 1 library: Bedok (1)" in none_mine
    any_lib = M.watch_text("Emma", "", 1, [], names, on_shelf={"BEPL": 1, "TRL": 2})
    assert "🔔 Alerts at any library" in any_lib
    assert "📗 On the shelf at 2 libraries: Bedok (1), Tampines (2)" in any_lib
    assert "not checked yet" in M.watch_text("Emma", "", 1, [], NAMES)
    assert "none right now" in M.watch_text("Emma", "", 1, [], NAMES, on_shelf={"TRL": 0})
    many = {f"B{i:02d}": 1 for i in range(22)}
    t = M.watch_text("Emma", "", 1, [], {}, on_shelf=many)
    assert "On the shelf at 22 libraries: B00 (1), B01 (1)" in t and "B21 (1)" in t


def test_watch_keyboard():
    assert _callbacks(M.watch_keyboard(12)) == ["c:12", "n:12", "ed:12", "x:12"]
    assert "🔕 Turn alerts off" in _texts(M.watch_keyboard(12, notify=True))
    assert "🔔 Turn alerts on" in _texts(M.watch_keyboard(12, notify=False))


def test_alerts_off_wording():
    # Alerts off: no alert libraries are highlighted; every library is shown the same way.
    t = M.watch_text("Emma", "", 1, {"TRL"}, NAMES, on_shelf={"TRL": 1, "PRPL": 2}, notify=False)
    assert "🔕 Alerts off – not checked automatically, tap Check now to update" in t
    assert "📗 On the shelf at 2 libraries: Pasir Ris (2), Tampines (1)" in t
    assert "alert libraries" not in t
    saved_off = M.watch_saved_text("Emma", {"TRL"}, {}, NAMES, notify=False)
    assert "isn't checked automatically" in saved_off and "You'll get a message" not in saved_off
    t = M.alerts_on_checking_text("Emma", 3, "about 15 seconds", done=1)
    assert "Turning alerts on" in t and t.endswith("\n1 of 3 checked…")


def test_branches_keyboard_alerts_toggle(branches_data):
    open_branches = [b for b in parse_branches(branches_data) if b.is_open]
    on = M.branches_keyboard(open_branches, set())
    off = M.branches_keyboard(open_branches, set(), notify=False)
    assert "🔔 Alerts on (tap to change)" in _texts(on)
    assert "🔕 Alerts off (tap to change)" in _texts(off)
    # Alerts off: no library buttons at all, just the switch, Back and Save.
    assert _callbacks(off) == ["bn", "eb", "bs"]
    assert len(_callbacks(on)) == 3 + 1 + len(open_branches)  # switch, Any library, each library, Back, Save
    # Picks survive switching off and on again (the keyboard just hides them).
    assert "✅ Tampines" in _texts(M.branches_keyboard(open_branches, {"TRL"}, notify=True))
    bt = M.branches_text("Emma")
    assert "which libraries should alert you?" in bt and "every library either way" in bt
    assert "turn alerts off" in bt
    off_text = M.branches_text("Emma", notify=False)
    assert "no libraries to pick" in off_text and "/books shows every library" in off_text
    assert "Check now" in off_text


def test_check_texts():
    assert "Checking the shelves for 2 editions… about 10 seconds." in M.checking_now_text("Emma", 2, "about 10 seconds")
    assert M.check_paused_text(5) == (
        "Too many checks in a short time. Check now is paused for 5 minutes. Regular checks continue as normal."
    )
    assert "paused for 1 minute." in M.check_paused_text(1)


def test_ago():
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
    iso = lambda **kw: (now - timedelta(**kw)).isoformat()
    assert M.ago(iso(seconds=20), now) == "just now"
    assert M.ago(iso(minutes=12), now) == "12 min ago"
    assert M.ago(iso(hours=3), now) == "3 h ago"
    assert M.ago(iso(days=3), now) == "3 days ago"
    assert M.ago(None) == ""


def test_searching_text_escapes_for_html():
    # Must be sent with parse_mode=HTML, otherwise the escape shows literally ("handmaid&#x27;s").
    assert M.searching_text("the handmaid's tale") == "Searching NLB for “the handmaid&#x27;s tale”…"


def test_alert_text():
    alerts = [
        Alert(1, 10, 100, "Emma", 300172872, "TRL", "Tampines Library", 1),
        Alert(1, 10, 100, "Emma", 300123287, "PRPL", "Pasir Ris Library", 2),
    ]
    labels = {300172872: "2025 · English · DK Classics · 467 pages", 300123287: "2024 · English · Puffin Books"}
    assert M.alert_text("Emma", alerts, labels) == (
        "📗 <b>Emma</b> is back on the shelf!\n"
        "• <b>Pasir Ris Library</b>: 2 copies\n"
        "   2024 · English · Puffin Books\n"
        "• <b>Tampines Library</b>: 1 copy\n"
        "   2025 · English · DK Classics · 467 pages"
    )
