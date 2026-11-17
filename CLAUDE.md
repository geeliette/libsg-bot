# NLB Watch Bot
Telegram bot for Singapore NLB library books: search, merge editions, alert when available.

## Stack
- Python 3.12, python-telegram-bot (with JobQueue), httpx, SQLite
- NLB Catalogue API v2; spec in docs/. Use it as the source of truth for fields.
- Secrets come from .env only. Never hardcode or print keys.

## Core concepts
- Edition = one BRN. Work = a group of editions the user confirmed as "the same book".
- Watch = (user, work, set of branches). Alert only when an edition goes from not-available to available at a watched branch.

## Conventions
- Keep the NLB client in its own module, separate from bot handlers.
- Tests use saved JSON fixtures in tests/fixtures/, not live API calls.
- Small commits, one per working step.