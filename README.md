# NLB Watch Bot

A Telegram bot that tells you when a Singapore NLB library book is back on the shelf at the libraries you choose.

Search for a book, pick the editions you care about (e.g. only English ones), choose your libraries, and the bot messages you when a copy becomes available.

## Features

- **Search** by title, author or ISBN. Print books only, top 5 matches with *More results*.
- **Merged editions.** Editions NLB lists separately are grouped into one book; untick any you don't want. Each edition shows year, language, publisher, pages, ISBN and a link to the NLB catalogue.
- **Choose libraries**, or watch any library. Libraries closed for renovation or relocation are hidden automatically.
- **Alerts** when a copy goes from not available to on the shelf at a watched library.
- **`/watches`** shows where each book is on the shelf as of the last check, with *Check now*, *Edit* and *Stop watching*.

## Commands

| Command | What it does |
|---|---|
| `/search <title, author or ISBN>` | Find a book and start watching it |
| `/watches` | Your watched books, where they are, and Check now / Edit / Stop |
| `/help` | How the bot works |

## Setup

You need Python 3.14, an NLB API key with its app code, and a Telegram bot token.

1. **Get credentials**
   - NLB: apply for API access through NLB's Open Web Service (openweb.nlb.gov.sg). You'll get an API key and an app code.
   - Telegram: create a bot with [@BotFather](https://t.me/BotFather) and copy its token.

2. **Create `.env`** in the project root. It's gitignored, so never commit it.
   ```
   NLB_API_KEY=...
   NLB_APP_CODE=...
   TELEGRAM_BOT_TOKEN=...
   ```

3. **Install**
   ```sh
   python3.14 -m venv .venv
   .venv/bin/pip install -r requirements-dev.txt   # or requirements.txt to run without tests
   ```

4. **API specs (optional, for development):** put NLB's OpenAPI files in `docs/` as `nlb-catalogue-openapi.json` and `nlb-library-openapi.json`. They're gitignored.

## Running

```sh
.venv/bin/python -m libsg.bot
```

The bot loads the library list from NLB on startup, then checks all watched editions every 30 minutes. Data is stored in `libsg.db` (SQLite, gitignored).

### Optional settings (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `DB_PATH` | `libsg.db` | SQLite database file |
| `POLL_MINUTES` | `30` | Minutes between regular checks |
| `POLL_SPACING_SECONDS` | `10` | Pause between NLB calls during regular checks |

## Trying the API without the bot

`try.py` calls the NLB API directly and saves every raw response to `tests/fixtures/`:

```sh
.venv/bin/python try.py search "pride and prejudice"
.venv/bin/python try.py avail 300172891          # copies per library for one BRN
.venv/bin/python try.py titles --author "jane austen"
```

## Tests

```sh
.venv/bin/pytest -q
```

Tests run against saved API responses in `tests/fixtures/`. They make no network calls and need no credentials.

## Project layout

```
libsg/
  nlb_client.py   NLB Catalogue + Library API client
  models.py       Editions and copies parsed from API responses
  grouping.py     Suggests which editions are the same book
  branches.py     Lending libraries and whether they're open
  db.py           SQLite storage
  watcher.py      The alert rule
  poller.py       One round of availability checks
  ratelimit.py    Spam guard for Check now
  messages.py     All bot text and buttons
  bot.py          Telegram handlers, scheduled jobs, entry point
try.py            CLI for poking the API
tests/            pytest suite + fixtures/
```

## Good to know

- **NLB rate limits.** NLB returns HTTP 429 when calls come too fast, and the limit isn't documented. The bot spaces its calls, sends them one at a time, and stops a check round early if it's rate-limited; skipped editions go first next round.
- **No reservations.** The bot only watches shelves. Reserve books in the NLB app.
- **Restarts** interrupt any check in progress; saved watches are kept.
