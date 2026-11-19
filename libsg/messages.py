"""Bot message text and inline keyboards. Pure functions -- no I/O.

All text is Telegram HTML (parse_mode="HTML"); user/API strings are escaped.

Callback data (Telegram limit: 64 bytes):
    w:<gen>:<i>   pick result i (0-based, across pages) from search generation gen
    m:<gen>       more results;  p:<gen> = previous results;  r:<gen> = back to results
    e:<brn>       toggle an edition;  ea = select all, ec = clear, en = next;  ep:<start> = page of editions
    b:<CODE>      toggle a branch;  b:* = any library;  bs = save;  eb = back to editions
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
    "Get a message when an NLB library book is back on the shelf at the libraries you choose.\n\n"
    "Start with <code>/search</code> and a title, e.g. <code>/search pride and prejudice</code>\n"
    "/help – how it works"
)

HELP = (
    "📚 Get a message when an NLB library book is back on the shelf.\n\n"
    "<b>/search</b> <i>title, author or ISBN</i>\n"
    "Find a book and start watching it:\n"
    "1. Pick it from the top 5 matches (<i>More results</i> for more).\n"
    "2. Tick the editions to watch – untick other languages or ones you don't want. "
    "Not sure which is which? Search the ISBN in the NLB app, or tap <i>View in catalogue</i>.\n"
    "3. Choose the libraries you can get to, or keep <i>Any library</i>.\n"
    "4. Save – you'll see where it's on the shelf right now.\n\n"
    "<b>/watches</b>\n"
    "All the books being watched, with where each one is on the shelf as of the last check. "
    "Each book has buttons to:\n"
    "• <i>Check now</i> – check the shelves right away\n"
    "• <i>Edit</i> – change its editions or libraries\n"
    "• <i>Stop watching</i>\n\n"
    "<b>/help</b> – this guide\n\n"
    "<b>How alerts work</b>\n"
    "The shelves get checked every {minutes} minutes. When a copy comes back on the shelf "
    "at one of your libraries, a message arrives – so don't wait too long, it can get borrowed again!\n\n"
    "<b>Good to know</b>\n"
    "• Print books only – no ebooks or audiobooks.\n"
    "• Libraries closed for renovation or relocation aren't listed.\n"
    "• Reservations happen in the NLB app, not here."
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


def branches_text(title: str, has_branches: bool = True) -> str:
    text = (
        f"<b>{escape(title)}</b>\nWhich libraries can you get to? "
        "Tap as many as you like, or leave <i>Any library</i> on. Then tap Save watch."
    )
    if not has_branches:
        text += "\n\n(The library list couldn't be loaded from NLB just now, so only <i>Any library</i> is available.)"
    return text


def branches_keyboard(branches: Sequence[Branch], selected: set[str], per_row: int = 3) -> Markup:
    rows = [[Button(f"{'✅' if not selected else '⬜'} Any library", callback_data="b:*")]]
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
        "\nThe result will also be in /watches."
        + progress_line(done, n_editions)
    )


def watch_saved_text(
    title: str,
    branches: set[str],
    on_shelf: Mapping[str, int] | None,
    names: Mapping[str, str],
    replaced: bool = False,
) -> str:
    """on_shelf: branch -> copies on shelf now (within the watched branches), None if unknown."""
    head = "Updated your watch" if replaced else "Watching"
    text = f"{head}: <b>{escape(title)}</b> at {escape(branch_list(branches, names))}."
    if on_shelf is None:
        text += "\nCouldn't check the shelves just now (NLB was busy) – it'll be checked in the next round."
    elif on_shelf:
        where = ", ".join(
            f"{escape(names.get(b, b))} ({n})" for b, n in sorted(on_shelf.items(), key=lambda kv: names.get(kv[0], kv[0]))
        )
        text += f"\nOn the shelf right now: {where}."
    else:
        text += "\nNo copies on the shelf there right now."
    return text + "\nYou'll get a message when a copy comes back on the shelf."


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
) -> str:
    """One watched book for /watches. on_shelf: branch -> copies (watched branches only); None = not checked yet."""
    author = short_author(author)
    branches = list(branches)
    if branches:
        where = ", ".join(
            escape(names.get(c, c)) + (" (closed)" if c in closed else "")
            for c in sorted(branches, key=lambda c: names.get(c, c))
        )
    else:
        where = "any library"
    lines = [
        f"<b>{escape(title)}</b>" + (f" – {escape(author)}" if author else ""),
        f"Watching {plural(n_editions, 'edition')} at {where}",
    ]
    if on_shelf is None:
        lines.append("On the shelf: not checked yet")
    elif on_shelf:
        # Every library, alphabetical so people can spot theirs; all ~22 fit comfortably in one message.
        places = sorted(on_shelf.items(), key=lambda kv: names.get(kv[0], kv[0]).lower())
        shown = ", ".join(f"{escape(names.get(c, c))} ({n})" for c, n in places)
        lines.append(f"📗 On the shelf at {plural(len(places), 'library', 'libraries')}: {shown}")
    else:
        lines.append("On the shelf: none right now")
    if checked:
        lines.append(f"<i>Last checked {checked}</i>")
    return "\n".join(lines)


def watch_keyboard(watch_id: int) -> Markup:
    return Markup([
        [Button("🔄 Check now", callback_data=f"c:{watch_id}")],
        [Button("Edit", callback_data=f"ed:{watch_id}"), Button("Stop watching", callback_data=f"x:{watch_id}")],
    ])


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
