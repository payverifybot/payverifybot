# PayVerify Bot — Native Windows install

This is an alternative to the Docker installer, designed for the GPay login flow.

## Why you might want this

Docker on Windows runs Chromium **headless** (invisible). Google actively blocks
headless Chromium logins — you get a "couldn't sign you in" screen or a 2FA
prompt you cannot answer.

The **native** installer runs Chromium as a **real visible window on your
desktop**. When Google asks for 2FA or a captcha, you see it and approve it just
like you would in normal Chrome. The bot then detects that the challenge was
cleared and continues to the transactions dashboard automatically.

## Steps

1. Right-click **install-native.cmd** → **Run as administrator**.
2. It will:
   - Install Python 3.11 (via winget) if missing
   - Install MongoDB 7 Community (via winget) if missing
   - Copy backend + built frontend into `%USERPROFILE%\PayVerifyBot-Native`
   - Install Python deps + Playwright Chromium
   - Ask you once for your API keys
   - Start the bot and open the dashboard
3. In the dashboard, add your GPay Business account. A visible Chromium
   window will pop up on your desktop. Approve the Google 2FA prompt on your
   phone. The bot will detect success and jump to the transactions dashboard
   automatically.
4. Next boot: click the "PayVerify Bot (Native)" desktop shortcut.

## When to use Docker vs Native

| | Docker installer | Native installer |
|--|--|--|
| Setup time | 5–10 min (first run) | 5–10 min (first run) |
| Chromium visibility | Headless (hidden) | Headed (visible on desktop) |
| Google 2FA / captcha | Hard — usually blocked | Works normally |
| Auto-restart on reboot | Yes | Manual (desktop shortcut) |

**Recommended: use native for the first-time GPay login, then either keep it or
switch back to Docker** (once the persistent Chromium profile is saved, Docker
can reuse it because both installers point at the same profile folder on disk).

## Troubleshooting

* **"MongoDB service not found"** — install manually from
  <https://www.mongodb.com/try/download/community>, choose "Install as a
  service", then re-run.
* **"Cannot find python.exe"** — winget installed Python but PATH wasn't
  refreshed. Close the terminal, open a new one, re-run.
* **"Chromium version mismatch"** — run
  `%USERPROFILE%\PayVerifyBot-Native\.venv\Scripts\python.exe -m playwright install chromium`
  from a new terminal.
