#!/bin/bash
# nanobot-svc.sh — Unified nanobot service management (prod + dev)
#
# Usage:
#   nanobot-svc.sh status <env> [role]           # Show status (PID + port + alive check)
#   nanobot-svc.sh start <env> <role|all>         # Start service(s)
#   nanobot-svc.sh stop <env> <role|all>          # Safely stop service(s)
#   nanobot-svc.sh restart <env> <role|all>       # stop + start
#   nanobot-svc.sh switch-gw <from-env> <to-env>  # Switch gateway (background script)
#   nanobot-svc.sh cron-lock-status               # Show cron lock holder
#   nanobot-svc.sh cron-lock-transfer <to-env>    # Transfer cron lock to target env
#
# Environments: prod, dev
# Roles: gateway, worker, webserver, all

set -euo pipefail

# ─── Constants ──────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_DIR="$HOME/.nanobot/env"
RUN_DIR="$HOME/.nanobot/run"
CRON_LOCK="$HOME/.nanobot/cron/scheduler.lock"

# Auto-detect Python from nanobot venv
if [ -z "${NANOBOT_PYTHON:-}" ]; then
    NANOBOT_BIN=$(which nanobot 2>/dev/null || true)
    if [ -n "$NANOBOT_BIN" ]; then
        NANOBOT_PYTHON="$(dirname "$NANOBOT_BIN")/python3"
    fi
fi
PYTHON="${NANOBOT_PYTHON:-python3}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ─── Utility Functions ──────────────────────────────────────────────────────

SVC_LOG="$HOME/.nanobot/logs/nanobot-svc.log"
mkdir -p "$(dirname "$SVC_LOG")"

# Persistent audit log (appended to file with timestamp)
audit_log() {
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$ts] [PID:$$] $*" >> "$SVC_LOG"
}

log_info()  { echo -e "${GREEN}✅${NC} $*"; audit_log "INFO: $*"; }
log_warn()  { echo -e "${YELLOW}⚠️${NC}  $*"; audit_log "WARN: $*"; }
log_error() { echo -e "${RED}❌${NC} $*"; audit_log "ERROR: $*"; }
log_step()  { echo -e "${BLUE}▶${NC}  $*"; audit_log "STEP: $*"; }

# Validate environment name
validate_env() {
    local env="$1"
    if [[ "$env" != "prod" && "$env" != "dev" ]]; then
        log_error "Invalid environment: $env (must be 'prod' or 'dev')"
        exit 1
    fi
}

# Validate role name
validate_role() {
    local role="$1"
    if [[ "$role" != "gateway" && "$role" != "worker" && "$role" != "webserver" && "$role" != "all" ]]; then
        log_error "Invalid role: $role (must be 'gateway', 'worker', 'webserver', or 'all')"
        exit 1
    fi
}

# Load environment file and expand ~ in paths
# Sets: NANOBOT_ENV, WEBSERVER_PORT, WORKER_PORT, GATEWAY_PORT, NANOBOT_CODE, WEBCHAT_CODE, NANOBOT_LOG_DIR
load_env() {
    local env="$1"
    local env_file="$ENV_DIR/${env}.env"
    if [ ! -f "$env_file" ]; then
        log_error "Env file not found: $env_file"
        exit 1
    fi
    # Source the env file
    # shellcheck disable=SC1090
    source "$env_file"
    # Expand ~ in paths
    NANOBOT_CODE="${NANOBOT_CODE/#\~/$HOME}"
    WEBCHAT_CODE="${WEBCHAT_CODE/#\~/$HOME}"
    NANOBOT_LOG_DIR="${NANOBOT_LOG_DIR/#\~/$HOME}"
}

# Get port for a given role (requires env loaded)
get_port_for_role() {
    local role="$1"
    case "$role" in
        gateway)   echo "$GATEWAY_PORT" ;;
        worker)    echo "$WORKER_PORT" ;;
        webserver) echo "$WEBSERVER_PORT" ;;
    esac
}

# ─── PID File Management ───────────────────────────────────────────────────

# PID file path: ~/.nanobot/run/{env}-{role}-{port}.pid
pid_file_path() {
    local env="$1" role="$2" port="$3"
    echo "$RUN_DIR/${env}-${role}-${port}.pid"
}

# Write PID to file
write_pid_file() {
    local env="$1" role="$2" port="$3" pid="$4"
    mkdir -p "$RUN_DIR"
    local pf
    pf=$(pid_file_path "$env" "$role" "$port")
    echo "$pid" > "$pf"
}

# Read PID from file, returns empty if stale or missing
read_pid_file() {
    local env="$1" role="$2" port="$3"
    local pf
    pf=$(pid_file_path "$env" "$role" "$port")
    if [ ! -f "$pf" ]; then
        return 0
    fi
    local pid
    pid=$(cat "$pf" 2>/dev/null | tr -d '[:space:]')
    if [ -z "$pid" ]; then
        rm -f "$pf"
        return 0
    fi
    # Check if process is alive
    if ! kill -0 "$pid" 2>/dev/null; then
        rm -f "$pf"
        return 0
    fi
    echo "$pid"
}

# Remove PID file
remove_pid_file() {
    local env="$1" role="$2" port="$3"
    local pf
    pf=$(pid_file_path "$env" "$role" "$port")
    rm -f "$pf"
}

# ─── Process Discovery ─────────────────────────────────────────────────────

# Find PID listening on a specific port
find_pid_on_port() {
    local port="$1"
    lsof -ti :"$port" -sTCP:LISTEN 2>/dev/null | head -1 || true
}

# Get process age in human-readable format
get_process_age() {
    local pid="$1"
    ps -o etime= -p "$pid" 2>/dev/null | xargs || echo "?"
}

# Get process age in seconds (macOS compatible)
# Parses ps etime format: [[dd-]hh:]mm:ss
get_process_age_seconds() {
    local pid="$1"
    local etime
    etime=$(ps -o etime= -p "$pid" 2>/dev/null | xargs) || return 1
    [ -z "$etime" ] && return 1

    local days=0 hours=0 minutes=0 seconds=0
    etime="${etime// /}"

    if [[ "$etime" == *-* ]]; then
        days="${etime%%-*}"
        etime="${etime#*-}"
    fi

    IFS=':' read -ra parts <<< "$etime"
    local n=${#parts[@]}
    if [ "$n" -eq 3 ]; then
        hours=$((10#${parts[0]}))
        minutes=$((10#${parts[1]}))
        seconds=$((10#${parts[2]}))
    elif [ "$n" -eq 2 ]; then
        minutes=$((10#${parts[0]}))
        seconds=$((10#${parts[1]}))
    elif [ "$n" -eq 1 ]; then
        seconds=$((10#${parts[0]}))
    fi

    echo $(( days*86400 + hours*3600 + minutes*60 + seconds ))
}

# Get process command (truncated)
get_process_cmd() {
    local pid="$1"
    ps -o command= -p "$pid" 2>/dev/null | head -c 100 || echo "?"
}

# Check if a process is alive and matches expected role
# For gateway: the process may not listen on a port (WebSocket client), so check by PID file + ps
# For worker/webserver: check by port
check_process_alive() {
    local env="$1" role="$2"
    load_env "$env"
    local port
    port=$(get_port_for_role "$role")

    if [ "$role" = "gateway" ]; then
        # Gateway doesn't listen on a port (it's a WebSocket client)
        # Check PID file first, then fallback to ps grep
        local pid
        pid=$(read_pid_file "$env" "$role" "$port")
        if [ -n "$pid" ]; then
            echo "$pid"
            return 0
        fi
        # Fallback: search by command pattern
        local pattern
        if [ "$env" = "prod" ]; then
            # prod gateway: nanobot gateway --port 18790 or nanobot.gateway.feishu.app --app ST --port 18790
            pid=$(ps aux | grep -E "(nanobot gateway|nanobot\.gateway\.feishu\.app).*--port $port" | grep -v grep | awk '{print $2}' | head -1 || true)
        else
            # dev gateway: python3 -m nanobot.gateway.feishu.app --app ST --port 18791 or nanobot gateway --port 18791
            pid=$(ps aux | grep -E "(nanobot gateway|nanobot\.gateway\.feishu\.app).*--port $port" | grep -v grep | awk '{print $2}' | head -1 || true)
        fi
        if [ -n "$pid" ]; then
            # Update PID file
            write_pid_file "$env" "$role" "$port" "$pid"
            echo "$pid"
        fi
    else
        # Worker/webserver: find by port
        local pid
        pid=$(find_pid_on_port "$port")
        if [ -n "$pid" ]; then
            # Update PID file
            write_pid_file "$env" "$role" "$port" "$pid"
            echo "$pid"
        else
            # Port not occupied, check PID file for stale
            read_pid_file "$env" "$role" "$port"
        fi
    fi
}

# ─── Status Command ────────────────────────────────────────────────────────

cmd_status() {
    local env="${1:-}"
    local role="${2:-}"

    if [ -z "$env" ]; then
        echo "Usage: $0 status <env> [role]"
        exit 1
    fi
    validate_env "$env"
    if [ -n "$role" ]; then
        validate_role "$role"
    fi

    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}  nanobot ${env} Environment Status${NC}"
    echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"
    echo ""

    local roles
    if [ -n "$role" ] && [ "$role" != "all" ]; then
        roles="$role"
    else
        roles="gateway worker webserver"
    fi

    load_env "$env"

    for r in $roles; do
        local port
        port=$(get_port_for_role "$r")
        local pid
        pid=$(check_process_alive "$env" "$r")

        if [ -n "$pid" ]; then
            local age cmd
            age=$(get_process_age "$pid")
            cmd=$(get_process_cmd "$pid")
            echo -e "  ${r}:$(printf '%*s' $((12 - ${#r})) '') ${GREEN}✅ running${NC}"
            echo -e "    PID: $pid | Port: $port | Uptime: $age"
            echo -e "    CMD: $cmd"
        else
            echo -e "  ${r}:$(printf '%*s' $((12 - ${#r})) '') ${RED}❌ stopped${NC} (port: $port)"
        fi
        echo ""
    done
}

# ─── Service Health Verification ────────────────────────────────────────────

# Max age in seconds for a process to be considered "newly started"
NEW_PROCESS_MAX_AGE=30

# Verify service health via HTTP endpoint + process age check.
# Gateway: skip (no HTTP endpoint, PID-only check is sufficient).
# Worker/Webserver: curl health endpoint, then verify process is new (not stale).
verify_service_health() {
    local role="$1" port="$2" max_wait="${3:-15}"

    # Gateway doesn't listen on TCP; PID alive check is already done by caller
    if [ "$role" = "gateway" ]; then
        return 0
    fi

    local health_url
    case "$role" in
        webserver) health_url="http://127.0.0.1:${port}/api/health" ;;
        worker)    health_url="http://127.0.0.1:${port}/health" ;;
        *)         return 0 ;;
    esac

    local i=0
    while [ "$i" -lt "$max_wait" ]; do
        if curl -sf "$health_url" > /dev/null 2>&1; then
            # Health endpoint responded — verify it's a NEW process (not stale)
            local pid_on_port
            pid_on_port=$(find_pid_on_port "$port")
            if [ -n "$pid_on_port" ]; then
                local first_pid
                first_pid=$(echo "$pid_on_port" | head -1)
                local age
                age=$(get_process_age_seconds "$first_pid" 2>/dev/null || echo "unknown")
                local age_human
                age_human=$(get_process_age "$first_pid")

                if [ "$age" = "unknown" ]; then
                    log_info "$role health OK (PID: $first_pid, port: $port, age: unknown)"
                    return 0
                elif [ "$age" -le "$NEW_PROCESS_MAX_AGE" ]; then
                    log_info "$role health OK (PID: $first_pid, port: $port, age: ${age}s)"
                    return 0
                else
                    log_error "$role port $port responds but process is STALE (PID: $first_pid, age: ${age_human})"
                    log_error "The new process likely failed to start (port already occupied by old process)."
                    log_error "Run '$0 stop' first, then retry."
                    return 1
                fi
            fi
            # No PID found on port but curl succeeded — unlikely but accept
            log_info "$role health OK (port: $port)"
            return 0
        fi
        sleep 1
        i=$((i + 1))
    done

    log_warn "$role health check failed after ${max_wait}s (port $port) — service may still be starting"
    return 1
}

# ─── Start Command ─────────────────────────────────────────────────────────

do_start_role() {
    local env="$1" role="$2"
    load_env "$env"
    local port
    port=$(get_port_for_role "$role")

    # Check if already running
    local existing_pid
    existing_pid=$(check_process_alive "$env" "$role")
    if [ -n "$existing_pid" ]; then
        log_warn "${env} ${role} already running (PID: $existing_pid, port: $port). Use 'restart' to restart."
        return 0
    fi

    # Gateway mutual exclusion: only one gateway can run at a time (they compete for Feishu messages)
    if [ "$role" = "gateway" ]; then
        local other_env
        if [ "$env" = "prod" ]; then other_env="dev"; else other_env="prod"; fi
        local other_gw_pid
        other_gw_pid=$(check_process_alive "$other_env" "gateway")
        if [ -n "$other_gw_pid" ]; then
            log_error "REFUSED: Cannot start ${env} gateway — ${other_env} gateway is running (PID: $other_gw_pid)!"
            log_error "Stop ${other_env} gateway first: $0 stop ${other_env} gateway"
            audit_log "REFUSED: start ${env} gateway blocked by ${other_env} gateway (PID: $other_gw_pid)"
            return 1
        fi
    fi

    # Check port is free (for worker/webserver)
    if [ "$role" != "gateway" ]; then
        local port_pid
        port_pid=$(find_pid_on_port "$port")
        if [ -n "$port_pid" ]; then
            log_error "Port $port needed for ${env} ${role} is occupied by PID $port_pid"
            echo "         CMD: $(get_process_cmd "$port_pid")"
            return 1
        fi
    fi

    # Verify code exists
    case "$role" in
        gateway)
            if [ ! -d "$NANOBOT_CODE/nanobot" ]; then
                log_error "Nanobot code not found at $NANOBOT_CODE"
                return 1
            fi
            ;;
        worker|webserver)
            if [ ! -f "$WEBCHAT_CODE/${role}.py" ]; then
                log_error "${role}.py not found at $WEBCHAT_CODE"
                return 1
            fi
            ;;
    esac

    # Ensure log dir exists
    mkdir -p "$NANOBOT_LOG_DIR"

    log_step "Starting ${env} ${role} on port $port..."

    local log_dir="$NANOBOT_LOG_DIR"
    local nanobot_code="$NANOBOT_CODE"
    local webchat_code="$WEBCHAT_CODE"
    local target_env="$env"

    case "$role" in
        webserver)
            local worker_url="http://127.0.0.1:${WORKER_PORT}"
            (
                cd "$webchat_code"
                PYTHONPATH="${nanobot_code}:${PYTHONPATH:-}" \
                NANOBOT_LOG_DIR="$log_dir" \
                NANOBOT_ENV="$target_env" \
                NANOBOT_ROLE="webserver" \
                NANOBOT_PORT="$port" \
                nohup "$PYTHON" webserver.py --port "$port" --worker-url "$worker_url" --daemonize \
                    >> "$log_dir/webserver-start.log" 2>&1 &
            ) &
            disown
            ;;
        worker)
            (
                cd "$webchat_code"
                PYTHONPATH="${nanobot_code}:${PYTHONPATH:-}" \
                NANOBOT_LOG_DIR="$log_dir" \
                NANOBOT_ENV="$target_env" \
                NANOBOT_ROLE="worker" \
                NANOBOT_PORT="$port" \
                nohup "$PYTHON" worker.py --port "$port" --daemonize \
                    >> "$log_dir/worker-start.log" 2>&1 &
            ) &
            disown
            ;;
        gateway)
            (
                cd "$nanobot_code"
                PYTHONPATH="${nanobot_code}:${PYTHONPATH:-}" \
                NANOBOT_LOG_DIR="$log_dir" \
                NANOBOT_ENV="$target_env" \
                NANOBOT_ROLE="gateway" \
                NANOBOT_PORT="$port" \
                nohup "$PYTHON" -m nanobot gateway --port "$port" \
                    >> "$log_dir/gateway-start.log" 2>&1 &
            ) &
            disown
            ;;
    esac

    # Wait and verify startup
    local max_wait=8
    local waited=0
    local new_pid=""

    while [ "$waited" -lt "$max_wait" ]; do
        sleep 1
        waited=$((waited + 1))
        new_pid=$(check_process_alive "$env" "$role")
        if [ -n "$new_pid" ]; then
            break
        fi
    done

    if [ -n "$new_pid" ]; then
        write_pid_file "$env" "$role" "$port" "$new_pid"
        log_info "${env} ${role} started (PID: $new_pid, port: $port)"

        # HTTP health check + process age verification (worker/webserver only)
        if ! verify_service_health "$role" "$port" 15; then
            log_warn "${env} ${role} process is alive but health verification failed"
        fi

        # Check for startup errors in logs
        local log_file=""
        case "$role" in
            gateway)   log_file="$log_dir/gateway-start.log" ;;
            worker)    log_file="$log_dir/worker-start.log" ;;
            webserver) log_file="$log_dir/webserver-start.log" ;;
        esac
        if [ -f "$log_file" ]; then
            local errors
            errors=$(tail -20 "$log_file" 2>/dev/null | grep -i "error\|traceback\|exception\|fatal" | head -3 || true)
            if [ -n "$errors" ]; then
                log_warn "Possible startup errors detected in $log_file:"
                echo "$errors" | sed 's/^/         /'
            fi
        fi
    else
        log_error "${env} ${role} failed to start!"
        local log_file=""
        case "$role" in
            gateway)   log_file="$log_dir/gateway-start.log" ;;
            worker)    log_file="$log_dir/worker-start.log" ;;
            webserver) log_file="$log_dir/webserver-start.log" ;;
        esac
        if [ -f "$log_file" ]; then
            echo "         Last log lines:"
            tail -5 "$log_file" 2>/dev/null | sed 's/^/         /'
        fi
        return 1
    fi
}

cmd_start() {
    local env="${1:-}"
    local role="${2:-}"

    if [ -z "$env" ] || [ -z "$role" ]; then
        echo "Usage: $0 start <env> <role|all>"
        exit 1
    fi
    validate_env "$env"
    validate_role "$role"

    echo ""
    echo -e "${BOLD}Starting ${env} services...${NC}"
    echo ""

    if [ "$role" = "all" ]; then
        do_start_role "$env" "worker"
        do_start_role "$env" "webserver"
        do_start_role "$env" "gateway"
    else
        do_start_role "$env" "$role"
    fi
    echo ""
}

# ─── Stop Command ──────────────────────────────────────────────────────────

do_stop_role() {
    local env="$1" role="$2"
    load_env "$env"
    local port
    port=$(get_port_for_role "$role")

    # Self-kill protection: if this script is running inside a nanobot process,
    # refuse to kill our own process
    if [ -n "${NANOBOT_PORT:-}" ] && [ "$port" = "$NANOBOT_PORT" ]; then
        log_error "REFUSED: Cannot stop ${env} ${role} (port $port) — this is our own process!"
        log_error "Self-kill protection triggered. NANOBOT_PORT=$NANOBOT_PORT matches target port."
        return 1
    fi

    local target_pid=""

    if [ "$role" = "gateway" ]; then
        # Gateway: find by PID file or ps pattern
        target_pid=$(check_process_alive "$env" "$role")
    else
        # Worker/webserver: find by port
        target_pid=$(find_pid_on_port "$port")
        if [ -z "$target_pid" ]; then
            # Fallback to PID file
            target_pid=$(read_pid_file "$env" "$role" "$port")
        fi
    fi

    if [ -z "$target_pid" ]; then
        echo "  ${env} ${role} not running (port $port)."
        remove_pid_file "$env" "$role" "$port"
        return 0
    fi

    log_step "Stopping ${env} ${role} (PID: $target_pid, port: $port)..."

    # Audit: record what we're about to kill
    local target_cmd
    target_cmd=$(ps -p "$target_pid" -o command= 2>/dev/null || echo "<already dead>")
    audit_log "KILL: about to kill PID=$target_pid env=$env role=$role port=$port cmd=[$target_cmd]"

    # Graceful kill
    kill "$target_pid" 2>/dev/null || true
    sleep 1

    # Check if still alive
    if kill -0 "$target_pid" 2>/dev/null; then
        log_warn "Process still alive, force killing..."
        kill -9 "$target_pid" 2>/dev/null || true
        sleep 0.5
    fi

    # Verify
    if kill -0 "$target_pid" 2>/dev/null; then
        log_error "Failed to stop ${env} ${role} (PID: $target_pid)"
        return 1
    fi

    remove_pid_file "$env" "$role" "$port"
    log_info "${env} ${role} stopped."
}

cmd_stop() {
    local env="${1:-}"
    local role="${2:-}"

    if [ -z "$env" ] || [ -z "$role" ]; then
        echo "Usage: $0 stop <env> <role|all>"
        exit 1
    fi
    validate_env "$env"
    validate_role "$role"

    echo ""
    echo -e "${BOLD}Stopping ${env} services...${NC}"
    echo ""

    if [ "$role" = "all" ]; then
        do_stop_role "$env" "gateway"
        do_stop_role "$env" "webserver"
        do_stop_role "$env" "worker"
    else
        do_stop_role "$env" "$role"
    fi
    echo ""
}

# ─── Restart Command ───────────────────────────────────────────────────────

cmd_restart() {
    local env="${1:-}"
    local role="${2:-}"

    if [ -z "$env" ] || [ -z "$role" ]; then
        echo "Usage: $0 restart <env> <role|all>"
        exit 1
    fi
    validate_env "$env"
    validate_role "$role"

    echo ""
    echo -e "${BOLD}Restarting ${env} services...${NC}"
    echo ""

    if [ "$role" = "all" ]; then
        # Gateway mutual exclusion pre-check for restart all
        local other_env
        if [ "$env" = "prod" ]; then other_env="dev"; else other_env="prod"; fi
        local other_gw_pid
        other_gw_pid=$(check_process_alive "$other_env" "gateway")
        if [ -n "$other_gw_pid" ]; then
            log_error "REFUSED: Cannot restart ${env} all — ${other_env} gateway is running (PID: $other_gw_pid)!"
            log_error "Gateway mutual exclusion: only one gateway can run at a time."
            log_error "Stop ${other_env} gateway first: $0 stop ${other_env} gateway"
            return 1
        fi
        do_stop_role "$env" "gateway"
        do_stop_role "$env" "webserver"
        do_stop_role "$env" "worker"
        sleep 1
        do_start_role "$env" "worker"
        do_start_role "$env" "webserver"
        do_start_role "$env" "gateway"
    else
        # Gateway mutual exclusion pre-check for restart gateway
        if [ "$role" = "gateway" ]; then
            local other_env
            if [ "$env" = "prod" ]; then other_env="dev"; else other_env="prod"; fi
            local other_gw_pid
            other_gw_pid=$(check_process_alive "$other_env" "gateway")
            if [ -n "$other_gw_pid" ]; then
                log_error "REFUSED: Cannot restart ${env} gateway — ${other_env} gateway is running (PID: $other_gw_pid)!"
                log_error "Stop ${other_env} gateway first: $0 stop ${other_env} gateway"
                return 1
            fi
        fi
        do_stop_role "$env" "$role"
        sleep 1
        do_start_role "$env" "$role"
    fi
    echo ""
}

# ─── Switch Gateway Command ────────────────────────────────────────────────

cmd_switch_gw() {
    local from_env="${1:-}"
    local to_env="${2:-}"

    if [ -z "$from_env" ] || [ -z "$to_env" ]; then
        echo "Usage: $0 switch-gw <from-env> <to-env>"
        exit 1
    fi
    validate_env "$from_env"
    validate_env "$to_env"

    if [ "$from_env" = "$to_env" ]; then
        log_error "from-env and to-env cannot be the same"
        exit 1
    fi

    local switch_script="$SCRIPT_DIR/switch-gateway.sh"
    if [ ! -x "$switch_script" ]; then
        log_error "switch-gateway.sh not found or not executable at $switch_script"
        exit 1
    fi

    # Fast-fail: check if another switch is already running
    local lock_dir="$RUN_DIR/switch-gateway.lock"
    if [ -d "$lock_dir" ]; then
        local holder_pid
        holder_pid=$(cat "$lock_dir/pid" 2>/dev/null | tr -d '[:space:]')
        if [ -n "$holder_pid" ] && kill -0 "$holder_pid" 2>/dev/null; then
            local lock_mtime now lock_age
            lock_mtime=$(stat -f %m "$lock_dir" 2>/dev/null || echo "0")
            now=$(date +%s)
            lock_age=$(( now - lock_mtime ))
            log_error "Another switch-gateway is already running (PID: $holder_pid, age: ${lock_age}s). Aborting."
            exit 1
        fi
    fi

    log_step "Launching gateway switch: $from_env → $to_env (background)..."

    # Launch switch-gateway.sh in background via nohup + disown
    local switch_log="$HOME/.nanobot/logs/switch-gateway-$(date +%Y%m%d-%H%M%S).log"
    (
        export _NANOBOT_SVC_CALLER=1
        nohup "$switch_script" "$from_env" "$to_env" >> "$switch_log" 2>&1 &
    ) &
    disown

    log_info "Switch script launched in background."
    echo "         Log: $switch_log"
    echo "         Monitor: tail -f $switch_log"
}

# ─── Cron Lock Commands ────────────────────────────────────────────────────

cmd_cron_lock_status() {
    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}  Cron Lock Status${NC}"
    echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"
    echo ""

    if [ ! -f "$CRON_LOCK" ]; then
        echo "  Lock file: $CRON_LOCK"
        echo -e "  Status: ${YELLOW}No lock file${NC}"
        echo ""
        return 0
    fi

    echo "  Lock file: $CRON_LOCK"

    # Check if lock is held using Python fcntl
    local lock_held
    lock_held=$("$PYTHON" -c "
import fcntl, os, sys
fd = os.open('$CRON_LOCK', os.O_RDONLY)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    fcntl.flock(fd, fcntl.LOCK_UN)
    print('no')
except (IOError, OSError):
    print('yes')
finally:
    os.close(fd)
" 2>/dev/null || echo "error")

    if [ "$lock_held" = "no" ]; then
        echo -e "  Status: ${YELLOW}Lock file exists but NOT held (no exclusive lock)${NC}"
        echo ""
        return 0
    elif [ "$lock_held" = "error" ]; then
        echo -e "  Status: ${RED}Error checking lock${NC}"
        echo ""
        return 1
    fi

    # Lock is held — find out by whom using lsof
    echo -e "  Status: ${GREEN}Lock IS held (exclusive)${NC}"
    echo ""

    # Get all PIDs that have the lock file open
    local lock_pids
    lock_pids=$(lsof "$CRON_LOCK" 2>/dev/null | tail -n +2 | awk '{print $2}' | sort -u || true)

    if [ -z "$lock_pids" ]; then
        echo -e "  Holder: ${YELLOW}Unknown (lsof returned no results)${NC}"
        echo ""
        return 0
    fi

    echo "  Processes with lock file open:"
    echo ""

    for pid in $lock_pids; do
        local cmd age
        cmd=$(get_process_cmd "$pid")
        age=$(get_process_age "$pid")

        # Determine which env this belongs to
        local env_label="unknown"
        local port_info=""

        # Check if this PID is on known ports
        for env_name in prod dev; do
            load_env "$env_name"
            local wp="$WORKER_PORT"
            local wsp="$WEBSERVER_PORT"
            local gp="$GATEWAY_PORT"

            local pid_on_worker pid_on_webserver
            pid_on_worker=$(find_pid_on_port "$wp" || true)
            pid_on_webserver=$(find_pid_on_port "$wsp" || true)

            if [ "$pid" = "$pid_on_worker" ]; then
                env_label="$env_name"
                port_info="worker:$wp"
                break
            elif [ "$pid" = "$pid_on_webserver" ]; then
                env_label="$env_name"
                port_info="webserver:$wsp"
                break
            fi

            # Check gateway by pattern
            local gw_pid
            gw_pid=$(ps aux | grep -E "(nanobot gateway|nanobot\.gateway\.feishu\.app).*--port $gp" | grep -v grep | awk '{print $2}' | head -1 || true)
            if [ "$pid" = "$gw_pid" ]; then
                env_label="$env_name"
                port_info="gateway:$gp"
                break
            fi
        done

        if [ "$env_label" != "unknown" ]; then
            echo -e "    PID $pid → ${CYAN}${env_label}${NC} ${port_info} (uptime: $age)"
        else
            echo -e "    PID $pid → ${YELLOW}${env_label}${NC} (uptime: $age)"
        fi
        echo "      CMD: $cmd"
    done
    echo ""
}

cmd_cron_lock_transfer() {
    local to_env="${1:-}"

    if [ -z "$to_env" ]; then
        echo "Usage: $0 cron-lock-transfer <to-env>"
        exit 1
    fi
    validate_env "$to_env"

    # Determine current holder
    local from_env=""
    if [ "$to_env" = "prod" ]; then
        from_env="dev"
    else
        from_env="prod"
    fi

    echo ""
    echo -e "${BOLD}Transferring cron lock to ${to_env}...${NC}"
    echo ""

    # Step 1: Stop the current holder's worker
    log_step "Step 1: Stopping ${from_env} worker (current lock holder)..."
    do_stop_role "$from_env" "worker"
    sleep 2

    # Step 2: Ensure target worker is running
    log_step "Step 2: Ensuring ${to_env} worker is running..."
    local to_pid
    load_env "$to_env"
    to_pid=$(check_process_alive "$to_env" "worker")
    if [ -z "$to_pid" ]; then
        log_step "Starting ${to_env} worker..."
        do_start_role "$to_env" "worker"
        sleep 2
    else
        log_info "${to_env} worker already running (PID: $to_pid)"
    fi

    # Step 3: Wait for target worker to acquire lock
    log_step "Step 3: Waiting for ${to_env} worker to acquire cron lock (max 60s)..."
    local max_wait=60
    local waited=0
    local lock_acquired=false

    while [ "$waited" -lt "$max_wait" ]; do
        # Check if lock is held by target env's worker
        load_env "$to_env"
        local wp="$WORKER_PORT"
        local target_worker_pid
        target_worker_pid=$(find_pid_on_port "$wp" || true)

        if [ -n "$target_worker_pid" ]; then
            # Check if this PID has the lock file open
            local has_lock
            has_lock=$(lsof "$CRON_LOCK" 2>/dev/null | awk '{print $2}' | grep -w "$target_worker_pid" || true)
            if [ -n "$has_lock" ]; then
                # Verify lock is actually held (exclusive)
                local lock_held
                lock_held=$("$PYTHON" -c "
import fcntl, os
fd = os.open('$CRON_LOCK', os.O_RDONLY)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    fcntl.flock(fd, fcntl.LOCK_UN)
    print('no')
except (IOError, OSError):
    print('yes')
finally:
    os.close(fd)
" 2>/dev/null || echo "error")
                if [ "$lock_held" = "yes" ]; then
                    lock_acquired=true
                    break
                fi
            fi
        fi

        sleep 2
        waited=$((waited + 2))
        if [ $((waited % 10)) -eq 0 ]; then
            echo "         Waited ${waited}s..."
        fi
    done

    if [ "$lock_acquired" = true ]; then
        log_info "Cron lock acquired by ${to_env} worker!"
    else
        log_error "Timeout: ${to_env} worker did not acquire cron lock in ${max_wait}s"
        log_step "Rolling back: restarting ${from_env} worker..."
        do_start_role "$from_env" "worker"
        return 1
    fi

    # Step 4: Restart the from-env worker (it will run without lock, watchdog will retry)
    log_step "Step 4: Restarting ${from_env} worker (without cron lock)..."
    do_start_role "$from_env" "worker"

    echo ""
    log_info "Cron lock successfully transferred to ${to_env}!"
    echo ""
}

# ─── Main ───────────────────────────────────────────────────────────────────

action="${1:-help}"
shift || true

audit_log "=== nanobot-svc.sh $action $* ==="

case "$action" in
    status)
        cmd_status "$@"
        ;;
    start)
        cmd_start "$@"
        ;;
    stop)
        cmd_stop "$@"
        ;;
    restart)
        cmd_restart "$@"
        ;;
    switch-gw)
        cmd_switch_gw "$@"
        ;;
    cron-lock-status)
        cmd_cron_lock_status
        ;;
    cron-lock-transfer)
        cmd_cron_lock_transfer "$@"
        ;;
    help|--help|-h)
        echo ""
        echo -e "${BOLD}nanobot-svc.sh — Unified nanobot service management${NC}"
        echo ""
        echo "Usage:"
        echo "  $0 status <env> [role]              Show status (PID + port + alive)"
        echo "  $0 start <env> <role|all>            Start service(s)"
        echo "  $0 stop <env> <role|all>             Safely stop service(s)"
        echo "  $0 restart <env> <role|all>          stop + start"
        echo "  $0 switch-gw <from-env> <to-env>     Switch gateway (background)"
        echo "  $0 cron-lock-status                  Show cron lock holder"
        echo "  $0 cron-lock-transfer <to-env>       Transfer cron lock"
        echo ""
        echo "Environments: prod, dev"
        echo "Roles: gateway, worker, webserver, all"
        echo ""
        ;;
    *)
        log_error "Unknown action: $action"
        echo "  Run '$0 help' for usage."
        exit 1
        ;;
esac
