#!/usr/bin/env bash
# Run ON YOUR MAC: copy the code to the server and restart the bot there.
# Usage: deploy/push.sh ubuntu@<server-ip>
# Never sends .env, the database, .venv or docs/; the server keeps its own copies.
set -euo pipefail
HOST="${1:?Usage: deploy/push.sh user@server}"
cd "$(dirname "$0")/.."

rsync -az --delete \
  --exclude .git --exclude .venv --exclude .env --exclude '*.db' --exclude '*.db-*' \
  --exclude __pycache__ --exclude .pytest_cache --exclude docs --exclude tests --exclude .claude \
  ./ "$HOST:libsg-bot/"

ssh "$HOST" 'bash libsg-bot/deploy/setup.sh'
