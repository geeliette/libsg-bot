"""Telegram bot: handlers and entry point.  Run with:  python -m libsg.bot

Env (.env): TELEGRAM_BOT_TOKEN, NLB_API_KEY, NLB_APP_CODE
Optional:   DB_PATH (libsg.db), POLL_MINUTES (30), POLL_SPACING_SECONDS (10)
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from html import escape

from dotenv import load_dotenv
from telegram import LinkPreviewOptions, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, NetworkError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from libsg import messages as M
from libsg.branches import parse_branches
from libsg.db import Store
from libsg.grouping import SuggestedWork, suggest_works
from libsg.models import Edition, parse_search
from libsg.nlb_client import NLBClient, NLBError
from libsg.poller import PollResult, check_editions, poll_once
from libsg.ratelimit import FRESH_FOR, SpamGuard

log = logging.getLogger("libsg.bot")
HTML = ParseMode.HTML
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
SEARCH_PAGE = 20  # NLB groups fetched per API call; we show M.PAGE_SIZE at a time
SAVE_SPACING = 1.5  # seconds between calls the user waits for (save, Check now); lowered from 3 s, watch logs for 429s


@dataclass
class Search:
    """A user's latest search, kept in user_data so 'More results' can page through it."""

    gen: int
    query: str
    works: list[SuggestedWork]
    next_offset: int | None  # NLB offset for the next API page, None if NLB has no more
    start: int = 0  # index of the first result on the current page

    def has_more(self) -> bool:
        return self.start + M.PAGE_SIZE < len(self.works) or self.next_offset is not None


@dataclass
class Draft:
    """A watch being set up, kept in user_data until saved."""

    title: str
    author: str
    editions: list[Edition]
    selected: set[int]
    branches: set[str] = field(default_factory=set)
    gen: int | None = None  # search it came from (for "Back to results")
    page_start: int = 0  # first edition shown (edition list is paged)
    watch_id: int | None = None  # set when editing an existing watch
    message_id: int | None = None  # the message this set-up lives in; buttons elsewhere can't touch it
    notify: bool = True  # alerts on/off

    def back_button(self) -> tuple[str, str] | None:
        if self.watch_id is not None:
            return ("‹ Cancel", "wc")
        if self.gen is not None:
            return ("‹ Back to results", f"r:{self.gen}")
        return None


# --- helpers ----------------------------------------------------------------


def _store(ctx: ContextTypes.DEFAULT_TYPE) -> Store:
    return ctx.bot_data["store"]


def _client(ctx: ContextTypes.DEFAULT_TYPE) -> NLBClient:
    return ctx.bot_data["client"]


def _names(ctx: ContextTypes.DEFAULT_TYPE) -> dict[str, str]:
    """Branch code -> short name, including closed branches (so old watches still read well)."""
    return {b.code: b.short_name for b in _store(ctx).branches()}


async def _nlb(ctx: ContextTypes.DEFAULT_TYPE, fn, *args, **kwargs):
    """Call the (synchronous) NLB client off the event loop, one call at a time.

    The lock keeps searches, Check now and the regular poll from bursting NLB together.
    SQLite stays on the loop thread.
    """
    async with ctx.bot_data["nlb_lock"]:
        return await asyncio.to_thread(fn, *args, **kwargs)


async def _fetch(ctx: ContextTypes.DEFAULT_TYPE, brn: int) -> list[dict]:
    return await _nlb(ctx, _client(ctx).get_all_availability, brn)


async def _search_page(ctx: ContextTypes.DEFAULT_TYPE, query: str, offset: int) -> tuple[list[SuggestedWork], int | None]:
    data = await _nlb(ctx, _client(ctx).search_titles, query[:250], SEARCH_PAGE, offset, MaterialTypes=["bks"])
    works = suggest_works(parse_search(data), physical_only=True)
    nxt = data.get("nextRecordsOffset") if data.get("hasMoreRecords") else None
    return works, nxt


def _stale_brns(store: Store, brns: list[int], max_age: float) -> list[int]:
    """BRNs never checked, or last checked more than max_age seconds ago."""
    times = store.checked_times(brns)
    now = datetime.now(timezone.utc)

    def age(iso: str) -> float:
        t = datetime.fromisoformat(iso)
        return (now - (t if t.tzinfo else t.replace(tzinfo=timezone.utc))).total_seconds()  # naive = UTC

    return [b for b in brns if b not in times or age(times[b]) >= max_age]


def _baseline_age(ctx: ContextTypes.DEFAULT_TYPE) -> float:
    """Snapshots older than one poll interval aren't kept fresh by anyone, so they're stale as a baseline."""
    return ctx.bot_data["poll_minutes"] * 60


def _progress(q, render):
    """Progress callback for long checks: edit the message every few editions."""
    async def update(done: int, total: int) -> None:
        if done < total and done % M.PROGRESS_EVERY == 0:
            await q.edit_message_text(render(done), parse_mode=HTML)
    return update


async def _send_alerts(ctx: ContextTypes.DEFAULT_TYPE, result: PollResult, skip_user: int | None = None) -> None:
    store = _store(ctx)
    for (user_id, work_id), alerts in result.alerts.items():
        if user_id == skip_user:
            continue
        labels = {
            r["brn"]: r["label"] or " ".join(str(x) for x in (r["year"], r["format_name"]) if x)
            for r in store.work_editions(work_id)
        }
        try:
            await ctx.bot.send_message(user_id, M.alert_text(alerts[0].work_title, alerts, labels), parse_mode=HTML)
        except Forbidden:
            log.info("User %s blocked the bot; alert dropped", user_id)
        except BadRequest as e:
            log.warning("Alert to %s failed: %s", user_id, e)


# --- commands ---------------------------------------------------------------


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(M.START, parse_mode=HTML)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(M.help_text(ctx.bot_data["poll_minutes"]), parse_mode=HTML)


async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    query = " ".join(ctx.args or []).strip()
    if not query:
        await msg.reply_text(
            "Send <code>/search</code> followed by a title, author or ISBN, e.g.\n"
            "<code>/search pride and prejudice</code>",
            parse_mode=HTML,
        )
        return
    placeholder = await msg.reply_text(M.searching_text(query), parse_mode=HTML)
    try:
        works, nxt = await _search_page(ctx, query, 0)
    except NLBError as e:
        log.warning("Search failed: %s", e)
        await placeholder.edit_text(M.busy_text() if e.status == 429 else "NLB search failed. Please try again later.")
        return
    if not works:
        await placeholder.edit_text(
            f"No print books found for “{query}”. Check the spelling, or try fewer or different words."
        )
        return
    gen = ctx.user_data.get("gen", 0) + 1
    ctx.user_data["gen"] = gen
    ctx.user_data.pop("draft", None)
    s = Search(gen, query, works, nxt)
    ctx.user_data["search"] = s
    await placeholder.edit_text(
        M.results_text(query, works, 0, s.has_more()),
        parse_mode=HTML,
        reply_markup=M.results_keyboard(gen, 0, min(M.PAGE_SIZE, len(works)), s.has_more()),
    )


def _watch_view(store: Store, watch, branches) -> str:
    """Text for one watched book: editions, alert libraries, and where it's on the shelf at every library
    as of the last check."""
    names = {b.code: b.short_name for b in branches}
    closed = {b.code for b in branches if not b.is_open}
    work = store.get_work(watch.work_id)
    physical = [r["brn"] for r in store.work_editions(watch.work_id) if r["physical"]]
    on_shelf: dict[str, int] | None = None
    snaps = [store.get_snapshot(b) for b in physical]
    if any(snap is not None for snap in snaps):
        on_shelf = {}
        for snap in snaps:
            for code, n in (snap or {}).items():
                if n:  # every library; alert libraries are listed first by the message
                    on_shelf[code] = on_shelf.get(code, 0) + n
    checked = M.ago(store.last_checked(physical)) if physical else ""
    return M.watch_text(work.title, work.author, len(work.brns), watch.branches, names, closed, on_shelf, checked,
                        notify=watch.notify)


async def cmd_books(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    store = _store(ctx)
    msg = update.effective_message
    watches = store.list_watches(update.effective_user.id)
    if not watches:
        await msg.reply_text("You're not watching anything yet. Try /search.")
        return
    branches = store.branches()
    await msg.reply_text(f"You're watching {M.plural(len(watches), 'book')}:")
    for w in watches:
        await msg.reply_text(_watch_view(store, w, branches), parse_mode=HTML, reply_markup=M.watch_keyboard(w.watch_id, w.notify))


# --- button callbacks -------------------------------------------------------


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    data = q.data or ""
    try:
        if data.startswith("w:"):
            await _pick_work(update, ctx, data)
        elif data.startswith("m:"):
            await _more(update, ctx, int(data[2:]))
        elif data.startswith("p:"):
            await _previous(update, ctx, int(data[2:]))
        elif data.startswith("r:"):
            await _back_to_results(update, ctx, int(data[2:]))
        elif data.startswith("n:"):
            await _toggle_notify(update, ctx, int(data[2:]))
        elif data == "bn":
            await _toggle_draft_notify(update, ctx)
        elif data.startswith("c:"):
            await _check_now(update, ctx, int(data[2:]))
        elif data.startswith("ed:"):
            await _edit_watch(update, ctx, int(data[3:]))
        elif data == "wc":
            await _cancel_edit(update, ctx)
        elif data == "eb":
            await _back_to_editions(update, ctx)
        elif data.startswith("e:") or data.startswith("ep:") or data in ("ea", "ec", "en"):
            await _edit_editions(update, ctx, data)
        elif data.startswith("b:"):
            await _toggle_branch(update, ctx, data[2:])
        elif data == "bs":
            await _save(update, ctx)
        elif data.startswith("x:"):
            await _stop(update, ctx, int(data[2:]))
        else:
            await q.answer()
    except BadRequest as e:
        # "Message is not modified" when a tap doesn't change anything -- harmless.
        if "not modified" not in str(e).lower():
            raise
        try:
            await q.answer()
        except BadRequest:
            pass  # already answered


def _search(ctx: ContextTypes.DEFAULT_TYPE, gen: int) -> Search | None:
    s = ctx.user_data.get("search")
    return s if s and s.gen == gen else None


def _message_id(update: Update) -> int | None:
    return getattr(update.callback_query.message, "message_id", None)


def _draft(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> Draft | None:
    """The set-up in progress -- but only for buttons on the message it lives in.

    Only one set-up is kept per user. Without this check, a button on an older message
    (e.g. book A's) would change, and could save over, the current one (e.g. book B's edit).
    """
    draft = ctx.user_data.get("draft")
    return draft if draft and draft.message_id == _message_id(update) else None


async def _expired(update: Update) -> None:
    await update.callback_query.answer(
        "This message is out of date. Run /search again, or use /books to edit a saved book.",
        show_alert=True,
    )


async def _more(update: Update, ctx: ContextTypes.DEFAULT_TYPE, gen: int) -> None:
    q = update.callback_query
    s = _search(ctx, gen)
    if not s:
        await _expired(update)
        return
    start = s.start + M.PAGE_SIZE
    if start + M.PAGE_SIZE > len(s.works) and s.next_offset is not None:
        # The button shows a spinner until answered; answer once, after loading.
        try:
            more, nxt = await _search_page(ctx, s.query, s.next_offset)
        except NLBError as e:
            log.warning("More results failed: %s", e)
            await q.answer(M.busy_text() if e.status == 429 else "Couldn't load more results.", show_alert=True)
            return
        seen = {b for w in s.works for b in w.brns}
        s.works += [w for w in more if not seen & set(w.brns)]
        s.next_offset = nxt
    await q.answer()
    if start >= len(s.works):
        s.next_offset = None
        await q.edit_message_reply_markup(M.results_keyboard(s.gen, s.start, min(M.PAGE_SIZE, len(s.works) - s.start), False))
        return
    s.start = start
    await q.edit_message_text(
        M.results_text(s.query, s.works, start, s.has_more()),
        parse_mode=HTML,
        reply_markup=M.results_keyboard(s.gen, start, min(M.PAGE_SIZE, len(s.works) - start), s.has_more()),
    )


async def _show_editions(update: Update, draft: Draft) -> None:
    await update.callback_query.edit_message_text(
        M.editions_text(draft.title, draft.author, draft.editions, draft.selected, draft.page_start),
        parse_mode=HTML,
        link_preview_options=NO_PREVIEW,
        reply_markup=M.editions_keyboard(draft.editions, draft.selected, draft.back_button(), draft.page_start),
    )


async def _pick_work(update: Update, ctx: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    _, gen, i = data.split(":")
    s = _search(ctx, int(gen))
    if not s or int(i) >= len(s.works):
        await _expired(update)
        return
    w = s.works[int(i)]
    draft = Draft(w.title, w.author, list(w.editions), {e.brn for e in w.editions}, gen=s.gen,
                  message_id=_message_id(update))
    ctx.user_data["draft"] = draft
    await update.callback_query.answer()
    await _show_editions(update, draft)


async def _edit_editions(update: Update, ctx: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    q = update.callback_query
    draft = _draft(update, ctx)
    if not draft:
        await _expired(update)
        return
    if data == "en":
        if not draft.selected:
            await q.answer("Tick at least one edition.", show_alert=True)
            return
        await q.answer()
        branches = _store(ctx).branches(open_only=True)
        await q.edit_message_text(M.branches_text(draft.title, bool(branches), draft.notify), parse_mode=HTML,
                                  reply_markup=M.branches_keyboard(branches, draft.branches, draft.notify))
        return
    if data.startswith("ep:"):
        draft.page_start = min(max(0, int(data[3:])), max(0, len(draft.editions) - 1))
    elif data == "ea":
        draft.selected = {e.brn for e in draft.editions}
    elif data == "ec":
        draft.selected = set()
    else:
        draft.selected ^= {int(data[2:])}
    await q.answer()
    await _show_editions(update, draft)


async def _render_results(update: Update, s: Search) -> None:
    count = min(M.PAGE_SIZE, len(s.works) - s.start)
    await update.callback_query.edit_message_text(
        M.results_text(s.query, s.works, s.start, s.has_more()),
        parse_mode=HTML,
        reply_markup=M.results_keyboard(s.gen, s.start, count, s.has_more()),
    )


async def _previous(update: Update, ctx: ContextTypes.DEFAULT_TYPE, gen: int) -> None:
    s = _search(ctx, gen)
    if not s:
        await _expired(update)
        return
    s.start = max(0, s.start - M.PAGE_SIZE)
    await update.callback_query.answer()
    await _render_results(update, s)


async def _back_to_results(update: Update, ctx: ContextTypes.DEFAULT_TYPE, gen: int) -> None:
    s = _search(ctx, gen)
    if not s:
        await _expired(update)
        return
    if _draft(update, ctx):  # only drop the set-up that lives in this message
        ctx.user_data.pop("draft", None)
    await update.callback_query.answer()
    await _render_results(update, s)


async def _back_to_editions(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    draft = _draft(update, ctx)
    if not draft:
        await _expired(update)
        return
    await update.callback_query.answer()
    await _show_editions(update, draft)


async def _edit_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE, watch_id: int) -> None:
    q = update.callback_query
    store = _store(ctx)
    w = store.get_watch(watch_id)
    if not w or w.user_id != update.effective_user.id:
        await q.answer("That watch no longer exists.", show_alert=True)
        await q.edit_message_reply_markup(None)
        return
    work = store.get_work(w.work_id)
    editions, watched = store.edition_choices(w.work_id)
    ctx.user_data["draft"] = Draft(work.title, work.author, editions, watched, set(w.branches), watch_id=watch_id,
                                   notify=w.notify, message_id=_message_id(update))
    await q.answer()
    await _show_editions(update, ctx.user_data["draft"])


async def _check_now(update: Update, ctx: ContextTypes.DEFAULT_TYPE, watch_id: int) -> None:
    q = update.callback_query
    store, user_id = _store(ctx), update.effective_user.id
    w = store.get_watch(watch_id)
    if not w or w.user_id != user_id:
        await q.answer("That watch no longer exists.", show_alert=True)
        await q.edit_message_reply_markup(None)
        return
    guard: SpamGuard = ctx.bot_data["spam_guard"]
    if wait := guard.check(user_id):
        await q.answer(M.check_paused_text(guard.minutes(wait)), show_alert=True)
        return
    physical = [r["brn"] for r in store.work_editions(w.work_id) if r["physical"]]
    stale = _stale_brns(store, physical, FRESH_FOR)
    view = lambda: (_watch_view(store, w, store.branches()), M.watch_keyboard(watch_id, w.notify))

    if not stale:
        await q.answer(f"Just checked ({M.ago(store.last_checked(physical))}) – showing that.")
        text, kb = view()
        await q.edit_message_text(text, parse_mode=HTML, reply_markup=kb)
        return
    await q.answer()
    work = store.get_work(w.work_id)
    estimate = M.estimate_text(len(stale), SAVE_SPACING)
    await q.edit_message_text(M.checking_now_text(work.title, len(stale), estimate), parse_mode=HTML)
    result = await check_editions(
        store, stale, lambda b: _fetch(ctx, b), spacing=SAVE_SPACING,
        on_progress=_progress(q, lambda done: M.checking_now_text(work.title, len(stale), estimate, done)),
    )
    # The user is looking at the result; alerts from this check go to *other* watchers only.
    await _send_alerts(ctx, result, skip_user=user_id)
    text, kb = view()
    if result.rate_limited or result.failed:
        text += "\n<i>NLB was busy, so some editions show their previous check.</i>"
    await q.edit_message_text(text, parse_mode=HTML, reply_markup=kb)


async def _cancel_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    draft = _draft(update, ctx)
    if not draft:
        await _expired(update)
        return
    ctx.user_data.pop("draft", None)
    store = _store(ctx)
    w = store.get_watch(draft.watch_id) if draft.watch_id is not None else None
    await q.answer("No changes made")
    if not w:
        await q.edit_message_text("No changes made. Send /books to see your watched books.")
        return
    await q.edit_message_text(_watch_view(store, w, store.branches()), parse_mode=HTML,
                              reply_markup=M.watch_keyboard(w.watch_id, w.notify))


async def _toggle_branch(update: Update, ctx: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    q = update.callback_query
    draft = _draft(update, ctx)
    if not draft:
        await _expired(update)
        return
    branches = _store(ctx).branches(open_only=True)
    if code == "*":
        draft.branches.clear()
    elif code in {b.code for b in branches}:
        draft.branches ^= {code}
    await q.answer()
    await q.edit_message_reply_markup(M.branches_keyboard(branches, draft.branches, draft.notify))


async def _toggle_draft_notify(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    draft = _draft(update, ctx)
    if not draft:
        await _expired(update)
        return
    draft.notify = not draft.notify  # library picks are kept, so switching back on restores them
    await q.answer(M.notify_label(draft.notify))
    branches = _store(ctx).branches(open_only=True)
    await q.edit_message_text(  # text changes too: with alerts off there are no libraries to pick
        M.branches_text(draft.title, bool(branches), draft.notify), parse_mode=HTML,
        reply_markup=M.branches_keyboard(branches, draft.branches, draft.notify),
    )


async def _toggle_notify(update: Update, ctx: ContextTypes.DEFAULT_TYPE, watch_id: int) -> None:
    """🔔/🔕 on a book in /books."""
    q = update.callback_query
    store = _store(ctx)
    w = store.get_watch(watch_id)
    if not w or w.user_id != update.effective_user.id:
        await q.answer("That watch no longer exists.", show_alert=True)
        await q.edit_message_reply_markup(None)
        return
    store.set_notify(watch_id, not w.notify)
    w = store.get_watch(watch_id)
    await q.answer(M.notify_label(w.notify))
    if w.notify:
        # Its snapshots may be days old (books with alerts off aren't polled). Refresh them first,
        # so the next poll doesn't alert about copies that were already on the shelf.
        physical = [r["brn"] for r in store.work_editions(w.work_id) if r["physical"]]
        stale = _stale_brns(store, physical, _baseline_age(ctx))
        if stale:
            estimate = M.estimate_text(len(stale), SAVE_SPACING)
            await q.edit_message_text(M.alerts_on_checking_text(w.work_title, len(stale), estimate), parse_mode=HTML)
            result = await check_editions(
                store, stale, lambda b: _fetch(ctx, b), spacing=SAVE_SPACING,
                on_progress=_progress(q, lambda done: M.alerts_on_checking_text(w.work_title, len(stale), estimate, done)),
            )
            await _send_alerts(ctx, result, skip_user=w.user_id)
    await q.edit_message_text(_watch_view(store, w, store.branches()), parse_mode=HTML,
                              reply_markup=M.watch_keyboard(watch_id, w.notify))


async def _save(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    draft = _draft(update, ctx)
    if not draft:
        await _expired(update)
        return
    store, user_id = _store(ctx), update.effective_user.id
    editions = [e for e in draft.editions if e.brn in draft.selected]

    # Saving the same book again (or editing it) replaces the earlier work + watch instead of duplicating it.
    old = set(store.works_sharing_editions(user_id, draft.selected))
    if draft.watch_id is not None and (w := store.get_watch(draft.watch_id)) and w.user_id == user_id:
        old.add(w.work_id)
    for wid in old:
        store.delete_work(wid)
    # Keep the unticked candidates too, so Edit can offer them again.
    work_id = store.create_work(user_id, draft.title, draft.author, draft.editions, watched=draft.selected)
    store.set_watch(user_id, work_id, draft.branches, notify=draft.notify)
    ctx.user_data.pop("draft", None)

    physical = [e.brn for e in editions if e.physical]
    # Never-checked editions always get a first check. With alerts on, editions whose last check is
    # older than a poll interval (e.g. from a time alerts were off) are refreshed too, so the next poll
    # doesn't alert about copies that were already on the shelf.
    unchecked = _stale_brns(store, physical, _baseline_age(ctx) if draft.notify else float("inf"))
    await q.answer("Saved")
    estimate = M.estimate_text(len(unchecked), SAVE_SPACING)
    if unchecked:  # editions with a fresh check need no new call
        await q.edit_message_text(M.checking_text(draft.title, len(unchecked), estimate), parse_mode=HTML)

    # Baseline: check editions we've never seen. Any alerts this produces belong to *other*
    # users already watching the same BRN (a first check never alerts), so deliver those.
    result = await check_editions(
        store, unchecked, lambda b: _fetch(ctx, b), spacing=SAVE_SPACING,
        on_progress=_progress(q, lambda done: M.checking_text(draft.title, len(unchecked), estimate, done)),
    )
    await _send_alerts(ctx, result, skip_user=user_id)

    on_shelf: dict[str, int] | None = {}
    for brn in physical:
        snap = store.get_snapshot(brn)
        if snap is None:
            on_shelf = None
            break
        for code, n in snap.items():
            if n:  # every library; alert libraries are listed first by the message
                on_shelf[code] = on_shelf.get(code, 0) + n
    await q.edit_message_text(
        M.watch_saved_text(draft.title, draft.branches, on_shelf, _names(ctx), replaced=bool(old), notify=draft.notify),
        parse_mode=HTML,
    )


async def _stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE, watch_id: int) -> None:
    q = update.callback_query
    store = _store(ctx)
    w = store.get_watch(watch_id)
    if not w or w.user_id != update.effective_user.id:
        await q.answer("Already stopped.")
        await q.edit_message_reply_markup(None)
        return
    store.delete_work(w.work_id)  # works are per user and only exist for their watch
    await q.answer("Stopped")
    await q.edit_message_text(f"Stopped watching <b>{escape(w.work_title)}</b>.", parse_mode=HTML)


# --- jobs -------------------------------------------------------------------


async def poll_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if ctx.bot_data.get("polling"):
        log.info("Previous round still running; skipping")
        return
    ctx.bot_data["polling"] = True
    try:
        result = await poll_once(_store(ctx), lambda b: _fetch(ctx, b), spacing=ctx.bot_data["spacing"])
        log.info("Poll: checked=%d failed=%d skipped=%d alerts=%d", result.checked, result.failed,
                 result.skipped, sum(len(a) for a in result.alerts.values()))
        await _send_alerts(ctx, result)
    finally:
        ctx.bot_data["polling"] = False


async def refresh_branches(bot_data: dict) -> None:
    """Reload NLB's branch list so closures and reopenings show up."""
    try:
        data = await asyncio.to_thread(bot_data["client"].get_branches)
    except Exception as e:  # keep the last good list on any failure (API error, network, bad JSON)
        log.warning("Branch refresh failed, keeping last list: %s", e)
        return
    branches = parse_branches(data)
    bot_data["store"].save_branches(branches)
    log.info("Branches: %d open, %d closed", sum(b.is_open for b in branches), sum(not b.is_open for b in branches))


async def refresh_branches_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await refresh_branches(ctx.bot_data)


async def post_init(app: Application) -> None:
    # Load branches before taking messages, so the very first picker has them.
    await refresh_branches(app.bot_data)


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(ctx.error, NetworkError):
        # Dropped connection to Telegram (e.g. laptop asleep); PTB reconnects by itself.
        log.warning("Telegram network error: %s", ctx.error)
        return
    log.error("Unhandled error: %s", ctx.error, exc_info=ctx.error)


# --- entry point ------------------------------------------------------------


def build_app() -> Application:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN in environment/.env")
    poll_minutes = float(os.getenv("POLL_MINUTES", "30"))

    app = Application.builder().token(token).post_init(post_init).build()
    app.bot_data["store"] = Store(os.getenv("DB_PATH", "libsg.db"))
    app.bot_data["client"] = NLBClient()
    app.bot_data["spacing"] = float(os.getenv("POLL_SPACING_SECONDS", "10"))
    app.bot_data["poll_minutes"] = poll_minutes
    app.bot_data["nlb_lock"] = asyncio.Lock()
    app.bot_data["spam_guard"] = SpamGuard()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("books", cmd_books))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_error_handler(on_error)
    app.job_queue.run_repeating(refresh_branches_job, interval=24 * 3600, first=24 * 3600, name="branches")
    app.job_queue.run_repeating(poll_job, interval=poll_minutes * 60, first=60, name="poll")
    return app


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    # httpx logs full request URLs at INFO, and Telegram URLs contain the bot token.
    for noisy in ("httpx", "httpcore", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    app = build_app()
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        app.bot_data["client"].close()
        app.bot_data["store"].close()


if __name__ == "__main__":
    main()
