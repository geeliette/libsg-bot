"""Manual CLI for poking the NLB API.

    python try.py search "emma jane austen"
    python try.py avail <BRN>
    python try.py titles --author "jane austen"

Every raw response is saved to tests/fixtures/.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from libsg.nlb_client import NLBClient, NLBError

FIXTURES = Path(__file__).parent / "tests" / "fixtures"
ENDPOINT_NAMES = {
    "SearchTitles": "search_titles",
    "GetTitles": "get_titles",
    "GetAvailabilityInfo": "get_availability",
}


def slug(params: dict[str, Any]) -> str:
    parts = []
    for k, v in params.items():
        if k in ("Limit",) or (k == "Offset" and not v):
            continue
        val = ",".join(map(str, v)) if isinstance(v, list) else str(v)
        parts.append(val if k == "Keywords" else f"{k}-{val}")
    s = re.sub(r"[^a-z0-9]+", "-", "_".join(parts).lower()).strip("-")
    return s[:80] or "all"


def save_fixture(endpoint: str, params: dict[str, Any], data: Any) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    path = FIXTURES / f"{ENDPOINT_NAMES.get(endpoint, endpoint)}_{slug(params)}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"[saved {path.relative_to(Path(__file__).parent)}]", file=sys.stderr)


def clean_title(title: str | None) -> str:
    # NLB titles look like "Emma / Jane Austen ; edited by ..." -- drop the responsibility part.
    return (title or "").split(" / ")[0].strip() or "?"


def year(publish_date: str | None) -> str:
    m = re.search(r"\d{4}", publish_date or "")
    return m.group(0) if m else "----"


def cut(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def cmd_search(client: NLBClient, args: argparse.Namespace) -> None:
    data = client.search_titles(args.keywords, limit=args.limit)
    print(f"{data.get('totalRecords', 0)} works, showing {data.get('count', 0)}")
    for t in data.get("titles") or []:
        title, author = clean_title(t.get("title")), t.get("author") or ""
        for r in t.get("records") or []:
            fmt = (r.get("format") or {}).get("name", "")
            avail = "*" if r.get("availability") else " "
            print(f"{r.get('brn'):>10} {avail} {cut(title, 40):<40}  {cut(author, 25):<25}  {cut(fmt, 12):<12}  {year(r.get('publishDate'))}")
    print("(* = at least one copy available)")


def cmd_titles(client: NLBClient, args: argparse.Namespace) -> None:
    params = {k: v for k, v in (("Keywords", args.keywords), ("Title", args.title), ("Author", args.author), ("ISBN", args.isbn)) if v}
    data = client.get_titles(limit=args.limit, **params)
    print(f"{data.get('totalRecords', 0)} titles, showing {data.get('count', 0)}")
    for t in data.get("titles") or []:
        fmt = (t.get("format") or {}).get("name", "")
        print(f"{t.get('brn'):>10}   {cut(clean_title(t.get('title')), 40):<40}  {cut(t.get('author') or '', 25):<25}  {cut(fmt, 12):<12}  {year(t.get('publishDate'))}")


def is_available(item: dict) -> bool:
    # status.code seen in fixtures: "In" (On Shelf), "Out" (On Loan), "In-Transit".
    # Reference-only copies are also "In" but can't be borrowed, so exclude them.
    return (item.get("status") or {}).get("code") == "In" and not item.get("nonCirculatingFlag")


def cmd_avail(client: NLBClient, args: argparse.Namespace) -> None:
    items = client.get_all_availability(args.brn)
    if not items:
        print(f"No items found for BRN {args.brn}")
        return
    by_branch: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        by_branch[(it.get("location") or {}).get("name") or "?"].append(it)
    total_avail = sum(is_available(i) for i in items)
    print(f"BRN {args.brn}: {total_avail}/{len(items)} available across {len(by_branch)} locations\n")
    for branch in sorted(by_branch, key=lambda b: (-sum(map(is_available, by_branch[b])), b)):
        its = by_branch[branch]
        print(f"{branch}: {sum(map(is_available, its))} available / {len(its)} total")
        for it in its:
            status = (it.get("status") or {}).get("name") or "?"
            tx = it.get("transactionStatus") or {}
            date = (tx.get("date") or "")[:10]
            if it.get("status", {}).get("code") == "Out" and date:
                extra = f" (due {date})"
            elif tx.get("name") and tx.get("name") not in (status, "Available"):
                extra = f" [{tx['name']}]"
            else:
                extra = ""
            ref = " (reference only)" if it.get("nonCirculatingFlag") else ""
            print(f"    {it.get('formattedCallNumber') or it.get('callNumber') or '?':<30} {status}{extra}{ref}")


def main() -> int:
    p = argparse.ArgumentParser(description="Poke the NLB Catalogue API")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="SearchTitles by keywords")
    s.add_argument("keywords")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(fn=cmd_search)
    a = sub.add_parser("avail", help="GetAvailabilityInfo for a BRN")
    a.add_argument("brn", type=int)
    a.set_defaults(fn=cmd_avail)
    t = sub.add_parser("titles", help="GetTitles by keywords/title/author/isbn")
    t.add_argument("--keywords")
    t.add_argument("--title")
    t.add_argument("--author")
    t.add_argument("--isbn")
    t.add_argument("--limit", type=int, default=20)
    t.set_defaults(fn=cmd_titles)
    args = p.parse_args()

    try:
        with NLBClient(on_response=save_fixture) as client:
            args.fn(client, args)
    except NLBError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
