#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# ResQNet — start.sh  (macOS / Linux)
# Usage:  ./start.sh             interactive simulator
#         ./start.sh --demo      scripted demo
#         ./start.sh --no-sim    backend + dashboard only
# ─────────────────────────────────────────────────────────────────────────────

set -e

DEMO_MODE=false
NO_SIM=false

for arg in "$@"; do
  case $arg in
    --demo)   DEMO_MODE=true ;;
    --no-sim) NO_SIM=true ;;
  esac
done

# Resolve repo root (directory containing this script)
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
[ -f "$ROOT/simulator/simulator.py" ]       || warn "simulator/simulator.py not found — simulator will be skipped."

# ── Check packages ────────────────────────────────────────────────────────────
python3 -c "import fastapi, uvicorn" 2>/dev/null || \
  die "fastapi or uvicorn not installed. Run: pip install -r backend/requirements.txt"

# ── Backend ───────────────────────────────────────────────────────────────────
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

# Open in default browser (macOS: open, Linux: xdg-open)
log "Opening dashboard in browser..."
if command -v open >/dev/null 2>&1; then
  open "http://localhost:5500"
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "http://localhost:5500"
else
  warn "Could not detect browser command. Open http://localhost:5500 manually."
fi

# ── Simulator ─────────────────────────────────────────────────────────────────
if [ "$NO_SIM" = true ]; then
  warn "Simulator skipped. Start manually: cd simulator && python3 simulator.py"
elif [ ! -f "$ROOT/simulator/simulator.py" ]; then
  warn "simulator.py not found — skipping."
else
  sleep 1
  if [ "$DEMO_MODE" = true ]; then
    log "Simulator: DEMO mode"
    echo ""
    (cd "$ROOT/simulator" && python3 simulator.py --demo)
  else
    log "Simulator: interactive  (p=panic  r=reset  0-3=mode  t=turn  q=quit)"
    echo ""
    (cd "$ROOT/simulator" && python3 simulator.py)
  fi
  PIDS+=($!)
fi

log "ResQNet is running."
log "  Dashboard → http://localhost:5500"
log "  Backend   → http://127.0.0.1:8000"
log "Press Ctrl+C to stop everything."
echo ""
wait