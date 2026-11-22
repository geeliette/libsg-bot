# NLB Watch Bot

A Telegram bot for Singapore NLB library books: see where a book is on the shelf across all its editions, keep a list of books you want, and get a message when one is back on the shelf at the libraries you choose.

Search for a book and tap it to see which libraries have it on the shelf. Save it to *My books* to keep it, with alerts on or off.

## Features

- **Search** by title, author or ISBN (`/search emma`, or just `/search` and then type it). Print books only, top 5 matches with *More results*.
- **Availability at once.** Tapping a result checks every edition and lists the libraries with a copy on the shelf (with counts) and those whose copies are all on loan. Checks under 5 minutes old are reused.
- **Editions together.** Each result is one of NLB's own groups of editions; untick any you don't want. Each edition shows year, language, publisher, pages, ISBN and a link to the NLB catalogue.
- **My books** (`/books`): saved books and where they are on the shelf as of the last check, with *Check now*, alerts on/off, *Edit* and *Remove*.
- **Alerts** when a copy goes from not available to on the shelf at one of your alert libraries (or any library). The picker only lists open libraries that hold a copy; libraries closed for renovation or relocation never appear.

## Commands

| Command | What it does |
|---|---|
| `/search <title, author or ISBN>` | Find a book and see where it's on the shelf (bare `/search` asks for one) |
| `/books` | My books: where each is, Check now, alerts on/off, Edit, Remove |
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

The bot loads the library list from NLB on startup, then checks the editions of books with alerts on every 30 minutes while libraries are open (opening hours from GetBranches, plus 30 minutes after closing; public holidays count as closed). If the hours can't be loaded, it checks around the clock. Data is stored in `libsg.db` (SQLite, gitignored).

### Optional settings (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `DB_PATH` | `libsg.db` | SQLite database file |
| `POLL_MINUTES` | `30` | Minutes between regular checks |
| `POLL_SPACING_SECONDS` | `10` | Pause between editions during regular checks (on top of the 1.5 s gap between any two NLB calls) |

## Deploying (Oracle Cloud Always Free)

Runs the bot 24/7 on a free Ubuntu server as a systemd service: it starts on boot and restarts 30 s after a crash. Only outbound connections are used, so no ports need opening.

**Run only one copy.** Telegram allows one running bot per token; a second copy (e.g. on your Mac) causes `Conflict` errors.

1. **Create the server.** Sign up at cloud.oracle.com and pick **Singapore** as home region (it can't be changed later). Create a compute instance:
   - Image: Canonical Ubuntu 24.04.
   - Shape: `VM.Standard.E2.1.Micro` (Always Free), or `VM.Standard.A1.Flex` with 1 OCPU / 6 GB if available.
   - SSH key: upload your public key (`~/.ssh/id_rsa.pub`).
   - Note the public IP. The login user is `ubuntu`.

   Oracle may reclaim Always Free VMs that stay nearly idle for a week, and this bot is very light. Upgrading the account to *Pay As You Go* stops that; Always Free resources stay free.

2. **First deploy**, from the project folder on your Mac:
   ```sh
   ssh ubuntu@<ip> 'mkdir -p libsg-bot'
   scp .env ubuntu@<ip>:libsg-bot/.env
   # Optional, to keep saved books: stop the bot on your Mac first, then
   scp libsg.db ubuntu@<ip>:libsg-bot/libsg.db
   deploy/push.sh ubuntu@<ip>
   ```
   `push.sh` copies the code and runs `deploy/setup.sh` on the server, which installs Python 3.14 (via uv) and the dependencies, sets the timezone to Singapore and starts the service.

3. **Updates:** run `deploy/push.sh ubuntu@<ip>` again. It restarts the bot, so check it's idle first.

**On the server:**
```sh
journalctl -u libsg-bot -f              # live log
sudo systemctl status libsg-bot         # running?
sudo systemctl restart libsg-bot        # restart
sudo systemctl stop libsg-bot           # stop (starts again on reboot unless you `disable` it)
```

The database lives at `~/libsg-bot/libsg.db` on the server. Copy it back now and then (`scp ubuntu@<ip>:libsg-bot/libsg.db backup.db`) so saved books survive losing the VM.

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
  grouping.py     One suggested book per NLB group of editions
  branches.py     Lending libraries and whether they're open
  db.py           SQLite storage
  watcher.py      The alert rule
  poller.py       One round of availability checks
  ratelimit.py    Spam guard for Check now and opening results
  messages.py     All bot text and buttons
  bot.py          Telegram handlers, scheduled jobs, entry point
try.py            CLI for poking the API
deploy/           push.sh (Mac → server) and setup.sh (server: install + systemd service)
tests/            pytest suite + fixtures/
```

## Good to know

- **NLB rate limits.** NLB returns HTTP 429 when calls come too fast, and the limit isn't documented. The quota is shared by all users (it's per API key). The bot sends calls one at a time with at least 1.5 s between any two, honours `Retry-After`, and stops a check round early if it's rate-limited; skipped editions go first next round.
- **No reservations.** The bot only checks shelves. Reserve books in the NLB app.
- **Restarts** interrupt any check in progress; saved books are kept.
