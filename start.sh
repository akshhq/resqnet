#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# ResQNet — start.sh  (macOS / Linux)
#
# The dashboard now has its OWN built-in simulator (the "+ Add Device" button
# in the browser). The Python simulator.py is optional and runs ALONGSIDE it —
# both can drive devices on the same map at the same time.
#
# Usage:
#   ./start.sh               backend + dashboard only (use "+ Add Device" in browser)
#   ./start.sh --with-sim    also launches the Python simulator (interactive)
#   ./start.sh --demo        also launches the Python simulator in demo mode
# ─────────────────────────────────────────────────────────────────────────────

set -e

DEMO_MODE=false
WITH_SIM=false

for arg in "$@"; do
  case $arg in
    --demo)     DEMO_MODE=true; WITH_SIM=true ;;
    --with-sim) WITH_SIM=true ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${GREEN}[resqnet]${NC} $1"; }
warn() { echo -e "${YELLOW}[resqnet]${NC} $1"; }
die()  { echo -e "${RED}[resqnet]${NC} $1"; exit 1; }

PIDS=()
cleanup() {
  echo ""
  log "Shutting down..."
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  exit 0
}
trap cleanup SIGINT SIGTERM

# ── Checks ────────────────────────────────────────────────────────────────────
[ -f "$ROOT/backend/app/main.py" ]          || die "backend/app/main.py not found. Run from repo root."
[ -f "$ROOT/Trial_Dashboard/index.html" ]   || die "Trial_Dashboard/index.html not found."

python3 -c "import fastapi, uvicorn" 2>/dev/null || \
  die "fastapi or uvicorn not installed. Run: pip install -r backend/requirements.txt"

# ── Backend ───────────────────────────────────────────────────────────────────
# Binds to 0.0.0.0 so both "localhost" and "127.0.0.1" resolve correctly.
log "Starting backend  → http://127.0.0.1:8000"
(cd "$ROOT/backend" && python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000) &
PIDS+=($!)

log "Waiting for backend to be ready..."
for i in $(seq 1 15); do
  python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/device/HEALTH')" \
    >/dev/null 2>&1 && log "Backend is up." && break
  sleep 1
  [ $i -eq 15 ] && warn "Backend took longer than expected — check the output above."
done

# ── Dashboard ─────────────────────────────────────────────────────────────────
log "Starting dashboard → http://localhost:5500"
(cd "$ROOT/Trial_Dashboard" && python3 -m http.server 5500 --quiet) &
PIDS+=($!)
sleep 1

log "Opening dashboard in browser..."
if command -v open >/dev/null 2>&1; then
  open "http://localhost:5500"
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "http://localhost:5500"
else
  warn "Could not detect browser command. Open http://localhost:5500 manually."
fi

# ── Python simulator (optional) ───────────────────────────────────────────────
if [ "$WITH_SIM" = false ]; then
  echo ""
  log "ResQNet is running."
  log "  Dashboard → http://localhost:5500"
  log "  Backend   → http://127.0.0.1:8000"
  echo ""
  log "Use the \"+ Add Device\" button in the dashboard to simulate"
  log "devices right in the browser."
  echo ""
  log "To ALSO run the Python simulator alongside it:"
  log "  ./start.sh --with-sim"
  log "  ./start.sh --demo"
  echo ""
  log "Press Ctrl+C to stop the backend and dashboard."
  wait
elif [ ! -f "$ROOT/simulator/simulator.py" ]; then
  warn "simulator.py not found — skipping. Backend + dashboard still running."
  wait
else
  echo ""
  log "Launching Python simulator IN ADDITION TO the browser's built-in"
  log "simulator. Both appear on the same dashboard at the same time."
  echo ""
  sleep 1
  if [ "$DEMO_MODE" = true ]; then
    log "Simulator: DEMO mode"
    (cd "$ROOT/simulator" && python3 simulator.py --demo)
  else
    log "Simulator: interactive  (p=panic  r=reset  0-3=mode  t=turn  q=quit)"
    (cd "$ROOT/simulator" && python3 simulator.py)
  fi
  PIDS+=($!)
  wait
fi