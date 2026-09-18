#!/usr/bin/env bash
# Exercise dashboard.sh's alert functions against fixtures, without touching
# the live service. Extracts the function block and stubs its inputs.
set -u
SRC=/opt/bookbrain/dashboard/dashboard.sh
# Pull in everything from the colour defs down to the end of render_alerts.
sed -n '/^RESET=/,/^TICK=0/p' "$SRC" > /tmp/alertfns.sh
ALERTS_LOG=$(mktemp); STATUS_FILE=$(mktemp); FAST_REFRESH=30
source /tmp/alertfns.sh
ALERTS_LOG=$(mktemp); : > "$ALERTS_LOG"
ALERTS_STATE=$(mktemp); : > "$ALERTS_STATE"
STATUS_FILE=$(mktemp); touch "$STATUS_FILE"
NIGHTLY_STATUS="success"
PROVIDERS_JSON='{"openbooks":{"dead":false,"window_got":3,"window_attempts":4,"window_hours":6},"libgen":{"dead":false},"torrent":{"dead":false,"window_got":0,"window_attempts":12,"window_hours":6}}'

pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "  ok   $1"; pass=$((pass+1)); else echo "  FAIL $1: got [$2] want [$3]"; fail=$((fail+1)); fi; }

# --- 1. the real 09-18 hang: 857s gap, threshold 300 ---
HANG_LAST=$(date -d '2026-09-18 09:33:44' +%s)
HANG_NOW=$(date -d '2026-09-18 09:48:01' +%s)
journalctl() { echo "$HANG_LAST.000000 fake line"; }
date() { if [ "${1:-}" = "+%s" ]; then echo "$HANG_NOW"; else command date "$@"; fi; }
export -f journalctl date 2>/dev/null || true
compute_alerts
check "09-18 hang (857s) raises backend-silent" "$(printf '%s\n' "${ALERT_KEYS[@]}" | grep -c backend-silent)" "1"
check "  message reports 14m" "$(printf '%s\n' "${ALERT_MSGS[@]}" | grep -o '14m')" "14m"

# --- 2. a routine 31s gap (the measured p99) must NOT fire ---
HANG_LAST=$(( HANG_NOW - 31 ))
compute_alerts
check "routine 31s gap does not fire" "$(printf '%s\n' "${ALERT_KEYS[@]-}" | grep -c backend-silent)" "0"

# --- 3. exactly at the threshold and one second past it ---
HANG_LAST=$(( HANG_NOW - 300 )); compute_alerts
check "exactly 300s does not fire" "$(printf '%s\n' "${ALERT_KEYS[@]-}" | grep -c backend-silent)" "0"
HANG_LAST=$(( HANG_NOW - 301 )); compute_alerts
check "301s fires" "$(printf '%s\n' "${ALERT_KEYS[@]}" | grep -c backend-silent)" "1"

# --- 4. dead provider comes through from PROVIDERS_JSON ---
HANG_LAST=$(( HANG_NOW - 5 ))
PROVIDERS_JSON='{"openbooks":{"dead":true,"window_got":0,"window_attempts":47,"window_hours":6},"libgen":{"dead":false},"torrent":{"dead":false}}'
compute_alerts
check "dead provider raises" "$(printf '%s\n' "${ALERT_MSGS[@]}" | grep -c 'provider-dead: openbooks 0/47 in 6h')" "1"

# --- 5. torrent, never proven, must never raise ---
PROVIDERS_JSON='{"torrent":{"dead":false,"window_got":0,"window_attempts":2210,"window_hours":6}}'
compute_alerts
check "torrent never raises" "$(printf '%s\n' "${ALERT_KEYS[@]-}" | grep -c provider-dead)" "0"

# --- 6. status.json staleness, measured in the reader ---
touch -d '5 minutes ago' "$STATUS_FILE"
date() { command date "$@"; }   # real clock for the mtime comparison
compute_alerts
check "stale status.json raises" "$(printf '%s\n' "${ALERT_KEYS[@]}" | grep -c status-stale)" "1"
touch "$STATUS_FILE"; compute_alerts
check "fresh status.json does not" "$(printf '%s\n' "${ALERT_KEYS[@]-}" | grep -c status-stale)" "0"

# --- 7. nightly promoted only when not success ---
NIGHTLY_STATUS="failed"; compute_alerts
check "failed nightly raises" "$(printf '%s\n' "${ALERT_KEYS[@]}" | grep -c nightly)" "1"
NIGHTLY_STATUS="n/a"; compute_alerts
check "n/a nightly does not" "$(printf '%s\n' "${ALERT_KEYS[@]-}" | grep -c '^nightly$')" "0"
NIGHTLY_STATUS="success"

# --- 8. transitions are logged once, not per tick ---
# Pin the journal timestamp to *now* so backend-silent stays quiet and
# provider-dead is the only alert in play; otherwise this case silently
# measures two transitions instead of one.
: > "$ALERTS_LOG"; : > "$ALERTS_STATE"; PREV_ALERT_KEYS=""
journalctl() { echo "$(command date +%s).000000 fresh line"; }
PROVIDERS_JSON='{"openbooks":{"dead":true,"window_got":0,"window_attempts":47,"window_hours":6}}'
compute_alerts; log_alert_transitions
compute_alerts; log_alert_transitions
compute_alerts; log_alert_transitions
check "3 identical ticks log 1 RAISED" "$(grep -c RAISED "$ALERTS_LOG")" "1"
PROVIDERS_JSON='{"openbooks":{"dead":false}}'
compute_alerts; log_alert_transitions
check "recovery logs CLEARED" "$(grep -c 'CLEARED  provider-dead:openbooks' "$ALERTS_LOG")" "1"
compute_alerts; log_alert_transitions
check "no duplicate CLEARED" "$(grep -c CLEARED "$ALERTS_LOG")" "1"

# --- 9. the traceback alarm ---
# The stub has to dispatch on args: compute_alerts calls journalctl twice,
# once for the last line (silence) and once with --since (tracebacks).
NIGHTLY_STATUS="success"
PROVIDERS_JSON='{"openbooks":{"dead":false}}'
TB_OUT=""
journalctl() {
    for a in "$@"; do [ "$a" = "--since" ] && { printf '%s' "$TB_OUT"; return; }; done
    echo "$(command date +%s).000000 fresh line"
}
TB_OUT=$'some line\nTraceback (most recent call last):\n  File "x.py"\nTraceback (most recent call last):\n'
compute_alerts
check "tracebacks in the hour raise" "$(printf '%s\n' "${ALERT_MSGS[@]}" | grep -c 'tracebacks: 2 in the last hour')" "1"
TB_OUT=""
compute_alerts
check "no tracebacks, no alert" "$(printf '%s\n' "${ALERT_KEYS[@]-}" | grep -c tracebacks)" "0"

# --- 10. a second concurrent instance must not re-raise ---
# bookbrain-dashboard-web.service runs this script under ttyd, one process per
# connected browser, so several instances tick against one alerts.log. This is
# the bug that showed up live: with per-process state, each re-RAISED every
# already-active alert.
: > "$ALERTS_LOG"; : > "$ALERTS_STATE"
PROVIDERS_JSON='{"openbooks":{"dead":true,"window_got":0,"window_attempts":47,"window_hours":6}}'
compute_alerts; log_alert_transitions                  # instance A, first tick
( PREV_ALERT_KEYS=""; compute_alerts; log_alert_transitions )   # instance B, fresh process
( PREV_ALERT_KEYS=""; compute_alerts; log_alert_transitions )   # instance C
check "concurrent instances log 1 RAISED" "$(grep -c RAISED "$ALERTS_LOG")" "1"

# --- 11. a restart does not re-announce an already-active alert ---
( PREV_ALERT_KEYS=""; compute_alerts; log_alert_transitions )
check "restart does not re-raise" "$(grep -c RAISED "$ALERTS_LOG")" "1"

# --- 12. the log self-truncates ---
ALERTS_LOG_MAX_LINES=10
for i in $(seq 1 12); do printf 'filler %s\n' "$i" >> "$ALERTS_LOG"; done
PROVIDERS_JSON='{"openbooks":{"dead":false}}'; compute_alerts; log_alert_transitions
check "log truncates past the cap" "$([ "$(wc -l < "$ALERTS_LOG")" -le 10 ] && echo yes)" "yes"

echo; echo "passed $pass, failed $fail"; [ "$fail" -eq 0 ]
