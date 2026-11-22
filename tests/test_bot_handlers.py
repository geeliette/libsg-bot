"""Button handlers driven with fake Telegram objects (no network).

Regression: only one set-up (draft) is kept per user, so buttons on an older message
used to change -- and could save over -- whatever set-up was current.
"""

import asyncio
from types import SimpleNamespace as NS

import pytest

import libsg.bot as B
from libsg.branches import parse_branches
from libsg.db import Store
from libsg.grouping import suggest_works
from libsg.models import parse_search

USER = 42
OUT_OF_DATE = "This message is out of date"


class FakeQuery:
    def __init__(self, data, message_id):
        self.data = data
        self.message = NS(message_id=message_id)
        self.answers, self.edits, self.markups = [], [], []

    async def answer(self, text=None, show_alert=False):
        self.answers.append(text)

    async def edit_message_text(self, text, **kw):
        self.edits.append(text)
        self.markups.append(kw.get("reply_markup"))

    async def edit_message_reply_markup(self, markup=None, *a, **kw):
        self.edits.append("<buttons>")
        self.markups.append(markup)


@pytest.fixture
def env(emma_search, pp_search, branches_data, monkeypatch):
    store = Store(":memory:")
    store.save_branches(parse_branches(branches_data))

    async def no_fetch(ctx, brn):
        return []

    monkeypatch.setattr(B, "_fetch", no_fetch)
    monkeypatch.setattr(B, "SAVE_SPACING", 0)
    emma = suggest_works(parse_search(emma_search), physical_only=True)[0]
    pp = next(w for w in suggest_works(parse_search(pp_search), physical_only=True) if len(w.editions) > 5)
    ctx = NS(user_data={}, bot_data={"store": store, "poll_minutes": 30, "spam_guard": B.SpamGuard()}, bot=NS())
    yield NS(store=store, ctx=ctx, emma=emma, pp=pp)
    store.close()


def press(env, data, message_id):
    q = FakeQuery(data, message_id)
    asyncio.run(B.on_button(NS(callback_query=q, effective_user=NS(id=USER)), env.ctx))
    return q


def saved_brns(store):
    return {w.work_id: set(store.get_work(w.work_id).brns) for w in store.list_watches(USER)}


def test_old_message_cannot_change_or_save_over_current_setup(env):
    # Book A (Emma) is saved, and so is book B (Pride and prejudice).
    a = env.store.create_work(USER, env.emma.title, env.emma.author, env.emma.editions)
    env.store.set_watch(USER, a)
    b = env.store.create_work(USER, env.pp.title, env.pp.author, env.pp.editions)
    b_watch = env.store.set_watch(USER, b)
    # Message 1: a search for Emma, picked -> set-up for A lives in message 1.
    env.ctx.user_data["search"] = B.Search(1, "emma", [env.emma], None)
    env.ctx.user_data["gen"] = 1
    press(env, "w:1:0", message_id=1)
    # Message 2: Edit book B from /books -> set-up for B replaces it, in message 2.
    press(env, f"ed:{b_watch}", message_id=2)
    draft = env.ctx.user_data["draft"]
    before = set(draft.selected)

    # Tapping A's old buttons in message 1 must not touch B's set-up...
    for data in (f"e:{env.emma.editions[0].brn}", "ea", "ec", "en", "eb", "b:TRL", "bn", "av", "ev", "sv"):
        q = press(env, data, message_id=1)
        assert q.answers and q.answers[-1].startswith(OUT_OF_DATE), data
        assert q.edits == [], data  # message 1 isn't redrawn with B's editions
    assert env.ctx.user_data["draft"] is draft and draft.selected == before

    # ...and Save in message 1 must not save B's set-up over book A.
    saved = saved_brns(env.store)
    q = press(env, "bs", message_id=1)
    assert q.answers[-1].startswith(OUT_OF_DATE)
    assert saved_brns(env.store) == saved

    # The real set-up still works in its own message.
    press(env, "en", message_id=2)
    press(env, "bs", message_id=2)
    works = saved_brns(env.store)
    assert set(env.emma.brns) in works.values()  # book A untouched
    assert len(works) == 2


def test_cancel_and_back_only_affect_their_own_message(env):
    b = env.store.create_work(USER, env.pp.title, env.pp.author, env.pp.editions)
    b_watch = env.store.set_watch(USER, b)
    env.ctx.user_data["search"] = B.Search(1, "emma", [env.emma], None)
    env.ctx.user_data["gen"] = 1
    press(env, "w:1:0", message_id=1)  # set-up A in message 1
    press(env, f"ed:{b_watch}", message_id=2)  # set-up B in message 2 replaces it
    draft = env.ctx.user_data["draft"]

    # "Back to results" on message 1 shows the results but keeps B's set-up.
    press(env, "r:1", message_id=1)
    assert env.ctx.user_data.get("draft") is draft
    # "Cancel" on some other message is out of date and keeps B's set-up too.
    q = press(env, "wc", message_id=3)
    assert q.answers[-1].startswith(OUT_OF_DATE)
    assert env.ctx.user_data.get("draft") is draft
    # Cancel in B's own message ends it.
    press(env, "wc", message_id=2)
    assert "draft" not in env.ctx.user_data


def test_after_restart_buttons_are_out_of_date(env):
    # A restart wipes user_data: any set-up button is out of date, nothing crashes.
    for data in ("e:1", "en", "bs", "b:TRL", "bn", "wc", "r:1", "w:1:0", "av", "ev", "sv"):
        q = press(env, data, message_id=1)
        assert q.answers[-1].startswith(OUT_OF_DATE), data


# --- search only through /search ---------------------------------------------


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)


@pytest.fixture
def searches(env, monkeypatch):
    """Records queries instead of calling NLB."""
    calls = []

    async def fake_run_search(msg, ctx, query):
        calls.append(query)

    monkeypatch.setattr(B, "_run_search", fake_run_search)
    return calls


def send(env, handler, text="", args=None):
    msg = FakeMessage(text)
    env.ctx.args = args
    asyncio.run(handler(NS(effective_message=msg, effective_user=NS(id=USER)), env.ctx))
    return msg


def test_search_with_words_runs_at_once(env, searches):
    send(env, B.cmd_search, "/search emma austen", args=["emma", "austen"])
    assert searches == ["emma austen"]
    send(env, B.on_text, "hello")  # not waiting for a query any more
    assert searches == ["emma austen"]


def test_bare_search_asks_and_next_text_is_the_query(env, searches):
    msg = send(env, B.cmd_search, "/search", args=[])
    assert msg.replies == [B.M.ASK_SEARCH] and searches == []
    send(env, B.on_text, "  pride and prejudice ")
    assert searches == ["pride and prejudice"]
    # Only the one message right after the prompt is a search.
    msg = send(env, B.on_text, "thanks!")
    assert searches == ["pride and prejudice"] and msg.replies == [B.M.USE_SEARCH]


def test_plain_text_without_search_gets_a_hint_and_no_nlb_call(env, searches):
    msg = send(env, B.on_text, "emma")
    assert searches == [] and msg.replies == [B.M.USE_SEARCH]
    assert "/search" in B.M.USE_SEARCH


@pytest.mark.parametrize("command", ["cmd_start", "cmd_help", "cmd_books", "on_unknown_command"])
def test_other_commands_cancel_a_pending_search(env, searches, command):
    send(env, B.cmd_search, "/search", args=[])
    send(env, getattr(B, command), "/x")
    msg = send(env, B.on_text, "emma")
    assert searches == [] and msg.replies == [B.M.USE_SEARCH]


def test_unknown_command_points_to_real_ones(env):
    msg = send(env, B.on_unknown_command, "/watches")
    assert msg.replies == [B.M.UNKNOWN_COMMAND]
    for cmd in ("/search", "/books", "/help"):
        assert cmd in B.M.UNKNOWN_COMMAND


def test_text_and_unknown_command_handlers_are_registered(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("DB_PATH", ":memory:")
    app = B.build_app()
    callbacks = [h.callback for group in app.handlers.values() for h in group]
    # Real commands come before the catch-all for unknown ones.
    assert callbacks.index(B.cmd_books) < callbacks.index(B.on_unknown_command)
    assert B.on_text in callbacks
    app.bot_data["store"].close()
    app.bot_data["client"].close()



# --- availability after picking a result, and the library picker --------------

from libsg.nlb_client import NLBError

A, B2 = 300172872, 300123287  # Emma's two print editions (English)


def copy(brn, code, status="In", reference=False):
    return {"brn": brn, "location": {"code": code, "name": code}, "status": {"code": status},
            "nonCirculatingFlag": reference}


SHELVES = {
    # Central on shelf, Punggol on loan, Queenstown closed for renovation, LD = PublicationSG reference copy.
    A: [copy(A, "CLL"), copy(A, "PRL", "Out"), copy(A, "QUPL"), copy(A, "LD", reference=True)],
    B2: [copy(B2, "BIPL"), copy(B2, "BIPL"), copy(B2, "TPPL", "Out")],
}


@pytest.fixture
def nlb(env, monkeypatch):
    """Fake GetAvailabilityInfo: records BRNs fetched; put a BRN in .busy to get a 429 for it."""
    state = NS(calls=[], busy=set())

    async def fetch(ctx, brn):
        state.calls.append(brn)
        if brn in state.busy:
            raise NLBError(429, "Too Many Requests", "API calls quota exceeded")
        return SHELVES.get(brn, [])

    monkeypatch.setattr(B, "_fetch", fetch)
    env.ctx.user_data["search"] = B.Search(1, "emma", [env.emma], None)
    env.ctx.user_data["gen"] = 1
    return state


def _buttons(q):
    return [b.text for row in q.markups[-1].inline_keyboard for b in row]


def test_picking_a_result_shows_availability(env, nlb):
    assert {e.brn for e in env.emma.editions} == {A, B2}
    q = press(env, "w:1:0", message_id=1)
    assert sorted(nlb.calls) == sorted([A, B2])
    text = q.edits[-1]
    assert "📗 On shelf: Bishan (2), Central (1)" in text
    assert "⏳ On loan: Punggol, Toa Payoh" in text
    assert "Queenstown" not in text and "LD" not in text  # closed library, reference collection
    assert _buttons(q) == ["⭐ Save to My books", "Editions (2 of 2)", "‹ Back to results"]


def test_recent_checks_are_reused_and_dont_count_toward_the_pause(env, nlb):
    press(env, "w:1:0", message_id=1)
    for _ in range(10):  # well over the pause limit, but nothing is fetched
        q = press(env, "w:1:0", message_id=1)
        assert "📗 On shelf" in q.edits[-1]
    assert len(nlb.calls) == 2


def test_too_many_fresh_checks_pause(env, nlb, monkeypatch):
    monkeypatch.setattr(B, "VIEW_FRESH_FOR", 0)  # every open fetches again
    for _ in range(6):
        press(env, "w:1:0", message_id=1)
    q = press(env, "w:1:0", message_id=1)
    assert "paused" in q.answers[-1] and q.edits == []


def test_nlb_busy_shows_try_again_without_partial_results(env, nlb):
    nlb.busy = {B2}
    q = press(env, "w:1:0", message_id=1)
    assert "NLB is busy right now. Try again in a minute." in q.edits[-1]
    assert "On shelf" not in q.edits[-1]
    assert _buttons(q) == ["🔄 Try again", "‹ Back to results"]
    nlb.busy, nlb.calls = set(), []
    q = press(env, "av", message_id=1)
    assert nlb.calls == [B2]  # the edition already fetched is reused
    assert "📗 On shelf: Bishan (2), Central (1)" in q.edits[-1]


def test_unticking_an_edition_updates_availability_without_calls(env, nlb):
    press(env, "w:1:0", message_id=1)
    q = press(env, "ev", message_id=1)
    assert _buttons(q)[-1] == "Done"
    press(env, f"e:{B2}", message_id=1)
    nlb.calls = []
    q = press(env, "en", message_id=1)
    assert nlb.calls == []
    assert "📗 On shelf: Central (1)" in q.edits[-1] and "Bishan" not in q.edits[-1]
    assert "Editions (1 of 2)" in _buttons(q)


def test_picker_lists_only_open_libraries_with_copies_and_saves_without_calls(env, nlb):
    press(env, "w:1:0", message_id=1)
    q = press(env, "sv", message_id=1)
    libs = _buttons(q)[2:-2]  # after the switch and Any library, before Back and Save
    assert libs == ["📗 Bishan", "📗 Central", "⏳ Punggol", "⏳ Toa Payoh"]
    assert "Only libraries with a copy are listed" in q.edits[-1]
    press(env, "b:CLL", message_id=1)
    press(env, "b:TRL", message_id=1)  # Tampines has no copy: not offered, so ignored
    assert env.ctx.user_data["draft"].branches == {"CLL"}
    q = press(env, "eb", message_id=1)  # Back -> availability again
    assert "📗 On shelf" in q.edits[-1]
    nlb.calls = []
    press(env, "sv", message_id=1)
    press(env, "bs", message_id=1)
    assert nlb.calls == []  # everything was just checked
    (w,) = env.store.list_watches(USER)
    assert w.branches == {"CLL"}


def test_saved_book_shows_in_my_books(env, nlb):
    press(env, "w:1:0", message_id=1)
    press(env, "sv", message_id=1)
    press(env, "bs", message_id=1)
    q = press(env, "w:1:0", message_id=2)
    assert "⭐ In My books" in q.edits[-1]
    assert _buttons(q)[0] == "⭐ Update in My books"


def test_editing_drops_closed_picks_and_flags_picks_without_copies(env, nlb):
    wid = env.store.create_work(USER, env.emma.title, env.emma.author, env.emma.editions)
    watch = env.store.set_watch(USER, wid, ["CLL", "TRL", "QUPL"])  # Tampines: no copy; Queenstown: closed
    press(env, f"ed:{watch}", message_id=5)
    assert env.ctx.user_data["draft"].branches == {"CLL", "TRL"}
    q = press(env, "en", message_id=5)  # Next -> never-checked editions are checked, then the picker
    assert sorted(nlb.calls) == sorted([A, B2])
    libs = _buttons(q)[2:-2]
    assert "✅📗 Central" in libs and "✅⚠️ Tampines (no copies)" in libs
    assert not any("Queenstown" in t for t in libs)
    q = press(env, "b:TRL", message_id=5)  # untick it: it leaves the list
    assert not any("Tampines" in t for t in _buttons(q))
    q = press(env, "eb", message_id=5)  # Back while editing -> editions
    assert _buttons(q)[-1] == "Next →"


# --- polling only while libraries are open ---------------------------------------

from datetime import datetime as _dt

from libsg.branches import SINGAPORE, parse_hours


@pytest.fixture
def polls(env, monkeypatch, branches_data):
    rounds = []

    async def fake_poll_once(store, fetch, spacing):
        rounds.append(1)
        return B.PollResult()

    monkeypatch.setattr(B, "poll_once", fake_poll_once)
    env.ctx.bot_data["hours"] = parse_hours(branches_data)
    env.ctx.bot_data["spacing"] = 0
    return rounds


def at(monkeypatch, *when):
    monkeypatch.setattr(B, "_now", lambda: _dt(*when, tzinfo=SINGAPORE))


def test_poll_skipped_while_libraries_are_closed(env, polls, monkeypatch):
    at(monkeypatch, 2026, 11, 23, 3, 0)  # 3 am
    asyncio.run(B.poll_job(env.ctx))
    assert polls == []
    at(monkeypatch, 2026, 11, 23, 10, 30)
    asyncio.run(B.poll_job(env.ctx))
    assert polls == [1]


def test_poll_runs_when_hours_unknown(env, polls, monkeypatch):
    env.ctx.bot_data.pop("hours")
    at(monkeypatch, 2026, 11, 23, 3, 0)
    asyncio.run(B.poll_job(env.ctx))
    assert polls == [1]


def test_check_now_and_results_work_while_libraries_are_closed(env, nlb, polls, monkeypatch):
    at(monkeypatch, 2026, 11, 23, 3, 0)
    q = press(env, "w:1:0", message_id=1)  # opening a result fetches from NLB at 3 am
    assert "📗 On shelf" in q.edits[-1] and nlb.calls
    wid = env.store.create_work(USER, env.emma.title, env.emma.author, env.emma.editions)
    watch = env.store.set_watch(USER, wid)
    env.store.conn.execute("DELETE FROM checked_editions")
    nlb.calls = []
    press(env, f"c:{watch}", message_id=2)  # Check now
    assert sorted(nlb.calls) == sorted([A, B2])


def test_branch_refresh_loads_hours_and_takes_the_nlb_lock(branches_data):
    async def run():
        lock = asyncio.Lock()
        seen = []

        class FakeClient:
            def get_branches(self):
                seen.append(lock.locked())
                return branches_data

        store = Store(":memory:")
        bot_data = {"client": FakeClient(), "store": store, "nlb_lock": lock}
        await B.refresh_branches(bot_data)
        store.close()
        return bot_data, seen

    bot_data, seen = asyncio.run(run())
    assert seen == [True]
    assert "BBPL" in bot_data["hours"] and "QUPL" not in bot_data["hours"]
