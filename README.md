# NLB Watch Bot

[![tests](https://github.com/geeliette/libsg-bot/actions/workflows/tests.yml/badge.svg)](https://github.com/geeliette/libsg-bot/actions/workflows/tests.yml)

A Telegram bot for Singapore's public libraries (NLB). Search for a book, see at once which libraries have it on the shelf across all its editions, save it to a reading list, and get a message when a copy comes back to a library you chose.

NLB's own app lists each edition separately and has no shelf alerts. If a book is popular, you either check every day or reserve it and wait. This bot does the checking for you.

**Try it on Telegram: [@libsg_bot](https://t.me/libsg_bot)**

## What it does

- **Search** by title, author or ISBN with `/search`. Results are print books only, five at a time.
- **Availability at once.** Tap a result to see the libraries with a copy on the shelf (with counts) and those whose copies are all on loan, across every edition of that book.
- **My books** (`/books`): a reading list showing where each book was on the shelf at the last check, with *Check now*, *Edit* and *Remove*.
- **Alerts**, which you can turn on or off per book. You get a message when a copy goes from not available to on the shelf at one of your libraries. The library picker only lists open libraries that actually hold a copy.

## Tech stack

| | |
|---|---|
| Language | Python 3.14 |
| Bot framework | [python-telegram-bot](https://python-telegram-bot.org/) (async handlers, JobQueue for scheduled checks) |
| HTTP | httpx |
| Storage | SQLite |
| Data | NLB Open Web Service: Catalogue API v2 (search, availability) and Library API v1 (branches, opening hours) |
| Hosting | Oracle Cloud Always Free (Ubuntu VM, systemd service), deployed with rsync over SSH |
| Tests | pytest, run against recorded API responses |

## How it works

```
/search ──▶ SearchTitles ──▶ results (one per NLB edition group)
                                  │ tap
                                  ▼
                 GetAvailabilityInfo per print edition ──▶ on shelf / on loan, by library
                                  │ ⭐ Save
                                  ▼
                 My books ──▶ alerts on? ──▶ checked every 30 min while libraries are open
                                                   │ not available ──▶ on shelf
                                                   ▼
                                              Telegram message
```

Each check stores a snapshot of available copies per edition and library. An alert fires only on the change from "none available" to "some available" at a library the user picked, so a book sitting on the shelf doesn't send repeat messages.

## Design decisions

**One shared rate limit.** NLB rate-limits per API key, so every user of the bot shares one undocumented quota. Bursts return HTTP 429. All NLB calls go through a single lock, and the client keeps at least 1.5 s between any two requests, whoever makes them: a user's search, another user's *Check now* or the background poll. That gap came from the production logs, where a poll call and a user call went out 0.15 s apart and both got 429s. The client also honours NLB's `Retry-After` header. A poll round that gets rate-limited stops early, and the editions it skipped go first next round, so none of them miss out for long.

**Poll only when it matters.** Books are only shelved while libraries are open. The bot reads opening hours and public-holiday closures from the Library API and pauses polling overnight (with 30 minutes' grace after closing for books shelved at closing time). That saves a large share of the API quota. *Check now* still works at any hour.

**Spending calls only on real requests.** Checks are reused for a few minutes, so opening the same book twice costs nothing. A per-user spam guard pauses *Check now* briefly if someone keeps pressing it. Search only runs from `/search`, so stray messages never call NLB.

**Libraries come from the API.** Branches close for renovation or relocation without disappearing from the API: they stay `isActive` but lose their schedule. The bot treats a branch with no opening hours as closed and never shows it, and it refreshes the list daily instead of hard-coding it. Only lending libraries count; reference floors and the legal-deposit and repository collections (PublicationSG, for example) are left out.

**Pure logic, thin handlers.** The alert rule, polling round, edition grouping and all message text are plain functions with no Telegram or network code. That makes them easy to test against recorded NLB responses.

## Project layout

```
libsg/
  nlb_client.py   NLB Catalogue + Library API client (auth, request gap, 429 retry)
  models.py       Editions and copies parsed from API responses
  grouping.py     One suggested book per NLB group of editions
  branches.py     Lending libraries, open/closed, opening hours
  db.py           SQLite storage (works, watches, snapshots, branches)
  watcher.py      The alert rule
  poller.py       One round of availability checks
  ratelimit.py    Reuse of recent checks, spam guard for Check now
  messages.py     All bot text and buttons
  bot.py          Telegram handlers, scheduled jobs, entry point
try.py            CLI for calling the NLB API directly
deploy/           Deploy scripts and guide (see deploy/README.md)
tests/            pytest suite; fixtures/ holds recorded NLB responses
```

## Running it yourself

You need Python 3.14, an NLB API key and app code (from NLB's Open Web Service, openweb.nlb.gov.sg), and a Telegram bot token from [@BotFather](https://t.me/BotFather).

```sh
python3.14 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

Create `.env` in the project root (it's gitignored):

```
NLB_API_KEY=...
NLB_APP_CODE=...
TELEGRAM_BOT_TOKEN=...
```

Then run the bot or the tests:

```sh
.venv/bin/python -m libsg.bot
.venv/bin/pytest -q      # no network calls or credentials needed
```

| Optional setting (`.env`) | Default | Meaning |
|---|---|---|
| `DB_PATH` | `libsg.db` | SQLite database file |
| `POLL_MINUTES` | `30` | Minutes between regular checks |
| `POLL_SPACING_SECONDS` | `10` | Extra pause between editions during regular checks |

Telegram allows one running copy per bot token. A second copy causes `Conflict` errors.

`try.py` calls the API directly and saves each raw response to `tests/fixtures/`:

```sh
.venv/bin/python try.py search "pride and prejudice"
.venv/bin/python try.py avail 300172891
```

Deploying to a server is covered in [deploy/README.md](deploy/README.md).
