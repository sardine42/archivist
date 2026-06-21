#!/usr/bin/env bash
# Daily digest cron entrypoint.
# Uses flock to serialize against weekly/monthly runs (shared lock).

set -uo pipefail

# Auto-detect project root from script location (no hardcoded paths)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

LOG_DIR="$PROJECT_ROOT/logs/cron"
LOCK_FILE="/tmp/archivist-digest.lock"
LOG_FILE="$LOG_DIR/daily-digest-$(date +%F).log"
ARCHIVIST="$PROJECT_ROOT/.venv/bin/archivist"
TODAY="$(date +%F)"
SKIP_FILE="$PROJECT_ROOT/.skip-digest-$TODAY"
PENDING_FILE="$PROJECT_ROOT/.digest-pending"

mkdir -p "$LOG_DIR"
cd "$PROJECT_ROOT"
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"

{
    echo
    echo "==== $(date -Iseconds) start: daily-digest ===="
} >> "$LOG_FILE"

# One-off skip: touch .skip-digest-YYYY-MM-DD to defer a day to the next run.
# The skipped date is appended to .digest-pending and replayed via --date next time.
if [ -f "$SKIP_FILE" ]; then
    echo "==== $(date -Iseconds) skip $TODAY (queued for catch-up) ====" >> "$LOG_FILE"
    grep -qxF "$TODAY" "$PENDING_FILE" 2>/dev/null || echo "$TODAY" >> "$PENDING_FILE"
    rm -f "$SKIP_FILE"
    "$ARCHIVIST" notify --text "📅 daily-digest 跳过 $TODAY（已加入补做队列）" >> "$LOG_FILE" 2>&1 || true
    exit 0
fi

START=$(date +%s)

# Capture this run's output separately so we can detect the empty-day marker
# without picking up prior runs that share the same daily log file.
RUN_LOG="$(mktemp)"
trap 'rm -f "$RUN_LOG"' EXIT

# Catch up any dates queued by prior skips before running today's digest.
if [ -s "$PENDING_FILE" ]; then
    while IFS= read -r DEFERRED; do
        [ -z "$DEFERRED" ] && continue
        echo "==== $(date -Iseconds) catch-up --date $DEFERRED ====" >> "$LOG_FILE"
        flock -n -E 75 "$LOCK_FILE" \
            "$ARCHIVIST" digest run --date "$DEFERRED" >> "$LOG_FILE" 2>&1 || true
    done < "$PENDING_FILE"
    rm -f "$PENDING_FILE"
fi

flock -n -E 75 "$LOCK_FILE" \
    "$ARCHIVIST" digest run > "$RUN_LOG" 2>&1
EXIT=$?
cat "$RUN_LOG" >> "$LOG_FILE"

ELAPSED=$(($(date +%s) - START))

{
    echo "==== $(date -Iseconds) end: daily-digest (exit=$EXIT, ${ELAPSED}s) ===="
} >> "$LOG_FILE"

case $EXIT in
    0)  if grep -q "daily digest skipped (empty)" "$RUN_LOG"; then
            NOTE="📭 daily-digest 空日 — 0 候选 (${ELAPSED}s)"
        else
            NOTE="✅ daily-digest 完成 (${ELAPSED}s)"
        fi ;;
    75) NOTE="⚠️ daily-digest 跳过：另一 digest 实例正在运行" ;;
    *)  NOTE="❌ daily-digest 失败 exit=${EXIT} (${ELAPSED}s)
日志：${LOG_FILE}" ;;
esac

"$ARCHIVIST" notify --text "$NOTE" >> "$LOG_FILE" 2>&1 || true

exit $EXIT
