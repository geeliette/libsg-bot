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

from libsg.models import Copy, Edition, parse_copies, parse_search, parse_titles
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


def cut(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def fmt_year(y: int | None) -> str:
    return str(y) if y else "----"


def edition_line(e: Edition) -> str:
    avail = "*" if e.any_available else " "
    return f"{e.brn:>10} {avail} {cut(e.title, 40):<40}  {cut(e.author, 25):<25}  {cut(e.format_name, 12):<12}  {fmt_year(e.year)}"


def cmd_search(client: NLBClient, args: argparse.Namespace) -> None:
    data = client.search_titles(args.keywords, limit=args.limit)
    print(f"{data.get('totalRecords', 0)} works, showing {data.get('count', 0)}")
    for g in parse_search(data):
        for e in g.editions:
            print(edition_line(e))
    print("(* = at least one copy available)")


def cmd_titles(client: NLBClient, args: argparse.Namespace) -> None:
    params = {k: v for k, v in (("Keywords", args.keywords), ("Title", args.title), ("Author", args.author), ("ISBN", args.isbn)) if v}
    data = client.get_titles(limit=args.limit, **params)
    print(f"{data.get('totalRecords', 0)} titles, showing {data.get('count', 0)}")
    for e in parse_titles(data):
        print(edition_line(e))


def cmd_avail(client: NLBClient, args: argparse.Namespace) -> None:
    copies = parse_copies(client.get_all_availability(args.brn))
    if not copies:
        print(f"No items found for BRN {args.brn}")
        return
    by_branch: dict[str, list[Copy]] = defaultdict(list)
    for c in copies:
        by_branch[c.branch_name].append(c)
    total_avail = sum(c.available for c in copies)
    print(f"BRN {args.brn}: {total_avail}/{len(copies)} available across {len(by_branch)} locations\n")
    for branch in sorted(by_branch, key=lambda b: (-sum(c.available for c in by_branch[b]), b)):
        cs = by_branch[branch]
        print(f"{branch}: {sum(c.available for c in cs)} available / {len(cs)} total")
        for c in cs:
            extra = f" (due {c.due_date})" if c.due_date else ""
            ref = " (reference only)" if c.reference_only else ""
            print(f"    {c.call_number or '?':<30} {c.status_name}{extra}{ref}")


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
