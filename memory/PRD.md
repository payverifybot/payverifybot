# PayVerify Bot — PRD

## Original problem statement
User runs an online business. Customers pay via GPay Business and send the payment screenshot + last-4 digits of the UTR to a WhatsApp group. Today the owner manually opens GPay Business dashboard, checks the transaction, and replies "received / not received" in the group. Goal: automate this loop.

## User choices (from ask_human)
- **WhatsApp**: WhatsApp Web automation via personal number (unofficial, QR-scan based)
- **GPay verification**: GPay Business dashboard login + scrape via browser automation
- **Customer input**: Payment screenshot only — bot must OCR the UTR out of it
- **Deployment**: FastAPI + React admin panel
- **Scope**: Full pipeline — WhatsApp automation + GPay automation + admin dashboard, with reply-to-message (received/not received)

## Architecture
```
[WhatsApp group image] → whatsapp_service (Playwright)
        ↓
[image bytes]  → ocr_service (Gemini vision, gemini-3-flash-preview) → { utr, amount, payer, ... }
        ↓
[utr, amount]  → gpay_service (Playwright on business.google.com/payments) → { found: bool, ... }
        ↓
bot_orchestrator → MongoDB `transactions` collection + reply via whatsapp_service
```

- Backend: FastAPI, Motor (MongoDB), emergentintegrations (Gemini via EMERGENT_LLM_KEY), Playwright
- Frontend: React + Tailwind, dark theme, emerald/rose/amber semantic accents
- **Preview mode**: `BOT_MOCK_MODE=true` in `/app/backend/.env` — GPay & WhatsApp are simulated (even last-digit UTR → received, odd → not received). OCR uses **real** Gemini.

## Personas
- **Business owner** (primary): configures WA group, links GPay, monitors the dashboard, occasionally overrides a decision.

## Core requirements
1. Read payment screenshots posted in a specific WhatsApp group
2. Extract the UTR from the screenshot
3. Verify UTR against GPay Business dashboard
4. Auto-reply in the group with success/failure message
5. Provide an admin dashboard with counts, transaction log, manual override, and setup

## What's been implemented (2026-01-15)
- Full FastAPI backend (`/api/status`, `/api/settings`, `/api/gpay/login`, `/api/gpay/verify`, `/api/whatsapp/start`, `/api/whatsapp/stop`, `/api/process-screenshot`, `/api/ocr`, `/api/transactions*`)
- Real Gemini vision OCR that reliably extracts UTR, amount, payer name from receipt images
- Mock-mode GPay & WhatsApp services with the exact same API surface as the real Playwright ones (drop-in when `BOT_MOCK_MODE=false`)
- Bot orchestrator that respects user-configured reply templates (`{utr_last4}`, `{amount}` placeholders)
- React admin dashboard with 4 pages:
  - **Overview** — stat cards, recent verifications, live bot status
  - **Transactions** — filterable log + detail panel + manual override
  - **Test Upload** — upload a screenshot and see the full pipeline result
  - **Setup** — WA group name, reply templates, auto-reply toggle, GPay login, WA session
- MongoDB persistence with proper (`_id` excluded, ISO datetime) serialization
- Test coverage: backend 100% (17 checks), frontend 100% after fix

## Deviations from ideal / known limits
- GPay Business dashboard automation is **fragile** because Google actively fights browser automation. For production, the user must run the bot process on a real machine where they can complete 2FA once. The container serves the admin dashboard.
- WhatsApp Web session lasts ~14 days per QR scan.
- The `_monitor_loop` in `whatsapp_service.py` that reads new group images is currently a stub — needs DOM scraping logic for `web.whatsapp.com` to be production-ready.

## Backlog (prioritized)
### P0
- Replace WhatsApp `_monitor_loop` stub with real DOM watcher for new image messages in target group
- Ship a `docker-compose.yml` + README so user can run the bot service locally against the hosted dashboard
### P1
- Encrypted at-rest storage of GPay credentials (currently in-memory only)
- Retry queue: if GPay dashboard errors, re-verify after N minutes rather than immediately marking not_received
- Fraud check: flag if the same UTR is submitted twice by different senders
### P2
- SMS fallback verification (Android companion) — much more reliable than GPay scraping
- Multi-group support
- Daily digest email of totals
