# PayVerify Bot — PRD

## Original problem statement
User runs an online business. Customers pay via GPay Business and send payment screenshots + UTR last 4 to a WhatsApp group. Today the owner manually opens GPay Business dashboard, checks the transaction, and replies "received / not received" in the group. Goal: automate this loop.

## User choices
- WhatsApp Web automation via personal number (QR-scan based)
- GPay Business dashboard login + Playwright scraping
- Customer input: screenshot only (bot OCRs UTR out of it)
- FastAPI + React admin panel
- Full pipeline + admin dashboard + auto-reply

## Architecture
```
[WhatsApp group image] → whatsapp_service (Playwright, real DOM watcher)
        ↓
[image bytes]  → ocr_service (Gemini vision) → { utr, amount, payer, ... }
        ↓
[utr]          → duplicate check (Mongo)
                   ├── duplicate → mark & reply immediately (skip GPay)
                   └── new       → gpay_service (Playwright) → { found: bool }
        ↓
bot_orchestrator → Mongo `transactions` → whatsapp_service.send_reply
        ↓
Daily 21:00 IST cron → email_service (Resend, managed) → owner_email
```

- Backend: FastAPI, Motor, emergentintegrations, Playwright, httpx
- Frontend: React + Tailwind (dark, emerald/rose/amber/violet semantic accents), responsive
- Scheduling: `.emergent/crons.yml` (`daily-digest` at `0 21 * * *` IST)
- Mock switch: `BOT_MOCK_MODE=true` in `/app/backend/.env` (default in preview). OCR + email are always real.

## Personas
- Business owner (primary): configures group + email, monitors dashboard, occasionally overrides a decision.

## Core requirements
1. Read payment screenshots posted in a specific WhatsApp group
2. Extract UTR from screenshot
3. Verify UTR against GPay Business dashboard
4. Auto-reply in the group with success/failure
5. Admin dashboard with counts, log, manual override, setup

## What's been implemented
### 2026-01-15 — MVP (iteration 1)
- Full FastAPI backend, real Gemini OCR, mock GPay & WhatsApp services with matching API surface
- React admin dashboard: Overview, Transactions, Test Upload, Setup
- Configurable reply templates with `{utr_last4}`, `{amount}` placeholders
- Testing agent: backend 100%, frontend 100% after one URL fix

### 2026-01-15 — Iteration 2 (all four next actions)
- **Live WhatsApp Watcher**: Real Playwright DOM watcher (`whatsapp_service._monitor_loop`) that opens the target group, primes `seen_ids`, polls every 3s for new image bubbles, canvas-exports each blob to JPEG, and calls `on_image(sender, bytes, msg_id)`. `send_reply` opens the message context menu, clicks Reply, types & sends. All still respects `BOT_MOCK_MODE`.
- **Local Runner Kit**: `/app/local-runner/` with `Dockerfile.backend` (Playwright base image), `docker-compose.yml` (mongo + backend + frontend), `.env.example`, `start.sh` one-click, and a full `README.md` covering QR setup, GPay 2FA, persistent profiles, and troubleshooting.
- **Duplicate UTR Guard**: `bot_orchestrator._find_duplicate()` checks Mongo before GPay. Duplicates get their own `status="duplicate"`, `duplicate_of` field, a dedicated `reply_template_duplicate` with `{utr_last4}`, `{orig_sender}`, `{orig_time}` placeholders, a violet stat card + row badge + filter chip on the frontend, and GPay is not touched.
- **Daily Payment Digest**: Emergent-managed Resend integration via `email_service.py` (guardrail-gated, no user-supplied HTML). Settings gain `owner_email`, `owner_name`, `digest_enabled`. `.emergent/crons.yml` calls `/api/cron/digest` at 21:00 IST with HMAC bearer auth + `X-Webhook-Id` idempotency. Cron acks 2xx immediately and kicks a background task. Dashboard has a "Send digest now" button; `/api/digest/history` records each send.
- Responsive header (mobile viewport 390px: nav collapses to icons, no horizontal overflow).
- Testing agent iter 2: backend 100%, frontend 100%, plus review-nit fixes applied (top-level asyncio import; duplicate lookup narrowed to root submissions).

## Backlog
### P0
- Multi-group support (pick which groups to watch)
- SMS-based fallback verification for GPay (much more reliable than dashboard scraping)
### P1
- Encrypted at-rest storage of GPay credentials (currently in-memory only)
- Retry queue: if GPay verify errors, requeue after N min instead of instantly marking not_received
- Optional WhatsApp digest (send the summary as a WA message to a chosen chat)
### P2
- Fraud rules: block a UTR whose amount doesn't match a pending order
- Order-matching: attach each verified payment to an order id from a linked shop
- CSV export & GSheet sync
- Multi-currency
