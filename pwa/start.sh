#!/usr/bin/env bash
# SOLVULATOR — local bundle launcher
# Usage: ./start.sh
# Or:    ANTHROPIC_API_KEY=sk-ant-... ./start.sh

set -euo pipefail
cd "$(dirname "$0")"

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo ""
  echo "  ANTHROPIC_API_KEY not set."
  echo "  Set it then re-run:"
  echo ""
  echo "    export ANTHROPIC_API_KEY=sk-ant-..."
  echo "    ./start.sh"
  echo ""
  echo "  Or run with a Gemini key instead:"
  echo "    GEMINI_API_KEY=... ./start.sh   (requires server.py edit)"
  echo ""
  read -rp "  Press Enter to start anyway (Sheet proxy will work, LLM calls will fail) ..."
fi

PORT=${PORT:-8080}
python3 server.py
