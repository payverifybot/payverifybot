#!/usr/bin/env bash
# One-click starter for the PayVerify bot on your own laptop / mini-PC.
# Requires: Docker + Docker Compose v2.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "→ Creating .env from .env.example. Edit it before continuing."
  cp .env.example .env
  ${EDITOR:-nano} .env
fi

mkdir -p data/browser data/mongo

echo "→ Building and starting containers…"
docker compose up -d --build

echo
echo "════════════════════════════════════════════════════════════════"
echo "  Backend:   http://localhost:8001"
echo "  Dashboard: http://localhost:3000"
echo "════════════════════════════════════════════════════════════════"
echo
echo "First-time setup:"
echo "  1. Open the dashboard → Setup"
echo "  2. Enter your WhatsApp group name, then click 'Start session'"
echo "     - A QR code appears in the dashboard. Scan it from your phone."
echo "  3. Enter your GPay Business Google email + password → Log in."
echo "     - If Google asks for 2FA, complete it on your phone."
echo "  4. That's it. Every payment screenshot posted in the group will be"
echo "     verified and replied to automatically."
echo
echo "Tail logs:   docker compose logs -f backend"
echo "Stop:        docker compose down"
