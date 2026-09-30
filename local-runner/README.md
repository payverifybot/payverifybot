# PayVerify Bot — One-Click Windows Install

## For anyone (no technical skills needed):

1. **Save-to-GitHub** this project in your Emergent chat (one-time)
2. **Download the installer**: right-click **install.cmd** in the `local-runner` folder on GitHub → **Save link as** → save to your Desktop
3. **Double-click install.cmd** — the installer takes care of everything:
   - Installs Docker Desktop if needed
   - Downloads the bot
   - Asks for your API keys once
   - Starts the bot and opens the dashboard
   - Creates a desktop shortcut
   - Sets it to auto-start whenever Windows boots

That's it. To share with another office → just send them the `install.cmd` file (or a link to it). They double-click and get the same setup.

## Daily use

- **Open dashboard**: double-click **"PayVerify Bot"** shortcut on your desktop
- **Stop bot**: double-click **stop.cmd** in `%USERPROFILE%\PayVerifyBot\local-runner`
- The bot auto-restarts whenever you log in to Windows

## First-time dashboard setup (5 min)

Once the browser opens on `http://localhost:3000`:
1. **Setup → Bot settings** → type each WhatsApp group name as a chip → Save
2. **Setup → WhatsApp Web session** → Start → scan QR from your phone
3. **Setup → GPay Business accounts** → Add each account (label + email + password)
4. **Setup → Daily digest** → your email → Save

Post a payment screenshot in your WhatsApp group and watch the bot reply within seconds.

## Something not working?

- `docker compose logs -f backend` from `%USERPROFILE%\PayVerifyBot\local-runner` in PowerShell — shows what the bot is doing
- **Reinstall**: delete `%USERPROFILE%\PayVerifyBot`, re-run `install.cmd`
- **Update to newer bot version**: re-run `install.cmd` — it re-downloads the code without touching your `.env` or account data
