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

mkdir -p "$LOG_DIR"
cd "$PROJECT_ROOT"
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"

{
    echo
    echo "==== $(date -Iseconds) start: daily-digest ===="
} >> "$LOG_FILE"

START=$(date +%s)

flock -n -E 75 "$LOCK_FILE" \
    "$ARCHIVIST" digest run >> "$LOG_FILE" 2>&1
EXIT=$?

ELAPSED=$(($(date +%s) - START))

{
    echo "==== $(date -Iseconds) end: daily-digest (exit=$EXIT, ${ELAPSED}s) ===="
} >> "$LOG_FILE"

case $EXIT in
    0)  NOTE="✅ daily-digest 完成 (${ELAPSED}s)" ;;
    75) NOTE="⚠️ daily-digest 跳过：另一 digest 实例正在运行" ;;
    *)  NOTE="❌ daily-digest 失败 exit=${EXIT} (${ELAPSED}s)
日志：${LOG_FILE}" ;;
esac

"$ARCHIVIST" notify --text "$NOTE" >> "$LOG_FILE" 2>&1 || true

exit $EXIT
