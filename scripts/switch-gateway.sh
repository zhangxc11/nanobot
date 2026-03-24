#!/bin/bash
# switch-gateway.sh — Gateway switch with health check and auto-rollback
#
# Usage: switch-gateway.sh <from-env> <to-env>
#
# This script is designed to run as an independent background process.
# It should be launched via nanobot-svc.sh switch-gw, NOT called directly by agents.
#
# Flow:
#   1. Unset caller identity (NANOBOT_ENV/ROLE/PORT)
#   2. Kill from-env gateway (by port/PID)
#   3. Double-fork start to-env gateway
#   4. Health check: 10s (first) + 3min (second)
#   5. Both pass → success
#   6. Any fail → kill to-env gateway → restart from-env gateway (rollback)

set -euo pipefail

# ─── Caller Check ───────────────────────────────────────────────────────────
# Must be called via nanobot-svc.sh, not directly by agents
if [ "${_NANOBOT_SVC_CALLER:-}" != "1" ]; then
    echo "ERROR: 请通过 nanobot-svc.sh switch-gw 调用，不要直接执行此脚本"
    exit 1
fi

# ─── Constants ──────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_DIR="$HOME/.nanobot/env"
RUN_DIR="$HOME/.nanobot/run"

# Auto-detect Python
if [ -z "${NANOBOT_PYTHON:-}" ]; then
    NANOBOT_BIN=$(which nanobot 2>/dev/null || true)
    if [ -n "$NANOBOT_BIN" ]; then
        NANOBOT_PYTHON="$(dirname "$NANOBOT_BIN")/python3"
    fi
fi
PYTHON="${NANOBOT_PYTHON:-python3}"

# ─── Logging ────────────────────────────────────────────────────────────────

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log_info()  { log "INFO:  $*"; }
log_warn()  { log "WARN:  $*"; }
log_error() { log "ERROR: $*"; }
log_step()  { log "STEP:  $*"; }
log_critical() { log "CRITICAL: $*"; }

# ─── Arguments ──────────────────────────────────────────────────────────────

FROM_ENV="${1:-}"
TO_ENV="${2:-}"

if [ -z "$FROM_ENV" ] || [ -z "$TO_ENV" ]; then
    echo "Usage: $0 <from-env> <to-env>"
    exit 1
fi

if [[ "$FROM_ENV" != "prod" && "$FROM_ENV" != "dev" ]]; then
    log_error "Invalid from-env: $FROM_ENV"
    exit 1
fi

if [[ "$TO_ENV" != "prod" && "$TO_ENV" != "dev" ]]; then
    log_error "Invalid to-env: $TO_ENV"
    exit 1
fi

if [ "$FROM_ENV" = "$TO_ENV" ]; then
    log_error "from-env and to-env cannot be the same"
    exit 1
fi

# ─── Step 1: Unset caller identity ──────────────────────────────────────────

log_step "1. Unsetting caller identity..."
unset NANOBOT_ENV 2>/dev/null || true
unset NANOBOT_ROLE 2>/dev/null || true
unset NANOBOT_PORT 2>/dev/null || true
log_info "Caller identity cleared."

# ─── Helper Functions ───────────────────────────────────────────────────────

load_env() {
    local env="$1"
    local env_file="$ENV_DIR/${env}.env"
    if [ ! -f "$env_file" ]; then
        log_error "Env file not found: $env_file"
        exit 1
    fi
    # shellcheck disable=SC1090
    source "$env_file"
    NANOBOT_CODE="${NANOBOT_CODE/#\~/$HOME}"
    WEBCHAT_CODE="${WEBCHAT_CODE/#\~/$HOME}"
    NANOBOT_LOG_DIR="${NANOBOT_LOG_DIR/#\~/$HOME}"
}

# Find gateway PID by command pattern matching port
find_gateway_pid() {
    local port="$1"
    ps aux | grep -E "(nanobot gateway|nanobot\.gateway\.feishu\.app).*--port $port" \
        | grep -v grep | awk '{print $2}' | head -1 || true
}

# PID file path
pid_file_path() {
    local env="$1" port="$2"
    echo "$RUN_DIR/${env}-gateway-${port}.pid"
}

# Read PID from file (with stale check)
read_pid_file() {
    local pf="$1"
    if [ ! -f "$pf" ]; then
        return 0
    fi
    local pid
    pid=$(cat "$pf" 2>/dev/null | tr -d '[:space:]')
    if [ -z "$pid" ]; then
        rm -f "$pf"
        return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
        rm -f "$pf"
        return 0
    fi
    echo "$pid"
}

# Kill a gateway by env
kill_gateway() {
    local env="$1"
    load_env "$env"
    local port="$GATEWAY_PORT"

    local pid
    pid=$(find_gateway_pid "$port")

    # Also check PID file
    local pf
    pf=$(pid_file_path "$env" "$port")
    local file_pid
    file_pid=$(read_pid_file "$pf")

    # Merge
    local target_pid="${pid:-$file_pid}"

    if [ -z "$target_pid" ]; then
        log_info "${env} gateway not running (port $port)."
        return 0
    fi

    log_step "Killing ${env} gateway (PID: $target_pid, port: $port)..."
    kill "$target_pid" 2>/dev/null || true
    sleep 2

    if kill -0 "$target_pid" 2>/dev/null; then
        log_warn "Process still alive, force killing..."
        kill -9 "$target_pid" 2>/dev/null || true
        sleep 1
    fi

    if kill -0 "$target_pid" 2>/dev/null; then
        log_error "Failed to kill ${env} gateway (PID: $target_pid)"
        return 1
    fi

    rm -f "$pf"
    log_info "${env} gateway killed."
}

# Start a gateway by env (double-fork)
start_gateway() {
    local env="$1"
    load_env "$env"
    local port="$GATEWAY_PORT"
    local nanobot_code="$NANOBOT_CODE"
    local log_dir="$NANOBOT_LOG_DIR"

    mkdir -p "$log_dir" "$RUN_DIR"

    log_step "Starting ${env} gateway on port $port..."

    (
        cd "$nanobot_code"
        PYTHONPATH="${nanobot_code}:${PYTHONPATH:-}" \
        NANOBOT_LOG_DIR="$log_dir" \
        NANOBOT_ENV="$env" \
        NANOBOT_ROLE="gateway" \
        NANOBOT_PORT="$port" \
        nohup "$PYTHON" -m nanobot gateway --port "$port" \
            >> "$log_dir/gateway-start.log" 2>&1 &
        echo $! > "$(pid_file_path "$env" "$port")"
    ) &
    disown

    sleep 3

    # Find the actual gateway PID (the nohup child)
    local new_pid
    new_pid=$(find_gateway_pid "$port")
    if [ -n "$new_pid" ]; then
        echo "$new_pid" > "$(pid_file_path "$env" "$port")"
        log_info "${env} gateway started (PID: $new_pid, port: $port)"
        return 0
    fi

    # Check PID file
    local pf
    pf=$(pid_file_path "$env" "$port")
    local file_pid
    file_pid=$(read_pid_file "$pf")
    if [ -n "$file_pid" ]; then
        log_info "${env} gateway started (PID: $file_pid, port: $port)"
        return 0
    fi

    log_error "${env} gateway failed to start!"
    return 1
}

# Check if gateway is alive
check_gateway_alive() {
    local env="$1"
    load_env "$env"
    local port="$GATEWAY_PORT"

    local pid
    pid=$(find_gateway_pid "$port")
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        echo "$pid"
        return 0
    fi

    # Check PID file
    local pf
    pf=$(pid_file_path "$env" "$port")
    local file_pid
    file_pid=$(read_pid_file "$pf")
    if [ -n "$file_pid" ] && kill -0 "$file_pid" 2>/dev/null; then
        echo "$file_pid"
        return 0
    fi

    return 0
}

# ─── Exclusive Lock ─────────────────────────────────────────────────────────
# Uses mkdir for atomic lock acquisition (works on macOS without flock).
# Lock contains a PID file for stale detection and auto-cleanup via trap.

LOCK_DIR="$RUN_DIR/switch-gateway.lock"
LOCK_PID_FILE="$LOCK_DIR/pid"
LOCK_TIMEOUT=300  # 5 minutes — enough for full switch cycle (~3.5min)

acquire_lock() {
    # Try mkdir (atomic on all POSIX systems)
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        echo "$$" > "$LOCK_PID_FILE"
        log_info "Lock acquired (PID: $$)"
        return 0
    fi

    # Lock dir exists — check if holder is still alive
    if [ -f "$LOCK_PID_FILE" ]; then
        local holder_pid
        holder_pid=$(cat "$LOCK_PID_FILE" 2>/dev/null | tr -d '[:space:]')

        if [ -n "$holder_pid" ]; then
            # Check if holder process is still alive
            if kill -0 "$holder_pid" 2>/dev/null; then
                # Holder alive — check timeout via lock dir mtime
                local lock_age
                # macOS stat: -f %m gives mtime epoch
                local lock_mtime
                lock_mtime=$(stat -f %m "$LOCK_DIR" 2>/dev/null || echo "0")
                local now
                now=$(date +%s)
                lock_age=$(( now - lock_mtime ))

                if [ "$lock_age" -ge "$LOCK_TIMEOUT" ]; then
                    log_warn "Lock held by PID $holder_pid for ${lock_age}s (> ${LOCK_TIMEOUT}s timeout). Force breaking stale lock."
                    rm -rf "$LOCK_DIR"
                    if mkdir "$LOCK_DIR" 2>/dev/null; then
                        echo "$$" > "$LOCK_PID_FILE"
                        log_info "Stale lock broken. Lock acquired (PID: $$)"
                        return 0
                    fi
                    log_error "Failed to acquire lock even after breaking stale lock"
                    return 1
                fi

                log_error "Another switch-gateway is running (PID: $holder_pid, age: ${lock_age}s). Aborting."
                return 1
            else
                # Holder dead — stale lock, clean up
                log_warn "Stale lock from dead PID $holder_pid. Cleaning up."
                rm -rf "$LOCK_DIR"
                if mkdir "$LOCK_DIR" 2>/dev/null; then
                    echo "$$" > "$LOCK_PID_FILE"
                    log_info "Lock acquired after cleanup (PID: $$)"
                    return 0
                fi
                log_error "Failed to acquire lock after cleaning stale lock"
                return 1
            fi
        fi
    fi

    # PID file missing or empty — stale lock dir
    log_warn "Lock dir exists but no valid PID file. Cleaning up."
    rm -rf "$LOCK_DIR"
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        echo "$$" > "$LOCK_PID_FILE"
        log_info "Lock acquired after cleanup (PID: $$)"
        return 0
    fi

    log_error "Failed to acquire lock"
    return 1
}

release_lock() {
    if [ -d "$LOCK_DIR" ] && [ -f "$LOCK_PID_FILE" ]; then
        local holder_pid
        holder_pid=$(cat "$LOCK_PID_FILE" 2>/dev/null | tr -d '[:space:]')
        if [ "$holder_pid" = "$$" ]; then
            rm -rf "$LOCK_DIR"
            log_info "Lock released (PID: $$)"
        fi
    fi
}

# Auto-release lock on exit (normal, error, or signal)
trap release_lock EXIT

if ! acquire_lock; then
    exit 1
fi

# ─── Main Flow ──────────────────────────────────────────────────────────────

log_info "=== Gateway Switch: $FROM_ENV → $TO_ENV ==="
log_info "Started at $(date)"

# Step 2: Kill from-env gateway
log_step "2. Killing ${FROM_ENV} gateway..."
kill_gateway "$FROM_ENV"
sleep 2

# Step 3: Start to-env gateway
log_step "3. Starting ${TO_ENV} gateway..."
if ! start_gateway "$TO_ENV"; then
    log_error "${TO_ENV} gateway failed to start. Rolling back..."
    log_step "ROLLBACK: Starting ${FROM_ENV} gateway..."
    start_gateway "$FROM_ENV" || log_critical "ROLLBACK FAILED! Both gateways are down!"
    exit 1
fi

# Step 4: First health check (10s after start)
log_step "4. First health check (waiting 10s)..."
sleep 10

alive_pid=$(check_gateway_alive "$TO_ENV")
if [ -z "$alive_pid" ]; then
    log_error "First health check FAILED: ${TO_ENV} gateway not alive after 10s"
    log_step "ROLLBACK: Killing ${TO_ENV} gateway remnants..."
    kill_gateway "$TO_ENV" 2>/dev/null || true
    log_step "ROLLBACK: Starting ${FROM_ENV} gateway..."
    start_gateway "$FROM_ENV" || log_critical "ROLLBACK FAILED! Both gateways are down!"
    exit 1
fi
log_info "First health check PASSED (PID: $alive_pid)"

# Step 5: Second health check (3min after start = ~170s more wait)
log_step "5. Second health check (waiting ~170s more, total ~3min from start)..."
sleep 170

alive_pid=$(check_gateway_alive "$TO_ENV")
if [ -z "$alive_pid" ]; then
    log_error "Second health check FAILED: ${TO_ENV} gateway died within 3 minutes"
    log_step "ROLLBACK: Killing ${TO_ENV} gateway remnants..."
    kill_gateway "$TO_ENV" 2>/dev/null || true
    log_step "ROLLBACK: Starting ${FROM_ENV} gateway..."
    start_gateway "$FROM_ENV" || log_critical "ROLLBACK FAILED! Both gateways are down!"
    exit 1
fi
log_info "Second health check PASSED (PID: $alive_pid)"

# Step 6: Success
log_info "=== Gateway switch SUCCESS: $FROM_ENV → $TO_ENV ==="
log_info "Completed at $(date)"
