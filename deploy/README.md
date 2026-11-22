# Deploying (Oracle Cloud Always Free)

The bot runs 24/7 on a free Ubuntu server as a systemd service: it starts on boot and restarts 30 s after a crash. It only makes outbound connections, so no ports need opening.

Deploying doesn't go through GitHub. `push.sh` copies the project folder from your machine to the server, so whatever is on disk goes out, committed or not.

**Run only one copy.** Telegram allows one running bot per token. A second copy (on your laptop, say) causes `Conflict` errors.

## 1. Create the server

Sign up at cloud.oracle.com and pick **Singapore** as the home region (it can't be changed later). Create a compute instance:

- Image: Canonical Ubuntu 24.04.
- Shape: `VM.Standard.E2.1.Micro` (Always Free), or `VM.Standard.A1.Flex` with 1 OCPU / 6 GB if available.
- SSH key: upload your public key (`~/.ssh/id_rsa.pub`).
- Note the public IP. The login user is `ubuntu`.

Oracle may reclaim Always Free VMs that stay nearly idle for a week, and this bot is very light. Upgrading the account to *Pay As You Go* stops that, and Always Free resources stay free.

## 2. First deploy

From the project folder:

```sh
ssh ubuntu@<ip> 'mkdir -p libsg-bot'
scp .env ubuntu@<ip>:libsg-bot/.env
# Optional, to keep saved books: stop the local bot first, then
scp libsg.db ubuntu@<ip>:libsg-bot/libsg.db
deploy/push.sh ubuntu@<ip>
```

`push.sh` rsyncs the code (never `.env`, the database, `.venv`, `docs/` or `tests/`) and runs `deploy/setup.sh` on the server. That script installs Python 3.14 (via uv) and the dependencies, sets the timezone to Singapore and starts the service.

## 3. Updates

Run `deploy/push.sh ubuntu@<ip>` again. It restarts the bot, which interrupts any check in progress, so make sure it's idle first. Saved books are kept.

## On the server

```sh
journalctl -u libsg-bot -f              # live log
sudo systemctl status libsg-bot         # is it running?
sudo systemctl restart libsg-bot        # restart
sudo systemctl stop libsg-bot           # stop (starts again on reboot unless you `disable` it)
```

The database is at `~/libsg-bot/libsg.db`. Copy it back now and then so saved books survive losing the VM:

```sh
scp ubuntu@<ip>:libsg-bot/libsg.db backup.db
```
