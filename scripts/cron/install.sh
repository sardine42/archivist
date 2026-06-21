#!/usr/bin/env bash
# install.sh — one-command interface to arm/disarm the system-cron digest jobs.
#
#   scripts/cron/install.sh             # install / refresh (idempotent)
#   scripts/cron/install.sh --uninstall # remove just the archivist block
#   scripts/cron/install.sh --show      # print the installed block
#
# Substitutes the project root into crontab.txt and writes the jobs into the
# user's crontab inside a marked block, leaving any other crontab entries
# untouched. Re-running replaces the block (idempotent). No hardcoded paths.

set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TEMPLATE="$SCRIPT_DIR/crontab.txt"

BEGIN="# >>> archivist digest jobs (managed by install.sh) >>>"
END="# <<< archivist digest jobs <<<"

# current crontab minus our managed block (@ delimiter: markers contain '#')
current_without_block() { crontab -l 2>/dev/null | sed "\\@$BEGIN@,\\@$END@d"; }
show_block()            { crontab -l 2>/dev/null | sed -n "\\@$BEGIN@,\\@$END@p"; }

case "${1:-install}" in
    --uninstall|uninstall)
        current_without_block | crontab -
        echo "✅ removed archivist cron block (other jobs preserved)"
        ;;
    --show|show|-l)
        out="$(show_block)"
        [ -n "$out" ] && printf '%s\n' "$out" || echo "(archivist cron block not installed)"
        ;;
    install|"")
        [ -f "$TEMPLATE" ] || { echo "❌ template not found: $TEMPLATE" >&2; exit 1; }
        block="$(sed "s#/path/to/archivist#$PROJECT_ROOT#g" "$TEMPLATE")"
        {
            current_without_block
            echo "$BEGIN"
            printf '%s\n' "$block"
            echo "$END"
        } | crontab -
        echo "✅ installed archivist cron jobs (root: $PROJECT_ROOT)"
        if [ "$(uname)" = "Darwin" ]; then
            echo "ℹ️  macOS notes:"
            echo "   - BSD cron ignores CRON_TZ; jobs run in the system timezone."
            echo "     current: $(date '+%Z %z') — ensure this is Beijing time, or shift the hours."
            echo "   - /usr/sbin/cron needs Full Disk Access (System Settings ▸ Privacy & Security)."
        fi
        echo "verify: crontab -l"
        ;;
    *)
        echo "usage: $0 [install|--uninstall|--show]" >&2
        exit 2
        ;;
esac
