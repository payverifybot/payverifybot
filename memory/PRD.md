# PayVerify Bot — PRD

## Original problem statement
User runs an online business. Customers pay via GPay Business and send payment screenshots + UTR to a WhatsApp group. User manually verifies each screenshot against the GPay dashboard and replies "received / not received" in the group. Goal: automate the loop across multiple groups.

## User choices
- WhatsApp Web automation via personal number (QR-scan based)
- GPay Business dashboard automation via Playwright
- Customer input: screenshot only (bot OCRs UTR out of it)
- FastAPI + React admin panel
- Full pipeline + admin dashboard + auto-reply
- **Iter 3**: bot must watch **multiple groups concurrently** (bulk-order chat + premium chat)

## Architecture
```
[WhatsApp group image] → whatsapp_service (Playwright rotator across N groups)
        ↓ tagged with group
[image bytes] → ocr_service (Gemini vision) → { utr, amount, payer, ... }
        ↓
[utr]         → duplicate check (Mongo, GLOBAL across groups)
                  ├── duplicate → mark & reply (skip GPay)
                  └── new       → gpay_service → { found }
        ↓
bot_orchestrator → Mongo txns (with group tag) → whatsapp_service.send_reply
        ↓
Daily 21:00 IST cron → email_service (Resend, group column in table) → owner
```

- Backend: FastAPI, Motor, emergentintegrations (Gemini), Playwright, httpx (Resend)
- Frontend: React + Tailwind (dark, responsive, chips + filters)
- Scheduling: `.emergent/crons.yml`
- Mock switch: `BOT_MOCK_MODE=true` (preview). OCR + email are always real.

## Personas
- Business owner: configures groups + email, monitors dashboard, occasionally overrides a decision.

## Core requirements
1. Read payment screenshots posted in one or more WhatsApp groups
2. Extract UTR from screenshot
3. Verify UTR against GPay Business dashboard (skip on duplicate)
4. Auto-reply in the same group with success/failure/duplicate message
5. Admin dashboard: totals, per-group breakdown, log, manual override, setup

## What's been implemented
### 2026-01-15 · Iter 1 — MVP
- FastAPI backend, real Gemini OCR, mock GPay & WhatsApp with matching API surface
- React admin: Overview, Transactions, Test Upload, Setup
- Reply templates with `{utr_last4}`, `{amount}` placeholders. Backend 100% / Frontend 100%.

### 2026-01-15 · Iter 2 — Watcher, Runner, Duplicate, Digest
- Live WhatsApp DOM watcher (Playwright poll loop, canvas → JPEG, quoted-reply send)
- `/app/local-runner/` — Dockerfile, docker-compose, one-click `start.sh`, README
- Duplicate UTR guard with `duplicate_of` linkage and `reply_template_duplicate`
- Daily digest email via Emergent-managed Resend (`.emergent/crons.yml`, HMAC-authed cron, idempotency)
- Responsive header. Testing 100%/100%.

### 2026-01-15 · Iter 3 — Multi-Group
- Settings gained `group_names: List[str]` (source of truth) + legacy `group_name` (auto-migrated once)
- WhatsApp service accepts a list; rotator iterates through groups in `_monitor_loop`, priming per-group seen sets on first visit
- Every transaction persists a `group` tag
- New endpoints: `/transactions/groups` (aggregate), `?group=` query on `/transactions` and `/transactions/stats`
- `/api/status` exposes `whatsapp_groups` and `whatsapp_active_group`
- Frontend Setup: chip-list GroupChips (add via input + Enter or Add button, remove via X). Watching-list shown under WhatsApp session with active pill highlighted.
- Frontend Transactions: `group-filters` chip row with per-group counts + emerald group name on each row + Group KV in detail panel
- Digest email now includes a Group column
- Testing 100%/100% (15/15 backend + full frontend; 1 skipped OCR-non-determinism assert). Applied review nit (persist migration on read).

## Backlog
### P0
- SMS-based fallback verification for GPay (more reliable than dashboard scraping)
- Restart WhatsApp watcher automatically when `group_names` changes (currently user must stop/start)
### P1
- Encrypted at-rest storage of GPay credentials
- Retry queue: requeue GPay-verify failures after N min instead of marking not_received
- Optional WhatsApp digest to a chosen chat, in addition to email
- Case-insensitive de-dup on group names
### P2
- Order-matching: attach each verified UTR to a pending order id from a linked shop
- Fraud rules (amount mismatch, velocity checks)
- CSV export + Google Sheets sync
- Multi-currency
