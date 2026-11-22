# NLB Watch Bot
Telegram bot for Singapore NLB library books: search, see availability across editions, My books list, alerts.

## Stack
- Python 3.14, python-telegram-bot (with JobQueue), httpx, SQLite
- NLB APIs, specs in docs/ (gitignored). Use them as the source of truth for fields:
  - Catalogue API v2 (`nlb-catalogue-openapi.json`): SearchTitles, GetTitles, GetAvailabilityInfo
  - Library API v1 (`nlb-library-openapi.json`): GetBranches
- Secrets come from .env only. Never hardcode or print keys. Telegram request URLs contain the bot token, so keep httpx logging at WARNING.

## Core concepts
- Edition = one BRN. Work = a group of editions the user confirmed as "the same book" (starts as one NLB group).
- Flow: /search → tap a result → availability of its print editions (checks reused under 5 min) → ⭐ Save to My books
  → alerts on/off + library picker (only open lending libraries holding a copy of the ticked editions).
- Watch = a saved book in My books (/books): (user, work, set of branches, notify). In code and the DB it's still a
  "watch"; users never see that word. Books with alerts off stay saved but aren't polled.
- Alert only when an edition goes from not-available to available at a watched branch.
- Empty branch set = any branch. Works belong to one user (never shared).
- Search only runs from /search (bare /search waits for the next text). Other text gets a hint and no NLB call.
- A work stores all candidate editions; `watched=0` marks ones the user unticked, so Edit can offer them again.

## Layout (libsg/)
- `nlb_client.py` – sync HTTP client for both APIs (auth headers, 429 retry). No bot code here.
- `models.py` – Edition/Copy parsed from API responses. `grouping.py` – suggest works (one per NLB group; we tried merging groups, but it mixed in other languages).
- `branches.py` – lending libraries from GetBranches. `db.py` – SQLite store (works, watches, snapshots, branches).
- `watcher.py` – pure alert rule. `poller.py` – one polling round. `ratelimit.py` – spam guard for Check now and opening results.
- `messages.py` – all bot text and keyboards (pure functions). `bot.py` – handlers, jobs, entry point.
- `deploy/` – `push.sh` (rsync from Mac to server, then runs setup) and `setup.sh` (on server: uv Python 3.14, systemd unit `libsg-bot`). Server runs Oracle Cloud Always Free, Ubuntu, user `ubuntu`.

## NLB data facts (verified against real responses)
- Available copy = `status.code == "In"` and not `nonCirculatingFlag`. Other codes seen: "Out" (on loan), "In-Transit".
- SearchTitles returns placeholder records with `brn: 0` and no data – skip them.
- Digital editions have a `digitalId` / source "Overdrive" and no branch copies; only physical editions are polled.
- A branch is open if GetBranches returns a non-empty `schedules`. Closed branches still have `isActive: true`.
  Only branch types "PL"/"RL" lend books. Branch codes don't always follow names (Tampines = TRL, Toa Payoh = TPPL).
- Catalogue link: `https://catalogue.nlb.gov.sg/search/card?recordId=<BRN>`. NLB may show several editions on one page.
- NLB rate-limits bursts with 429 "API calls quota exceeded"; the quota is undocumented. 429s carry a `Retry-After`
  (seen: about 1 s), which the client honours and logs.
- All NLB calls go through one lock, and the client keeps at least 1.5 s between any two requests (`NLB_MIN_GAP`),
  including availability pages. The poll also waits `POLL_SPACING_SECONDS` (10 s) between editions.

## Conventions
- Keep the NLB client in its own module, separate from bot handlers.
- Tests use saved JSON fixtures in tests/fixtures/, not live API calls. Save new live responses there.
- Run tests: `.venv/bin/pytest -q`.
- Small commits, one per working step. Don't commit unless asked; list the `git add`/`git commit` commands instead.
- Restarting the running bot kills checks in progress: check it's idle first and say when it was restarted.
