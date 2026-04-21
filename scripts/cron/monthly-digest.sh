#!/usr/bin/env bash
# Monthly digest cron entrypoint. Runs on 1st of month for the previous month.
# Shares /tmp/archivist-digest.lock with daily/weekly so they serialize.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

LOG_DIR="$PROJECT_ROOT/logs/cron"
LOCK_FILE="/tmp/archivist-digest.lock"
LOG_FILE="$LOG_DIR/monthly-digest-$(date +%F).log"
ARCHIVIST="$PROJECT_ROOT/.venv/bin/archivist"

mkdir -p "$LOG_DIR"
cd "$PROJECT_ROOT"
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"

# Cover the previous calendar month
MONTH_ARG="$(date -d "last month" +%Y-%m)"

{
    echo
    echo "==== $(date -Iseconds) start: monthly-digest month=$MONTH_ARG ===="
} >> "$LOG_FILE"

START=$(date +%s)

# Wait up to 1h for daily/weekly to release the lock
flock -w 3600 -E 75 "$LOCK_FILE" \
    "$ARCHIVIST" digest run-monthly --month "$MONTH_ARG" >> "$LOG_FILE" 2>&1
EXIT=$?

ELAPSED=$(($(date +%s) - START))

{
    echo "==== $(date -Iseconds) end: monthly-digest (exit=$EXIT, ${ELAPSED}s) ===="
} >> "$LOG_FILE"

case $EXIT in
    0)  NOTE="✅ monthly-digest ${MONTH_ARG} 完成 (${ELAPSED}s)" ;;
    75) NOTE="⚠️ monthly-digest 跳过：等锁超时" ;;
    *)  NOTE="❌ monthly-digest ${MONTH_ARG} 失败 exit=${EXIT} (${ELAPSED}s)
日志：${LOG_FILE}" ;;
esac

"$ARCHIVIST" notify --text "$NOTE" >> "$LOG_FILE" 2>&1 || true

exit $EXIT
