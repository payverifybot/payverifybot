# PayVerify Bot — PRD

## Original problem statement
User runs an online business. Staff post payment screenshots to a WhatsApp group and expect a confirmation within 1–2 minutes. Payments arrive across **multiple GPay Business accounts** (typically 2 active at a time; rotated as each hits its receiving limit). The bot must:
- Read each screenshot's UTR
- Verify it against every currently-active GPay account **in parallel**
- Reply in the group with the outcome, naming which account confirmed the payment
- Let staff flip "limit reached" on an account with one click

## User choices
- WhatsApp Web automation via personal number (QR-scan based)
- GPay Business dashboard automation via Playwright (per-account browser session)
- Customer input: screenshot only (bot OCRs UTR out of it)
- FastAPI + React admin panel
- Multi-group + multi-GPay-account pool
- Fast reply (target: 1–2 min; achieved: ~3s in mock, expected 3–10s live)

## Architecture
```
[Group image] → whatsapp_service (rotator across N groups, Playwright)
        ↓ (sender, image_bytes, msg_id, group)
[image bytes] → ocr_service (Gemini vision) → { utr, amount, payer, ... }
        ↓
[utr]         → duplicate check (Mongo, GLOBAL across groups & accounts)
                  ├── duplicate → mark & reply (skip GPay)
                  └── new       → gpay_pool.verify_utr(utr)
                                    └── asyncio.gather across every ACTIVE
                                        + logged-in account. First hit wins.
                                    → { found, account_label, checked_accounts[] }
        ↓
bot_orchestrator → Mongo txns (with group + winning account_label)
                → whatsapp_service.send_reply (quoted)
        ↓
Daily 21:00 IST cron (.emergent/crons.yml) → email_service (Resend) → owner
```

- Backend: FastAPI, Motor, emergentintegrations (Gemini), Playwright, httpx (Resend), cryptography (Fernet)
- Frontend: React + Tailwind (dark, responsive, chips + filters)
- Mock switch: `BOT_MOCK_MODE=true` — GPay pool + WhatsApp simulated; OCR + email always real.
- Persistence: `gpay_accounts` collection (passwords Fernet-encrypted with `GPAY_ENC_KEY`), `settings`, `transactions`, `digests`, `cron_runs`.

## Personas
- Business owner: configures groups + email + accounts, monitors dashboard.
- Staff: posts payment slips in WhatsApp; sees bot reply within seconds.

## Core requirements
1. Read screenshots posted in one or more WhatsApp groups
2. Extract UTR from screenshot (Gemini vision)
3. Verify UTR against every active GPay Business account in parallel
4. Auto-reply in the group with success (naming winning account) / failure / duplicate
5. Admin dashboard: totals, per-group + per-account breakdown, manual override, setup
6. One-click "Hit limit" and "Reactivate" per account

## What's been implemented
### 2026-01-15 · Iter 1 — MVP
- FastAPI backend, real Gemini OCR, mock GPay & WhatsApp with matching API surface
- React admin: Overview, Transactions, Test Upload, Setup — testing 100%/100%

### 2026-01-15 · Iter 2 — Watcher, Runner, Duplicate, Digest
- Live WhatsApp DOM watcher (Playwright)
- `/app/local-runner/` Docker kit
- Duplicate UTR guard
- Daily digest via Emergent-managed Resend + `.emergent/crons.yml` + HMAC cron + idempotency
- Testing 100%/100%

### 2026-01-15 · Iter 3 — Multi-Group
- `Settings.group_names: List[str]`, legacy `group_name` auto-migrated
- WhatsApp rotator loops through groups (prime seen ids per-group)
- Every txn tagged with `group`; new `/transactions/groups` aggregate + `?group=` filter
- Frontend GroupChips + group filter row + row/detail group display
- Testing 100%/100%

### 2026-01-15 · Iter 4 — Multi-GPay-Account Pool
- New `gpay_accounts` Mongo collection with Fernet-encrypted passwords
- `GPayPoolService` manages one Playwright session per account; `verify_utr` does `asyncio.gather` across every active + logged-in account and returns the winning `account_label` + full `checked_accounts` list
- New REST surface: `GET /api/gpay/accounts`, `POST /api/gpay/accounts`, `PATCH /api/gpay/accounts/{id}`, `DELETE /api/gpay/accounts/{id}`, `POST .../hit-limit`, `POST .../reactivate`, `POST .../re-login`
- Legacy `/api/gpay/login` shims to upsert a "Default" account
- Startup restore: previously-active accounts auto-login using stored ciphertext
- Reply templates gain `{account_label}` placeholder; success reads "✅ Received via Store-A. UTR ...9900 (₹4200)"
- `/api/status` returns `gpay_active_count` + `gpay_total_count`; header pill reads "GPay ×2 online"
- Dashboard KV row & Setup panel `GPayAccounts` with per-account status pill + Hit limit / Reactivate / Re-login / Remove buttons + Add form (label + email + password)
- Verified end-to-end: 2.8s for OCR + parallel verify with 2 accounts (mock)
- Testing 100%/100% (47/47 tests, 16 new + 31 regression)

### 2026-02-01 · Iter 11 — Live-mode observability + native Windows runner
- **whatsapp_service.py rewritten**: step tracking (idle/launching/awaiting_qr/connected/error/stopped), QR auto-refreshes every 5s while unlinked (handles WA's 60s QR rotation), full-page screenshots captured on every state change, human-readable `prompt` messages
- **gpay_service.py rewritten**: per-account step tracking (launching → loading_login → entering_email → entering_password → awaiting_2fa | awaiting_captcha | awaiting_review → opening_dashboard → logged_in | error). `_wait_for_login_outcome` polls the page for 10 min looking for dashboard / 2FA prompts / captcha / password errors and automatically continues once the operator resolves the manual step. After login, auto-navigates to `GPAY_TXN_URL` (overridable) for fast UTR verification.
- **New endpoints**: `GET /api/whatsapp/status` (step, qr_data_url, screenshot_b64, prompt, error), `GET /api/gpay/accounts/{id}/status` (per-account step + screenshot + prompt + error).
- **Frontend Setup.jsx**: WhatsApp panel now shows `StepBadge`, live prompt/error boxes, auto-refreshing QR image with animated refresh icon, collapsible live browser screenshot. Properly parses HTTP errors (`d.detail || d.error`).
- **Frontend GPayAccounts.jsx**: each account renders `StepPill` + live prompt/error boxes + collapsible live browser view. Polls `/status` every 3s.
- **Frontend Dashboard.jsx**: Diagnostics list now uses `<pre>` with `whitespace-pre-wrap` and per-check bordered cards; full multi-line error text visible (no more truncation).
- **Native Windows runner** at `local-runner/native/`: install-native.cmd + install-native.ps1 that installs Python 3.11 + MongoDB 7 + Playwright Chromium directly on Windows (no Docker) and runs Chromium HEADED so Google 2FA / captcha screens are visible to the operator. Persistent Chromium profile shared with Docker install.
- Testing 100%/100% (backend + frontend)

## Backlog
### P0
- Restart WhatsApp watcher automatically when `group_names` changes (currently stop/start)
- Auto-await async login on `/reactivate` to remove the 1-poll flicker
### P1
- SMS-based fallback verification (survives GPay dashboard blocks)
- Retry queue: requeue GPay-verify errors after N min
- Optional WhatsApp digest to a chosen chat, in addition to email
- Slugify account labels for testids / IDs
### P2
- Order-matching: attach each verified UTR to a pending order id
- Fraud rules (amount mismatch, velocity)
- CSV export + Google Sheets sync
- Per-account daily receipt totals in the digest email
