# PayVerify Bot — Windows install

Two installers, pick one based on what you need:

## 1. Docker install (headless)
For office/back-end use. Chromium runs invisibly. Fine once GPay logins are
already saved. Google's first-time 2FA typically FAILS here because it can't
see a real browser window.

Steps: **install.cmd** → double-click → follow prompts. Dashboard at
`http://localhost:8001`.

## 2. Native install (headed, RECOMMENDED for first-time GPay login)
Runs Python + Chromium directly on Windows. Chromium opens as a real visible
window so you can approve Google 2FA / captcha. See
[native/README.md](native/README.md).

Steps: right-click **native/install-native.cmd** → **Run as administrator** →
follow prompts.

## Daily use

- **Open dashboard**: double-click the desktop shortcut ("PayVerify Bot" for
  Docker, "PayVerify Bot (Native)" for native)
- **Stop bot**: double-click `stop.cmd` in the install folder
- Docker version auto-restarts on login; native version needs a click

## First-time dashboard setup (5 min)

Once the dashboard is open:
1. **Setup → Bot settings** → type each WhatsApp group name as a chip → Save
2. **Setup → WhatsApp Web session** → Start → scan the QR that appears
   (it auto-refreshes every 5s)
3. **Setup → GPay Business accounts** → Add each account. A live status card
   below each account will show you exactly what the bot's browser is doing.
   When it says "needs 2FA", approve the prompt on your phone and the bot
   continues automatically.
4. **Setup → Daily digest** → your email → Save

Post a payment screenshot in your WhatsApp group and watch the bot reply within
seconds.

## Something not working?

- **Dashboard → Overview → System diagnostics → Run full self-check** — this
  now shows the full error text for every component; copy and share if you
  need help.
- Docker: `docker compose logs -f backend` from `%USERPROFILE%\PayVerifyBot\local-runner`
- Native: the terminal window that opened when you clicked the shortcut has
  the live logs.
- **Reinstall**: delete `%USERPROFILE%\PayVerifyBot` (or `PayVerifyBot-Native`)
  and re-run the installer.
