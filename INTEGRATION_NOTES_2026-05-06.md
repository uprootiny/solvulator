# Integration Notes — 2026-05-06

Branch: `integration/ralphex-bootstrap`

## What was integrated
- Added `pwa/` module from local Erlich scaffold (`/Users/uprootiny/Erlich/solvulator/app`).
- This preserves existing repo architecture while introducing the ralphex-mode Solvulator mobile-first shell.

## PWA highlights
- Local Python server bundle (`pwa/server.py`)
- iPhone-ready single-page workflow (`pwa/index.html`)
- "How It Works · ralphex mode" modal and staged gate loop copy

## Run
```bash
cd pwa
ANTHROPIC_API_KEY=sk-ant-... python3 server.py
```
