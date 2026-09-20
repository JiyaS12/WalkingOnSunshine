#!/usr/bin/env bash
# One-command local demo: sets up backend + frontend and runs both.
#   ./demo.sh            -> http://localhost:3000
# Clinician login is printed at startup. Ctrl+C stops both servers.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

log() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# ---------- Python (mediapipe 0.10.14 needs 3.10 - 3.12) ----------
PY=""
for candidate in python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    ver="$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    case "$ver" in
      3.10|3.11|3.12) PY="$candidate"; break ;;
    esac
  fi
done
if [ -z "$PY" ]; then
  if command -v brew >/dev/null 2>&1; then
    log "Installing Python 3.12 with Homebrew"
    brew install python@3.12
    PY="$(brew --prefix python@3.12)/bin/python3.12"
  else
    die "Python 3.10-3.12 is required (3.13 cannot install MediaPipe). Install it, e.g. https://www.python.org/downloads/release/python-3120/ or 'brew install python@3.12', then re-run."
  fi
fi
log "Using $($PY --version) at $(command -v "$PY" || echo "$PY")"

# ---------- Node ----------
command -v node >/dev/null 2>&1 || die "Node.js 20 is required: https://nodejs.org (or 'brew install node@20')."
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
[ "$NODE_MAJOR" -ge 20 ] || die "Node.js 20+ is required (found $(node --version))."

# ---------- Backend ----------
log "Setting up backend"
cd "$ROOT/backend"
if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv
fi
.venv/bin/pip install -q --disable-pip-version-check -r requirements.txt

if [ ! -f .env ]; then
  log "Creating backend/.env with demo credentials"
  gen() { .venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))'; }
  cat > .env <<EOF
CLINICIAN_USERNAME=clinician
CLINICIAN_PASSWORD=Sana-Demo-2026
CLINICIAN_SESSION_SECRET=$(gen)
PATIENT_LINK_SIGNING_SECRET=$(gen)
SURVEY_INGEST_TOKEN=$(gen)
PATIENT_APP_BASE_URL=http://localhost:3000
PATIENT_LINK_TTL_SECONDS=86400
CORS_ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
CLINICIAN_COOKIE_SECURE=false
CLINICIAN_SESSION_TTL_SECONDS=28800
CLINICIAN_LOGIN_MAX_ATTEMPTS=5
CLINICIAN_LOGIN_WINDOW_SECONDS=60
OPENAI_API_KEY=
ALLOW_UNAUTHENTICATED_SURVEY_INGEST=false
ALLOW_DEMO_PATIENTS=true
EOF
fi
CLIN_USER="$(grep '^CLINICIAN_USERNAME=' .env | cut -d= -f2-)"
CLIN_PASS="$(grep '^CLINICIAN_PASSWORD=' .env | cut -d= -f2-)"

# ---------- Frontend ----------
log "Setting up frontend"
cd "$ROOT/frontend"
if [ ! -d node_modules ]; then
  npm ci --no-audit --no-fund
fi
if [ ! -f .env.local ]; then
  cp .env.example .env.local
fi

# ---------- Run ----------
for port in 3000 8000; do
  if (command -v lsof >/dev/null 2>&1 && lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1) \
     || (command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ":$port "); then
    die "Port $port is already in use. Stop the other process (e.g. 'lsof -ti tcp:$port | xargs kill') and re-run."
  fi
done

cleanup() {
  log "Stopping servers"
  kill "${BACKEND_PID:-}" "${FRONTEND_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

log "Starting backend on http://localhost:8000"
cd "$ROOT/backend"
.venv/bin/uvicorn main:app --port 8000 --env-file .env &
BACKEND_PID=$!

log "Starting frontend on http://localhost:3000"
cd "$ROOT/frontend"
npm run dev &
FRONTEND_PID=$!

for _ in $(seq 1 60); do
  if curl -fsS http://localhost:8000/api/health >/dev/null 2>&1 \
     && curl -fsS -o /dev/null http://localhost:3000/ >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

# Mint a magic link for the seeded demo patient (data/mock_patients.json).
cd "$ROOT/backend"
set -a; . ./.env; set +a
MAGIC_LINK="$(.venv/bin/python -c 'import patient_access; print(patient_access.create_patient_link("RGN-0417").url)' 2>/dev/null || true)"

cat <<EOF

============================================================
  Sana is running

  App:               http://localhost:3000
  Clinician portal:  http://localhost:3000/doctor
                     username: $CLIN_USER
                     password: $CLIN_PASS
  Demo patient walk (magic link, no sign-in needed):
    ${MAGIC_LINK:-http://localhost:3000/patient/RGN-0417  (sign in at /doctor first)}

  Press Ctrl+C to stop.
============================================================
EOF

wait
