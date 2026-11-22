"""Bot message text and inline keyboards. Pure functions -- no I/O.

All text is Telegram HTML (parse_mode="HTML"); user/API strings are escaped.

Callback data (Telegram limit: 64 bytes):
    w:<gen>:<i>   pick result i (0-based, across pages) from search generation gen
    m:<gen>       more results;  p:<gen> = previous results;  r:<gen> = back to results
    e:<brn>       toggle an edition;  ea = select all, ec = clear, en = next;  ep:<start> = page of editions
    b:<CODE>      toggle a branch;  b:* = any library;  bn = alerts on/off;  bs = save;  eb = back to editions
    n:<watch_id>  turn alerts on/off for a saved watch
    ed:<watch_id> edit a watch;  wc = cancel editing;  c:<watch_id> = check now
    x:<watch_id>  stop watching
"""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Iterable, Mapping, Sequence

from telegram import InlineKeyboardButton as Button
from telegram import InlineKeyboardMarkup as Markup

from libsg.branches import Branch
from libsg.grouping import SuggestedWork
from libsg.models import Edition
from libsg.watcher import Alert

PAGE_SIZE = 5  # search results per page
EDITIONS_PAGE = 10  # editions per page when picking editions
MAX_TEXT = 3900  # Telegram's limit is 4096; leave room

START = (
    "📚 Find library books faster.\n\n"
    "Search for a book to get started, e.g.\n"
    "<code>/search pride and prejudice</code>\n\n"
    "New here? /help shows how it all works."
)

HELP = (
    "📚 Here's how it works:\n\n"
    "<b>/search</b> <i>title, author or ISBN</i>\n"
    "Find a book and see all its editions in one place. Watch them all at once, "
    "or just untick any you don't want. Then pick the libraries you'd like alerts from "
    "– alerts are on by default.\n\n"
    "<b>/books</b>\n"
    "All your books, and which libraries have them on the shelf.\n"
    "🔔 <i>Alerts on</i>: the shelves get checked every {minutes} minutes, and you'll get a message "
    "as soon as a copy is back at one of your alert libraries.\n"
    "🔕 <i>Alerts off</i>: tap <i>Check now</i> whenever you want to see where it is.\n\n"
    "Print books only, and reservations happen in the NLB app. Happy reading! 📖"
)


def help_text(poll_minutes: float) -> str:
    return HELP.format(minutes=f"{poll_minutes:g}")


def short_author(author: str) -> str:
    """"Austen, Jane, 1775-1817" -> "Jane Austen"; "陈启淦 | Chen, Qigan" -> "陈启淦 (Qigan Chen)"."""
    if " | " in author:
        native, roman = author.split(" | ", 1)
        return f"{native.strip()} ({short_author(roman)})"
    parts = [p.strip() for p in author.split(",")]
    if len(parts) >= 2 and parts[1] and not parts[1][0].isdigit():
        return f"{parts[1]} {parts[0]}"
    return author.strip()


def year_range(editions: Iterable[Edition]) -> str:
    years = sorted({e.year for e in editions if e.year})
    if not years:
        return ""
    return str(years[0]) if years[0] == years[-1] else f"{years[0]}–{years[-1]}"


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def branch_list(codes: Iterable[str], names: Mapping[str, str]) -> str:
    labels = sorted(names.get(c, c) for c in codes)
    return ", ".join(labels) if labels else "any library"


def estimate_text(n_calls: int, spacing: float) -> str:
    seconds = max(5, round(n_calls * (spacing + 1.5)))
    if seconds < 60:
        return f"about {5 * round(seconds / 5)} seconds"
    return f"about {plural(round(seconds / 60), 'minute')}"


def busy_text() -> str:
    return "NLB is busy right now (too many requests). Please try again in a minute."


# --- search ---------------------------------------------------------------


def searching_text(query: str) -> str:
    return f"Searching NLB for “{escape(query)}”…"


def _languages(editions: Sequence[Edition]) -> str:
    langs = sorted({e.language for e in editions if e.language})
    return "" if langs in ([], ["English"]) else ", ".join(langs)


def result_entry(n: int, work: SuggestedWork) -> str:
    eds = work.editions
    author = short_author(work.author)
    head = f"<b>{n}. {escape(work.title)}</b>" + (f" – {escape(author)}" if author else "")
    bits = [plural(len(eds), "edition")]
    if yr := year_range(eds):
        bits[0] += f" ({yr})"
    if langs := _languages(eds):
        bits.append(escape(langs))
    if any(e.any_available for e in eds):
        bits.append("some on shelf now")
    return head + "\n" + " · ".join(bits)


def results_text(query: str, works: Sequence[SuggestedWork], start: int, has_more: bool) -> str:
    page = works[start:start + PAGE_SIZE]
    if start == 0:
        head = f"Top {len(page)} matches for “{escape(query)}” (print books only)"
    else:
        head = f"Matches {start + 1}–{start + len(page)} for “{escape(query)}”"
    body = "\n\n".join(result_entry(start + i + 1, w) for i, w in enumerate(page))
    foot = "Tap a number to pick a book."
    if not has_more:
        foot += " That's all the matches."
    foot += "\nNot what you're looking for? Add the author's name, or search by ISBN."
    return f"{head}\n\n{body}\n\n{foot}"


def results_keyboard(gen: int, start: int, count: int, has_more: bool) -> Markup:
    rows = [[Button(str(start + i + 1), callback_data=f"w:{gen}:{start + i}") for i in range(count)]]
    nav = []
    if start > 0:
        nav.append(Button("‹ Previous", callback_data=f"p:{gen}"))
    if has_more:
        nav.append(Button("More results ▸", callback_data=f"m:{gen}"))
    if nav:
        rows.append(nav)
    return Markup(rows)


# --- draft: editions ------------------------------------------------------


def _edition_entry(n: int, e: Edition, selected: bool, detail: bool) -> str:
    line = f"{'✅' if selected else '⬜'} <b>{n}.</b> {escape(e.describe())}"
    if not detail:
        return line
    extra = []
    if e.isbn:
        extra.append(f"ISBN {e.isbn}")
    # Each edition links to its own record; NLB's site decides which page that opens.
    extra.append(f'<a href="{escape(e.catalogue_url)}">View in catalogue</a>')
    if e.any_available:
        extra.append("on shelf somewhere")
    return line + ("\n      " + " · ".join(extra) if extra else "")


def editions_text(
    title: str, author: str, editions: Sequence[Edition], selected: set[int], start: int = 0,
) -> str:
    """One page (EDITIONS_PAGE) of the edition list, numbered across pages."""
    author = short_author(author)
    head = f"<b>{escape(title)}</b>" + (f" – {escape(author)}" if author else "")
    head += (
        "\n\nTicked editions will be watched. Untick any you don't want (e.g. other languages), then tap Next."
        "\nNot sure which is which? Search the ISBN in the NLB app, or tap <i>View in catalogue</i> "
        "(NLB sometimes shows several editions on one page)."
    )
    page = editions[start:start + EDITIONS_PAGE]
    if len(editions) > EDITIONS_PAGE:
        head += (
            f"\n\n<i>Editions {start + 1}–{start + len(page)} of {len(editions)}"
            f" · {len(selected)} ticked in total</i>"
        )
    for detail in (True, False):  # safety net for unusually long entries
        body = "\n".join(
            _edition_entry(start + i + 1, e, e.brn in selected, detail) for i, e in enumerate(page)
        )
        text = f"{head}\n\n{body}"
        if len(text) <= MAX_TEXT:
            return text
    return text[:MAX_TEXT]


def editions_keyboard(
    editions: Sequence[Edition], selected: set[int], back: tuple[str, str] | None = None,
    start: int = 0, per_row: int = 5,
) -> Markup:
    """Tick buttons for the current page; back: (label, callback_data) e.g. ("‹ Back to results", "r:3")."""
    page = editions[start:start + EDITIONS_PAGE]
    buttons = [
        Button(f"{'✅' if e.brn in selected else '⬜'} {start + i + 1}", callback_data=f"e:{e.brn}")
        for i, e in enumerate(page)
    ]
    rows = [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]
    nav = []
    if start > 0:
        nav.append(Button("‹ Previous editions", callback_data=f"ep:{max(0, start - EDITIONS_PAGE)}"))
    if start + EDITIONS_PAGE < len(editions):
        nav.append(Button("More editions ▸", callback_data=f"ep:{start + EDITIONS_PAGE}"))
    if nav:
        rows.append(nav)
    if len(editions) > 1:
        rows.append([Button("Select all", callback_data="ea"), Button("Clear", callback_data="ec")])
    nav = [Button(back[0], callback_data=back[1])] if back else []
    rows.append(nav + [Button("Next →", callback_data="en")])
    return Markup(rows)


# --- draft: branches ------------------------------------------------------


def branches_text(title: str, has_branches: bool = True, notify: bool = True) -> str:
    if not notify:
        return (
            f"<b>{escape(title)}</b>\n🔕 <i>Alerts off</i>: no messages, so no libraries to pick – "
            "/books shows every library. The book isn't checked automatically; "
            "use <i>Check now</i> in /books whenever you like."
            "\n\nTap 🔕 to turn alerts back on, or Save watch."
        )
    text = (
        f"<b>{escape(title)}</b>\n🔔 <i>Alerts on</i>: which libraries should alert you? Tap as many as you like, "
        "or keep <i>Any library</i> on. You'll get a message when a copy comes back on the shelf there, "
        "and /books shows every library either way."
        "\n\nRather not get messages? Tap 🔔 to turn alerts off. Then tap Save watch."
    )
    if not has_branches:
        text += "\n\n(The library list couldn't be loaded from NLB just now, so only <i>Any library</i> is available.)"
    return text


def notify_label(notify: bool) -> str:
    return "🔔 Alerts on" if notify else "🔕 Alerts off"


def branches_keyboard(
    branches: Sequence[Branch], selected: set[str], notify: bool = True, per_row: int = 3,
) -> Markup:
    """Alerts switch on top; library buttons only when alerts are on (they only choose where alerts come from)."""
    rows = [[Button(f"{notify_label(notify)} (tap to change)", callback_data="bn")]]
    if notify:
        rows.append([Button(f"{'✅' if not selected else '⬜'} Any library", callback_data="b:*")])
        buttons = [
            Button(f"{'✅ ' if b.code in selected else ''}{b.short_name}", callback_data=f"b:{b.code}")
            for b in branches
        ]
        rows += [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]
    rows.append([Button("‹ Back to editions", callback_data="eb"), Button("Save watch", callback_data="bs")])
    return Markup(rows)


PROGRESS_EVERY = 3  # editions between progress updates on long checks


def progress_line(done: int, total: int) -> str:
    return f"\n{done} of {total} checked…" if 0 < done < total else ""


def checking_text(title: str, n_editions: int, estimate: str, done: int = 0) -> str:
    return (
        f"Saved <b>{escape(title)}</b>.\n"
        f"Checking the shelves for {plural(n_editions, 'edition')}… this takes {estimate}."
        "\nThe result will also be in /books."
        + progress_line(done, n_editions)
    )


def _places(counts: Mapping[str, int], names: Mapping[str, str]) -> str:
    """"Bedok (2), Clementi (1)" -- alphabetical so people can spot their library."""
    items = sorted(counts.items(), key=lambda kv: names.get(kv[0], kv[0]).lower())
    return ", ".join(f"{escape(names.get(c, c))} ({n})" for c, n in items)


def _library_names(codes: Iterable[str], names: Mapping[str, str], closed: set[str] = frozenset()) -> str:
    return ", ".join(
        escape(names.get(c, c)) + (" (closed)" if c in closed else "")
        for c in sorted(codes, key=lambda c: names.get(c, c).lower())
    )


def shelf_lines(
    on_shelf: Mapping[str, int] | None,
    alert_branches: Iterable[str],
    names: Mapping[str, str],
    notify: bool = True,
) -> list[str]:
    """Where a book is on the shelf, across every library. on_shelf: branch -> copies, None = not checked yet.

    With alerts on for specific libraries, those come first and the rest follow as "Also on the shelf".
    """
    if on_shelf is None:
        return ["On the shelf: not checked yet"]
    available = {c: n for c, n in on_shelf.items() if n}
    alert_branches = set(alert_branches)
    if not (notify and alert_branches):
        if not available:
            return ["On the shelf: none right now"]
        return [f"📗 On the shelf at {plural(len(available), 'library', 'libraries')}: {_places(available, names)}"]
    mine = {c: n for c, n in available.items() if c in alert_branches}
    others = {c: n for c, n in available.items() if c not in alert_branches}
    lines = [
        f"🔔 On the shelf at your alert libraries: {_places(mine, names)}" if mine
        else f"🔔 Not on the shelf at {_library_names(alert_branches, names)} right now"
    ]
    if others:
        lines.append(f"📗 Also on the shelf at {plural(len(others), 'library', 'libraries')}: {_places(others, names)}")
    return lines


def watch_saved_text(
    title: str,
    branches: set[str],
    on_shelf: Mapping[str, int] | None,
    names: Mapping[str, str],
    replaced: bool = False,
    notify: bool = True,
) -> str:
    """branches: alert libraries (empty = any). on_shelf: branch -> copies at every library, None if unknown."""
    head = "Updated your watch" if replaced else "Watching"
    lines = [f"{head}: <b>{escape(title)}</b>"]
    if on_shelf is None:
        lines.append("Couldn't check the shelves just now (NLB was busy) – it'll be checked in the next round.")
    else:
        lines += shelf_lines(on_shelf, branches, names, notify)
    if notify:
        where = _library_names(branches, names) if branches else "any library"
        lines.append(f"\nYou'll get a message when a copy comes back at {where}.")
    else:
        lines.append("\n🔕 Alerts are off: this book isn't checked automatically. Use Check now in /books to update it.")
    return "\n".join(lines)


# --- watches --------------------------------------------------------------


def ago(iso: str | None, now: datetime | None = None) -> str:
    """ISO timestamp -> "just now" / "12 min ago" / "3 h ago" / "2 days ago"."""
    if not iso:
        return ""
    then = datetime.fromisoformat(iso)
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    minutes = int(((now or datetime.now(timezone.utc)) - then).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} h ago"
    return f"{minutes // (24 * 60)} days ago"


def watch_text(
    title: str,
    author: str,
    n_editions: int,
    branches: Iterable[str],
    names: Mapping[str, str],
    closed: set[str] = frozenset(),
    on_shelf: Mapping[str, int] | None = None,
    checked: str = "",
    notify: bool = True,
) -> str:
    """One book for /books. branches: alert libraries (empty = any).
    on_shelf: branch -> copies at every library; None = not checked yet."""
    author = short_author(author)
    branches = list(branches)
    if not notify:
        alerts = "🔕 Alerts off – not checked automatically, tap Check now to update"
    elif branches:
        alerts = f"🔔 Alerts at {_library_names(branches, names, closed)}"
    else:
        alerts = "🔔 Alerts at any library"
    lines = [
        f"<b>{escape(title)}</b>" + (f" – {escape(author)}" if author else ""),
        f"Watching {plural(n_editions, 'edition')}",
        alerts,
        *shelf_lines(on_shelf, branches, names, notify),
    ]
    if checked:
        lines.append(f"<i>Last checked {checked}</i>")
    return "\n".join(lines)


def watch_keyboard(watch_id: int, notify: bool = True) -> Markup:
    toggle = "🔕 Turn alerts off" if notify else "🔔 Turn alerts on"
    return Markup([
        [Button("🔄 Check now", callback_data=f"c:{watch_id}"), Button(toggle, callback_data=f"n:{watch_id}")],
        [Button("Edit", callback_data=f"ed:{watch_id}"), Button("Stop watching", callback_data=f"x:{watch_id}")],
    ])


def alerts_on_checking_text(title: str, n_editions: int, estimate: str, done: int = 0) -> str:
    return (
        f"<b>{escape(title)}</b>\n🔔 Turning alerts on – checking the shelves for "
        f"{plural(n_editions, 'edition')} first, so only copies returned from now on trigger an alert… {estimate}."
        + progress_line(done, n_editions)
    )


def checking_now_text(title: str, n_editions: int, estimate: str, done: int = 0) -> str:
    return (
        f"<b>{escape(title)}</b>\nChecking the shelves for {plural(n_editions, 'edition')}… {estimate}."
        + progress_line(done, n_editions)
    )


def check_paused_text(wait_minutes: int) -> str:
    return (
        f"Too many checks in a short time. Check now is paused for {plural(wait_minutes, 'minute')}. "
        "Regular checks continue as normal."
    )


# --- alerts ---------------------------------------------------------------


def alert_text(work_title: str, alerts: Sequence[Alert], labels: Mapping[int, str]) -> str:
    """labels: brn -> edition description. Alerts all belong to one user + work."""
    lines = [f"📗 <b>{escape(work_title)}</b> is back on the shelf!"]
    for a in sorted(alerts, key=lambda a: (a.branch_name, a.brn)):
        lines.append(f"• <b>{escape(a.branch_name)}</b>: {plural(a.available, 'copy', 'copies')}")
        if label := labels.get(a.brn):
            lines.append(f"   {escape(label)}")
    return "\n".join(lines)
