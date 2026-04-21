#!/usr/bin/env bash
# Weekly digest cron entrypoint. Runs on Monday for the previous ISO week.
# Shares /tmp/archivist-digest.lock with daily/monthly so they serialize.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

LOG_DIR="$PROJECT_ROOT/logs/cron"
LOCK_FILE="/tmp/archivist-digest.lock"
LOG_FILE="$LOG_DIR/weekly-digest-$(date +%F).log"
ARCHIVIST="$PROJECT_ROOT/.venv/bin/archivist"

mkdir -p "$LOG_DIR"
cd "$PROJECT_ROOT"
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"

# Cover the previous ISO week
WEEK_ARG="$(date -d "last week" +%G-W%V)"

{
    echo
    echo "==== $(date -Iseconds) start: weekly-digest week=$WEEK_ARG ===="
} >> "$LOG_FILE"

START=$(date +%s)

# Wait up to 1h for daily-digest to release the lock (daily usually < 30 min)
flock -w 3600 -E 75 "$LOCK_FILE" \
    "$ARCHIVIST" digest run-weekly --week "$WEEK_ARG" >> "$LOG_FILE" 2>&1
EXIT=$?

ELAPSED=$(($(date +%s) - START))

{
    echo "==== $(date -Iseconds) end: weekly-digest (exit=$EXIT, ${ELAPSED}s) ===="
} >> "$LOG_FILE"

case $EXIT in
    0)  NOTE="✅ weekly-digest ${WEEK_ARG} 完成 (${ELAPSED}s)" ;;
    75) NOTE="⚠️ weekly-digest 跳过：等锁超时" ;;
    *)  NOTE="❌ weekly-digest ${WEEK_ARG} 失败 exit=${EXIT} (${ELAPSED}s)
日志：${LOG_FILE}" ;;
esac

"$ARCHIVIST" notify --text "$NOTE" >> "$LOG_FILE" 2>&1 || true

exit $EXIT
