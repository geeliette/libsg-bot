#!/usr/bin/env bash
# Run ON THE SERVER from the project folder: installs Python 3.14 + dependencies
# and (re)starts the bot as a systemd service. Safe to re-run after every update.
# Re-running restarts the bot, which interrupts any check in progress.
set -euo pipefail
cd "$(dirname "$0")/.."
APP_DIR="$(pwd)"
APP_USER="$(whoami)"

if [ ! -f .env ]; then
  echo "Missing $APP_DIR/.env - copy it from your Mac first (see README, Deploying)." >&2
  exit 1
fi
chmod 600 .env

# uv installs Python 3.14 without touching the system Python.
if ! command -v uv >/dev/null && [ ! -x "$HOME/.local/bin/uv" ]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
uv python install 3.14
[ -x .venv/bin/python ] || uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -r requirements.txt

# Log timestamps in Singapore time.
sudo timedatectl set-timezone Asia/Singapore

sudo tee /etc/systemd/system/libsg-bot.service >/dev/null <<EOF
[Unit]
Description=NLB Watch Bot (Telegram)
Wants=network-online.target
After=network-online.target

[Service]
User=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python -m libsg.bot
Environment=PYTHONUNBUFFERED=1
Restart=always
# Wait between restarts so a crash loop can't hammer NLB.
RestartSec=30
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable libsg-bot >/dev/null
sudo systemctl restart libsg-bot
sleep 5
sudo systemctl --no-pager --lines=5 status libsg-bot
