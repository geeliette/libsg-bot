"""Bot message text and inline keyboards. Pure functions -- no I/O.

All text is Telegram HTML (parse_mode="HTML"); user/API strings are escaped.

Callback data (Telegram limit: 64 bytes):
    w:<gen>:<i>   pick suggested work i from search generation gen
    e:<brn>       toggle an edition in the draft
    en            editions done -> branch picker
    b:<CODE>      toggle a branch;  b:*  = any library
    bs            save the watch
    x:<watch_id>  stop watching
"""

from __future__ import annotations

from html import escape
from typing import Iterable, Mapping, Sequence

from telegram import InlineKeyboardButton as Button
from telegram import InlineKeyboardMarkup as Markup

from libsg.branches import BRANCHES, branch_name, short_name
from libsg.grouping import SuggestedWork
from libsg.models import Edition
from libsg.watcher import Alert

HELP = (
    "I watch NLB library books and message you when a copy is back on the shelf.\n\n"
    "/search <i>words</i> – find a book (title, author, ISBN)\n"
    "/watches – your watched books\n"
    "/help – this message"
)


def short_author(author: str) -> str:
    """"Austen, Jane, 1775-1817" -> "Jane Austen"; anything else unchanged."""
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


def branch_list(codes: Iterable[str]) -> str:
    codes = sorted(codes, key=lambda c: branch_name(c).lower())
    return ", ".join(short_name(c) for c in codes) if codes else "any library"


# --- search ---------------------------------------------------------------


def work_summary(work: SuggestedWork) -> str:
    eds = work.editions
    bits = [plural(len(eds), "print edition")]
    if yr := year_range(eds):
        bits.append(f"({yr})")
    on_shelf = sum(e.any_available for e in eds)
    line2 = " ".join(bits)
    if on_shelf:
        line2 += f" · {on_shelf} with copies on shelf somewhere"
    author = short_author(work.author)
    return f"<b>{escape(work.title)}</b>" + (f"\n{escape(author)}" if author else "") + f"\n{line2}"


def work_keyboard(gen: int, i: int) -> Markup:
    return Markup([[Button("Watch this book", callback_data=f"w:{gen}:{i}")]])


# --- draft: editions ------------------------------------------------------


def edition_label(e: Edition, selected: bool) -> str:
    return f"{'✅' if selected else '⬜'} {e.year or '????'} · {e.format_name or 'Item'} · {e.brn}"


def editions_text(title: str) -> str:
    return (
        f"<b>{escape(title)}</b>\nThese editions look like the same book. "
        "Untick any that aren't, then tap Next."
    )


def editions_keyboard(editions: Sequence[Edition], selected: set[int]) -> Markup:
    rows = [[Button(edition_label(e, e.brn in selected), callback_data=f"e:{e.brn}")] for e in editions]
    rows.append([Button("Next →", callback_data="en")])
    return Markup(rows)


# --- draft: branches ------------------------------------------------------


def branches_text(title: str) -> str:
    return (
        f"<b>{escape(title)}</b>\nWhich libraries should I watch? "
        "Pick any number, or leave <i>Any library</i> on."
    )


def branches_keyboard(selected: set[str], per_row: int = 3) -> Markup:
    rows = [[Button(f"{'✅' if not selected else '⬜'} Any library", callback_data="b:*")]]
    row: list[Button] = []
    for code in BRANCHES:
        row.append(Button(f"{'✅ ' if code in selected else ''}{short_name(code)}", callback_data=f"b:{code}"))
        if len(row) == per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([Button("Save watch", callback_data="bs")])
    return Markup(rows)


def watch_saved_text(
    title: str,
    branches: set[str],
    on_shelf: Mapping[str, int] | None,
    replaced: bool = False,
) -> str:
    """on_shelf: branch -> copies on shelf now (within the watched branches), None if unknown."""
    head = "Updated" if replaced else "Watching"
    text = f"{head}: <b>{escape(title)}</b> at {escape(branch_list(branches))}."
    if on_shelf is None:
        text += "\nI couldn't check availability just now; I'll check in the next round."
    elif on_shelf:
        where = ", ".join(f"{escape(short_name(b))} ({n})" for b, n in sorted(on_shelf.items(), key=lambda kv: branch_name(kv[0])))
        text += f"\nOn shelf right now: {where}."
    else:
        text += "\nNo copies on the shelf right now."
    return text + "\nI'll message you when a copy comes back."


# --- watches --------------------------------------------------------------


def watch_line(title: str, author: str, n_editions: int, branches: Iterable[str]) -> str:
    author = short_author(author)
    return (
        f"<b>{escape(title)}</b>" + (f" – {escape(author)}" if author else "")
        + f"\n{plural(n_editions, 'edition')} · {escape(branch_list(branches))}"
    )


def watch_keyboard(watch_id: int) -> Markup:
    return Markup([[Button("Stop watching", callback_data=f"x:{watch_id}")]])


# --- alerts ---------------------------------------------------------------


def alert_text(work_title: str, alerts: Sequence[Alert], editions: Mapping[int, tuple[int | None, str]]) -> str:
    """editions: brn -> (year, format_name). Alerts all belong to one user + work."""
    lines = [f"📗 <b>{escape(work_title)}</b> is back on the shelf"]
    for a in sorted(alerts, key=lambda a: (a.branch_name, a.brn)):
        year, fmt = editions.get(a.brn, (None, ""))
        ed = " ".join(str(x) for x in (year, fmt) if x)
        lines.append(
            f"• {escape(branch_name(a.branch_code, a.branch_name))}: {plural(a.available, 'copy', 'copies')}"
            + (f" – {escape(ed)}" if ed else "") + f" (BRN {a.brn})"
        )
    return "\n".join(lines)
