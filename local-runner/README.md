# Local Runner Kit

Run the PayVerify Bot on your own machine so it can:
- log into **your** Google account for GPay Business
- scan **your** WhatsApp QR
- keep a persistent browser profile so you don't have to re-auth every time

The hosted Emergent dashboard still works — you can point it at the local backend
(`REACT_APP_BACKEND_URL=http://localhost:8001`) or use the bundled local dashboard.

## Prerequisites
- Docker Desktop / Docker Engine + Docker Compose v2 (`docker compose version` >= 2.20)
- A machine that stays on 24/7 (mini-PC, Raspberry Pi 5, small VPS, an old laptop)
- Chromium ~500MB disk

## One-click start

```bash
cd local-runner
./start.sh
```

The script:
1. Copies `.env.example` → `.env` and opens it in your `$EDITOR`. Fill:
   - `EMERGENT_LLM_KEY` — copy from your Emergent-hosted `/app/backend/.env`
   - `EMERGENT_EMAIL_KEY` — same
   - `EMAIL_FROM_NAME` — your business name
2. Creates `data/browser` (persistent Chromium profiles) and `data/mongo` (transactions DB)
3. Builds the backend image (Python + Playwright + Chromium)
4. Starts three services: MongoDB, the FastAPI bot, and the React dashboard

Open `http://localhost:3000`.

## First-time linking (5 minutes)

**Setup → WhatsApp Web session**
- Enter your group name in *Bot settings*
- Click **Start session**
- QR code appears in the panel — scan it from WhatsApp on your phone (Settings → Linked devices → Link a device)
- Session lasts ~14 days before you must re-scan

**Setup → GPay Business account**
- Enter Google email + password
- Click **Log in to GPay Business**
- If Google asks for 2FA / device confirmation, complete it on your phone
- The `data/browser/gpay_profile` volume keeps the session across restarts

Once both pills go green, drop a payment screenshot in the WhatsApp group — the bot will read it, verify against GPay, and reply within ~5 seconds.

## Daily commands

```bash
docker compose logs -f backend      # tail bot activity
docker compose ps                   # see what's running
docker compose restart backend      # after a code change
docker compose down                 # stop everything (profiles preserved)
docker compose down -v              # nuke MongoDB data as well
```

## Troubleshooting

- **"Group not found"** — the name in *Bot settings* must exactly match what WhatsApp shows in the chat list (including emojis).
- **GPay login stuck on 2FA** — open the container's remote debug port and click "Yes, it's me" once; the persistent profile remembers the device from then on.
- **QR keeps appearing** — the profile is fresh or expired. Just scan again.
- **Nothing happens when I post a screenshot** — check `docker compose logs -f backend`. Most common cause: WhatsApp didn't render the message inside the currently open chat. Open the group manually in the container to prime it.

## Security notes

- Your GPay Google password never leaves the machine you run this on. It's held only in-memory (in `gpay_service`) and used to drive the browser. Only the persistent cookie remains on disk.
- Everything in `data/` is sensitive — back it up privately, and never commit it.
- The bot exposes port 8001 on your LAN. Bind it to `127.0.0.1:8001` in `docker-compose.yml` if you don't want other devices on your network hitting it.
