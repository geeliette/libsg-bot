"""Telegram bot: handlers and entry point.  Run with:  python -m libsg.bot

Env (.env): TELEGRAM_BOT_TOKEN, NLB_API_KEY, NLB_APP_CODE
Optional:   DB_PATH (libsg.db), POLL_MINUTES (30), POLL_SPACING_SECONDS (10)
"""

from __future__ import annotations

import asyncio
from html import escape
import logging
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from libsg import messages as M
from libsg.branches import BRANCHES
from libsg.db import Store
from libsg.grouping import SuggestedWork, suggest_works
from libsg.models import Edition, parse_search
from libsg.nlb_client import NLBClient, NLBError
from libsg.poller import PollResult, check_editions, poll_once

log = logging.getLogger("libsg.bot")
HTML = ParseMode.HTML
MAX_RESULTS = 5


@dataclass
class Draft:
    """A watch being set up, kept in user_data until saved."""

    title: str
    author: str
    editions: list[Edition]
    selected: set[int]
    branches: set[str] = field(default_factory=set)


# --- helpers ----------------------------------------------------------------


def _store(ctx: ContextTypes.DEFAULT_TYPE) -> Store:
    return ctx.bot_data["store"]


def _client(ctx: ContextTypes.DEFAULT_TYPE) -> NLBClient:
    return ctx.bot_data["client"]


def _spacing(ctx: ContextTypes.DEFAULT_TYPE) -> float:
    return ctx.bot_data["spacing"]


async def _fetch(ctx: ContextTypes.DEFAULT_TYPE, brn: int) -> list[dict]:
    # The NLB client is synchronous; keep it off the event loop. SQLite stays on the loop thread.
    return await asyncio.to_thread(_client(ctx).get_all_availability, brn)


async def _send_alerts(ctx: ContextTypes.DEFAULT_TYPE, result: PollResult, skip_user: int | None = None) -> None:
    store = _store(ctx)
    for (user_id, work_id), alerts in result.alerts.items():
        if user_id == skip_user:
            continue
        eds = {r["brn"]: (r["year"], r["format_name"]) for r in store.work_editions(work_id)}
        try:
            await ctx.bot.send_message(user_id, M.alert_text(alerts[0].work_title, alerts, eds), parse_mode=HTML)
        except Forbidden:
            log.info("User %s blocked the bot; alert dropped", user_id)
        except BadRequest as e:
            log.warning("Alert to %s failed: %s", user_id, e)


# --- commands ---------------------------------------------------------------


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(M.HELP, parse_mode=HTML)


async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    query = " ".join(ctx.args or []).strip()
    if not query:
        await msg.reply_text("Usage: /search <i>title, author or ISBN</i>", parse_mode=HTML)
        return
    try:
        data = await asyncio.to_thread(_client(ctx).search_titles, query[:250], 20)
    except NLBError as e:
        text = "NLB is busy right now, try again in a minute." if e.status == 429 else "NLB search failed, try again later."
        log.warning("Search failed: %s", e)
        await msg.reply_text(text)
        return
    works = suggest_works(parse_search(data), physical_only=True)[:MAX_RESULTS]
    if not works:
        await msg.reply_text("No print editions found. Try other words.")
        return
    gen = ctx.user_data.get("gen", 0) + 1
    ctx.user_data["gen"] = gen
    ctx.user_data["results"] = works
    for i, w in enumerate(works):
        await msg.reply_text(M.work_summary(w), parse_mode=HTML, reply_markup=M.work_keyboard(gen, i))


async def cmd_watches(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    store = _store(ctx)
    msg = update.effective_message
    watches = store.list_watches(update.effective_user.id)
    if not watches:
        await msg.reply_text("You're not watching anything yet. Try /search.")
        return
    for w in watches:
        work = store.get_work(w.work_id)
        if not work:
            continue
        await msg.reply_text(
            M.watch_line(work.title, work.author, len(work.brns), w.branches),
            parse_mode=HTML,
            reply_markup=M.watch_keyboard(w.watch_id),
        )


# --- button callbacks -------------------------------------------------------


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    data = q.data or ""
    try:
        if data.startswith("w:"):
            await _pick_work(update, ctx, data)
        elif data.startswith("e:") or data == "en":
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
        await q.answer()


def _draft(ctx: ContextTypes.DEFAULT_TYPE) -> Draft | None:
    return ctx.user_data.get("draft")


async def _expired(update: Update) -> None:
    await update.callback_query.answer("That's from an old search. Run /search again.", show_alert=True)


async def _pick_work(update: Update, ctx: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    _, gen, i = data.split(":")
    results: list[SuggestedWork] = ctx.user_data.get("results") or []
    if int(gen) != ctx.user_data.get("gen") or int(i) >= len(results):
        await _expired(update)
        return
    w = results[int(i)]
    draft = Draft(w.title, w.author, list(w.editions), {e.brn for e in w.editions})
    ctx.user_data["draft"] = draft
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(M.editions_text(draft.title), parse_mode=HTML,
                              reply_markup=M.editions_keyboard(draft.editions, draft.selected))


async def _edit_editions(update: Update, ctx: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    q = update.callback_query
    draft = _draft(ctx)
    if not draft:
        await _expired(update)
        return
    if data == "en":
        if not draft.selected:
            await q.answer("Keep at least one edition.", show_alert=True)
            return
        await q.answer()
        await q.edit_message_text(M.branches_text(draft.title), parse_mode=HTML,
                                  reply_markup=M.branches_keyboard(draft.branches))
        return
    brn = int(data[2:])
    draft.selected ^= {brn}
    await q.answer()
    await q.edit_message_reply_markup(M.editions_keyboard(draft.editions, draft.selected))


async def _toggle_branch(update: Update, ctx: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    q = update.callback_query
    draft = _draft(ctx)
    if not draft:
        await _expired(update)
        return
    if code == "*":
        draft.branches.clear()
    elif code in BRANCHES:
        draft.branches ^= {code}
    await q.answer()
    await q.edit_message_reply_markup(M.branches_keyboard(draft.branches))


async def _save(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    draft = _draft(ctx)
    if not draft:
        await _expired(update)
        return
    store, user_id = _store(ctx), update.effective_user.id
    editions = [e for e in draft.editions if e.brn in draft.selected]

    # Saving the same book again replaces the earlier work + watch instead of duplicating it.
    old = store.works_sharing_editions(user_id, draft.selected)
    for wid in old:
        store.delete_work(wid)
    work_id = store.create_work(user_id, draft.title, draft.author, editions)
    store.set_watch(user_id, work_id, draft.branches)
    ctx.user_data.pop("draft", None)

    await q.answer("Saved")
    await q.edit_message_text(f"Saved <b>{escape(draft.title)}</b>. Checking the shelves…", parse_mode=HTML)

    # Baseline: check editions we've never seen. Any alerts this produces belong to *other*
    # users already watching the same BRN (a first check never alerts), so deliver those.
    physical = [e.brn for e in editions if e.physical]
    unchecked = [b for b in physical if store.get_snapshot(b) is None]
    result = await check_editions(store, unchecked, lambda b: _fetch(ctx, b), spacing=min(_spacing(ctx), 3))
    await _send_alerts(ctx, result, skip_user=user_id)

    on_shelf: dict[str, int] | None = {}
    for brn in physical:
        snap = store.get_snapshot(brn)
        if snap is None:
            on_shelf = None
            break
        for code, n in snap.items():
            if n and (not draft.branches or code in draft.branches):
                on_shelf[code] = on_shelf.get(code, 0) + n
    await q.edit_message_text(M.watch_saved_text(draft.title, draft.branches, on_shelf, replaced=bool(old)),
                              parse_mode=HTML)


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


# --- polling job ------------------------------------------------------------


async def poll_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if ctx.bot_data.get("polling"):
        log.info("Previous round still running; skipping")
        return
    ctx.bot_data["polling"] = True
    try:
        result = await poll_once(_store(ctx), lambda b: _fetch(ctx, b), spacing=_spacing(ctx))
        log.info("Poll: checked=%d failed=%d skipped=%d alerts=%d", result.checked, result.failed,
                 result.skipped, sum(len(a) for a in result.alerts.values()))
        await _send_alerts(ctx, result)
    finally:
        ctx.bot_data["polling"] = False


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Unhandled error: %s", ctx.error, exc_info=ctx.error)


# --- entry point ------------------------------------------------------------


def build_app() -> Application:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN in environment/.env")
    poll_minutes = float(os.getenv("POLL_MINUTES", "30"))

    app = Application.builder().token(token).build()
    app.bot_data["store"] = Store(os.getenv("DB_PATH", "libsg.db"))
    app.bot_data["client"] = NLBClient()
    app.bot_data["spacing"] = float(os.getenv("POLL_SPACING_SECONDS", "10"))

    app.add_handler(CommandHandler(["start", "help"], cmd_help))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("watches", cmd_watches))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_error_handler(on_error)
    app.job_queue.run_repeating(poll_job, interval=poll_minutes * 60, first=60, name="poll")
    return app


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    # httpx logs full request URLs at INFO, and Telegram URLs contain the bot token.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    app = build_app()
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        app.bot_data["client"].close()
        app.bot_data["store"].close()


if __name__ == "__main__":
    main()
