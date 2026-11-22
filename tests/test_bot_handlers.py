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
        self.answers, self.edits = [], []

    async def answer(self, text=None, show_alert=False):
        self.answers.append(text)

    async def edit_message_text(self, text, **kw):
        self.edits.append(text)

    async def edit_message_reply_markup(self, *a, **kw):
        self.edits.append("<buttons>")


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
    ctx = NS(user_data={}, bot_data={"store": store, "poll_minutes": 30}, bot=NS())
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
    for data in (f"e:{env.emma.editions[0].brn}", "ea", "ec", "en", "eb", "b:TRL", "bn"):
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
    for data in ("e:1", "en", "bs", "b:TRL", "bn", "wc", "r:1", "w:1:0"):
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
