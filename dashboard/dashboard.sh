#!/usr/bin/env bash
# Console status dashboard for tty1. Loops forever, redrawing every FAST_REFRESH
# seconds. A few BookBrain queries are heavier (the acquisition queue is
# thousands of rows), so those are only re-fetched every SLOW_EVERY ticks.
set -u
export TERM=${TERM:-linux}
FAST_REFRESH=30
SLOW_EVERY=2    # 2 * 30s = 60s
API="http://localhost:8000"

# ANSI colors -- tty1's default Linux console font handles the basic 16 fine.
RESET=$'\033[0m'
BOLD=$'\033[1m'
DIM=$'\033[2m'
RED=$'\033[31m'
GREEN=$'\033[32m'
YELLOW=$'\033[33m'
CYAN=$'\033[36m'

pct_color() {  # pct_color <0-100> -- a "high is bad" gauge (CPU/mem/disk)
    local p=$1
    if (( p >= 85 )); then printf '%s' "$RED"
    elif (( p >= 60 )); then printf '%s' "$YELLOW"
    else printf '%s' "$GREEN"
    fi
}

conf_color() {  # conf_color <0-100> -- a "high is good" gauge (confidence)
    local p=$1
    if (( p >= 85 )); then printf '%s' "$GREEN"
    elif (( p >= 60 )); then printf '%s' "$YELLOW"
    else printf '%s' "$RED"
    fi
}

status_color() {
    case "$1" in
        RUNNING|ok|success|approved) printf '%s' "$GREEN" ;;
        DOWN|failed|unreachable) printf '%s' "$RED" ;;
        pending|no_match|degraded|n/a) printf '%s' "$YELLOW" ;;
        *) printf '%s' "$RESET" ;;
    esac
}

bar() {
    # bar <percent 0-100> <width> [fill-char] [empty-char]
    local pct=$1 width=$2 fillc=${3:-#} emptyc=${4:-.}
    local filled=$(( pct * width / 100 ))
    (( filled > width )) && filled=$width
    (( filled < 0 )) && filled=0
    local empty=$(( width - filled ))
    printf '['
    (( filled > 0 )) && printf "%0.s${fillc}" $(seq 1 $filled)
    (( empty > 0 )) && printf "%0.s${emptyc}" $(seq 1 $empty)
    printf ']'
}

# segmented bar: four counts sharing one width, in order a b c d, using chars # + - .
# Colored to match: got=green, queued=yellow, not-searched=dim, no-match=red.
segbar() {
    local a=$1 b=$2 c=$3 d=$4 width=$5
    local total=$((a+b+c+d))
    if (( total <= 0 )); then bar 0 "$width"; return; fi
    local wa=$(( a*width/total )) wb=$(( b*width/total )) wc=$(( c*width/total ))
    local wd=$(( width - wa - wb - wc ))
    printf '['
    (( wa > 0 )) && printf '%s' "$GREEN" && printf '%0.s#' $(seq 1 $wa)
    (( wb > 0 )) && printf '%s' "$YELLOW" && printf '%0.s+' $(seq 1 $wb)
    (( wc > 0 )) && printf '%s' "$DIM" && printf '%0.s-' $(seq 1 $wc)
    (( wd > 0 )) && printf '%s' "$RED" && printf '%0.s.' $(seq 1 $wd)
    printf '%s]' "$RESET"
}

hr() { printf '%s' "$DIM"; printf '%.0s-' $(seq 1 "${1:-100}"); printf '%s\n' "$RESET"; }

dur() {
    # dur <seconds> -> "45m" / "3h12m"
    local s=$1
    if (( s < 3600 )); then printf '%dm' "$(( s/60 ))"
    else printf '%dh%02dm' "$(( s/3600 ))" "$(( (s%3600)/60 ))"
    fi
}

resolve_peer() {
    # resolve_peer <ip> -> a Tailscale hostname for a 100.x tailnet address,
    # else the address as-is (a plain LAN peer).
    local ip=$1 name=""
    if [[ "$ip" == 100.* ]]; then
        name=$(tailscale status 2>/dev/null | awk -v ip="$ip" '$1==ip{print $2; exit}')
    fi
    [ -n "$name" ] && echo "$name" || echo "$ip"
}

# Known project roots for the "what's being worked on" activity proxy --
# the live BookBrain checkout plus every git repo under ~/projects.
PROJECT_ROOTS=(/opt/bookbrain /home/james/projects/*)

ago() {
    # ago <ISO timestamp> -> "3m ago" / "2h ago" / "5d ago"
    local ts=$1
    [ -z "$ts" ] || [ "$ts" = "null" ] && { echo "n/a"; return; }
    local then now diff
    then=$(date -d "$ts" +%s 2>/dev/null) || { echo "n/a"; return; }
    now=$(date +%s)
    diff=$(( now - then ))
    (( diff < 0 )) && diff=0
    if (( diff < 3600 )); then echo "$(( diff/60 ))m ago"
    elif (( diff < 86400 )); then echo "$(( diff/3600 ))h ago"
    else echo "$(( diff/86400 ))d ago"
    fi
}

TICK=0
Q_UNSEARCHED=0; Q_PENDING=0; Q_APPROVED=0; Q_NOMATCH=0; Q_FAILED=0; Q_TOTAL=0
HIT_PCT="n/a"
ORG_24H="?"; ORG_7D="?"
SEARCHED_JSON="[]"; GOT_JSON="[]"; LAST_GOT_AT=""
SPARK_LINE=""
SSH_LINE="none"; CLAUDE_COUNT=0; CLAUDE_DURS=""; VSCODE_WINDOWS=0; ACTIVITY_LINE="n/a"

while true; do
    NOW=$(date '+%Y-%m-%d %H:%M:%S %Z')
    UPTIME=$(uptime -p 2>/dev/null | sed 's/^up //')
    LOAD=$(cut -d' ' -f1-3 /proc/loadavg)
    NPROC=$(nproc)

    # CPU usage (instantaneous, sampled over 300ms)
    read -r _ u1 n1 s1 i1 w1 _ < <(grep '^cpu ' /proc/stat)
    sleep 0.3
    read -r _ u2 n2 s2 i2 w2 _ < <(grep '^cpu ' /proc/stat)
    idle1=$((i1 + w1)); idle2=$((i2 + w2))
    total1=$((u1+n1+s1+i1+w1)); total2=$((u2+n2+s2+i2+w2))
    dtotal=$((total2-total1)); didle=$((idle2-idle1))
    if (( dtotal > 0 )); then CPU_PCT=$(( (100*(dtotal-didle)) / dtotal )); else CPU_PCT=0; fi

    # Memory
    MEM_TOTAL_KB=$(awk '/MemTotal/{print $2}' /proc/meminfo)
    MEM_AVAIL_KB=$(awk '/MemAvailable/{print $2}' /proc/meminfo)
    MEM_USED_KB=$(( MEM_TOTAL_KB - MEM_AVAIL_KB ))
    MEM_PCT=$(( 100 * MEM_USED_KB / MEM_TOTAL_KB ))
    MEM_USED_GB=$(awk -v k="$MEM_USED_KB" 'BEGIN{printf "%.1f", k/1024/1024}')
    MEM_TOTAL_GB=$(awk -v k="$MEM_TOTAL_KB" 'BEGIN{printf "%.1f", k/1024/1024}')

    # Disk
    read -r _ DISK_SIZE DISK_USED DISK_AVAIL DISK_PCT _ < <(df -h / | tail -1)
    DISK_PCT_NUM=${DISK_PCT%\%}

    # Network
    LAN_IP=$(ip -4 addr show eno1 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1)
    [ -z "$LAN_IP" ] && LAN_IP=$(ip -4 -o addr show scope global 2>/dev/null | grep -v tailscale | awk '{print $4}' | head -1 | cut -d/ -f1)
    TS_IP=$(ip -4 addr show tailscale0 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1)

    # --- Who's connected and what's active: cheap, local, every tick ------
    SSH_LINE=""
    while read -r count ip; do
        [ -z "$ip" ] && continue
        SSH_LINE+="${SSH_LINE:+, }${count}x $(resolve_peer "$ip")"
    done < <(ss -tn state established '( sport = :22 )' 2>/dev/null |
        awk 'NR>1{print $4}' | sed 's/:[0-9]*$//' | sort | uniq -c)
    [ -z "$SSH_LINE" ] && SSH_LINE="none"

    mapfile -t CLAUDE_PIDS < <(pgrep -f 'native-binary/claude' 2>/dev/null)
    CLAUDE_COUNT=${#CLAUDE_PIDS[@]}
    CLAUDE_DURS=""
    for pid in "${CLAUDE_PIDS[@]}"; do
        et=$(ps -o etimes= -p "$pid" 2>/dev/null | tr -d ' ')
        [ -n "$et" ] && CLAUDE_DURS+="${CLAUDE_DURS:+, }$(dur "$et")"
    done

    VSCODE_WINDOWS=$(pgrep -fc 'type=extensionHost' 2>/dev/null)
    [ -z "$VSCODE_WINDOWS" ] && VSCODE_WINDOWS=0

    # Most recently touched file across known project dirs -- a rough proxy
    # for "what's being worked on" since a session's real cwd isn't visible
    # from outside (Claude Code and VS Code both track it internally, not as
    # the OS process cwd).
    ACTIVITY_LINE="n/a"
    best_mtime=0
    for root in "${PROJECT_ROOTS[@]}"; do
        [ -d "$root" ] || continue
        hit=$(find "$root" -type f \
            -not -path '*/.git/*' -not -path '*/node_modules/*' -not -path '*/__pycache__/*' \
            -not -path '*/.venv/*' -not -path '*/venv/*' -not -path '*/dist/*' -not -path '*/build/*' \
            -not -path '*/.pytest_cache/*' -not -path '*/.mypy_cache/*' -not -path '*/.ruff_cache/*' \
            -not -path '*/.cache/*' -not -name '*.db' -not -name '*.db-*' -not -name '*.log' \
            -not -name '*.pyc' -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1)
        [ -z "$hit" ] && continue
        mt=${hit%% *}; mt_int=${mt%.*}
        if (( mt_int > best_mtime )); then
            best_mtime=$mt_int
            ACTIVITY_LINE="$(basename "$root"): $(realpath --relative-to="$root" "${hit#* }" 2>/dev/null || echo "${hit#* }")"
            ACTIVITY_AT=$mt_int
        fi
    done
    if (( best_mtime > 0 )); then
        ACTIVITY_LINE="$ACTIVITY_LINE ($(ago "$(date -d "@$ACTIVITY_AT" -Iseconds)"))"
    fi

    # --- BookBrain: cheap calls, every tick -------------------------------
    if systemctl is-active --quiet bookbrain.service; then BB_STATE="RUNNING"; else BB_STATE="DOWN"; fi
    HEALTH_OK=$(curl -s -m 2 "$API/api/health" 2>/dev/null | jq -r '.status // "unreachable"' 2>/dev/null)
    INBOX_N=$(curl -s -m 2 "$API/api/files?status=inbox" 2>/dev/null | jq 'length' 2>/dev/null); [ -z "$INBOX_N" ] && INBOX_N="?"
    REVIEW_N=$(curl -s -m 2 "$API/api/reviews?status=pending" 2>/dev/null | jq 'length' 2>/dev/null); [ -z "$REVIEW_N" ] && REVIEW_N="?"
    DUP_N=$(curl -s -m 2 "$API/api/duplicates" 2>/dev/null | jq 'length' 2>/dev/null); [ -z "$DUP_N" ] && DUP_N="?"
    LOCALSCAN_N=$(curl -s -m 2 "$API/api/local-scan/pending" 2>/dev/null | jq 'length' 2>/dev/null); [ -z "$LOCALSCAN_N" ] && LOCALSCAN_N="?"

    NIGHTLY=$(curl -s -m 3 "$API/api/jobs/nightly" 2>/dev/null)
    NIGHTLY_STATUS=$(echo "$NIGHTLY" | jq -r '.last_run.status // "n/a"' 2>/dev/null)
    NIGHTLY_FINISHED=$(echo "$NIGHTLY" | jq -r '.last_run.finished_at // ""' 2>/dev/null)
    NIGHTLY_SUMMARY=$(echo "$NIGHTLY" | jq -r '.last_run.summary // ""' 2>/dev/null)
    BOOK_COUNT=$(echo "$NIGHTLY_SUMMARY" | grep -oE 'index: [0-9]+ books' | grep -oE '[0-9]+')
    [ -z "$BOOK_COUNT" ] && BOOK_COUNT="?"

    RECENT=$(curl -s -m 3 "$API/api/library/recently-organized?since=24" 2>/dev/null)
    ORG_24H=$(echo "$RECENT" | jq '.organized | length' 2>/dev/null); [ -z "$ORG_24H" ] && ORG_24H="?"

    # --- BookBrain: heavy call (7k+ row acquisition queue), every SLOW_EVERY ticks ---
    if (( TICK % SLOW_EVERY == 0 )); then
        REQ=$(curl -s -m 5 "$API/api/acquire/requests" 2>/dev/null)
        if [ -n "$REQ" ]; then
            Q_UNSEARCHED=$(echo "$REQ" | jq '[.[] | select(.status=="unsearched")] | length' 2>/dev/null || echo 0)
            Q_PENDING=$(echo "$REQ" | jq '[.[] | select(.status=="pending")] | length' 2>/dev/null || echo 0)
            Q_APPROVED=$(echo "$REQ" | jq '[.[] | select(.status=="approved")] | length' 2>/dev/null || echo 0)
            Q_NOMATCH=$(echo "$REQ" | jq '[.[] | select(.status=="no_match")] | length' 2>/dev/null || echo 0)
            Q_FAILED=$(echo "$REQ" | jq '[.[] | select(.status=="failed")] | length' 2>/dev/null || echo 0)
            Q_TOTAL=$(( Q_UNSEARCHED + Q_PENDING + Q_APPROVED + Q_NOMATCH ))
            if (( Q_APPROVED + Q_NOMATCH > 0 )); then
                HIT_PCT="$(( 100 * Q_APPROVED / (Q_APPROVED + Q_NOMATCH) ))%"
            else
                HIT_PCT="n/a"
            fi
            # Last 5 resolved (any outcome) and last 5 actually downloaded,
            # newest first -- and the timestamp of the most recent download,
            # for the "how long has it been" line.
            SEARCHED_JSON=$(echo "$REQ" | jq -c '
                [.[] | select(.status != "unsearched" and .resolved_at != null)]
                | sort_by(.resolved_at) | reverse | .[:5]' 2>/dev/null)
            [ -z "$SEARCHED_JSON" ] && SEARCHED_JSON="[]"
            GOT_JSON=$(echo "$REQ" | jq -c '
                [.[] | select(.status == "approved" and .resolved_at != null)]
                | sort_by(.resolved_at) | reverse | .[:5]' 2>/dev/null)
            [ -z "$GOT_JSON" ] && GOT_JSON="[]"
            LAST_GOT_AT=$(echo "$GOT_JSON" | jq -r '.[0].resolved_at // empty' 2>/dev/null)
        fi
        RECENT7=$(curl -s -m 5 "$API/api/library/recently-organized?since=168" 2>/dev/null)
        ORG_7D=$(echo "$RECENT7" | jq '.organized | length' 2>/dev/null)
        [ -z "$ORG_7D" ] && ORG_7D="?"

        # 7-day activity sparkline: one bar per day, oldest to newest, height
        # scaled to the busiest day in the window.
        if [ -n "$RECENT7" ]; then
            SPARK_CHARS=(▁ ▂ ▃ ▄ ▅ ▆ ▇ █)
            SPARK_COUNTS=()
            SPARK_MAX=0
            for i in 6 5 4 3 2 1 0; do
                day=$(date -d "-$i days" +%Y-%m-%d)
                c=$(echo "$RECENT7" | jq --arg d "$day" \
                    '[.organized[] | select(.organized_at | startswith($d))] | length' 2>/dev/null)
                [ -z "$c" ] && c=0
                SPARK_COUNTS+=("$c")
                (( c > SPARK_MAX )) && SPARK_MAX=$c
            done
            SPARK_LINE=""
            for c in "${SPARK_COUNTS[@]}"; do
                if (( SPARK_MAX == 0 )); then
                    idx=0
                else
                    idx=$(( c * 7 / SPARK_MAX ))
                    (( idx > 7 )) && idx=7
                fi
                SPARK_LINE+="${SPARK_CHARS[$idx]}"
            done
        fi
    fi
    TICK=$(( TICK + 1 ))

    clear
    echo "  ${BOLD}${CYAN}$(hostname)${RESET}  --  server dashboard  --  $NOW"
    hr
    printf "  Uptime: %-28s Load avg: %-16s Cores: %s\n" "$UPTIME" "$LOAD" "$NPROC"
    printf "  LAN:    %-28s Tailscale: %s\n" "${LAN_IP:-n/a}" "${TS_IP:-n/a}"
    echo
    echo "  ${BOLD}Connected & working${RESET}"
    printf "  SSH: %-30s VS Code windows: %-3s Claude Code: %s%s%s\n" \
        "$SSH_LINE" "$VSCODE_WINDOWS" "$CYAN" "${CLAUDE_COUNT:-0} running${CLAUDE_DURS:+ ($CLAUDE_DURS)}" "$RESET"
    printf "  Most recent activity: %s\n" "$ACTIVITY_LINE"
    echo
    printf "  CPU     %s%3d%%%s %s\n" "$(pct_color "$CPU_PCT")" "$CPU_PCT" "$RESET" "$(bar "$CPU_PCT" 50)"
    printf "  Memory  %s%3d%%%s %s  %sG / %sG\n" "$(pct_color "$MEM_PCT")" "$MEM_PCT" "$RESET" "$(bar "$MEM_PCT" 50)" "$MEM_USED_GB" "$MEM_TOTAL_GB"
    printf "  Disk /  %s%3s%s  %s  %s / %s\n" "$(pct_color "$DISK_PCT_NUM")" "$DISK_PCT" "$RESET" "$(bar "$DISK_PCT_NUM" 50)" "$DISK_USED" "$DISK_SIZE"
    echo
    hr
    printf "  BookBrain   service: %s%-9s%s api: %s%-12s%s\n" \
        "$(status_color "$BB_STATE")" "$BB_STATE" "$RESET" \
        "$(status_color "${HEALTH_OK:-unreachable}")" "${HEALTH_OK:-unreachable}" "$RESET"
    hr
    printf "  %-18s %-18s %-18s %-18s %-18s\n" \
        "books: $BOOK_COUNT" "reviews: $REVIEW_N" "inbox: $INBOX_N" "duplicates: $DUP_N" "local scan: $LOCALSCAN_N"
    printf "  organized last 24h: %-8s organized last 7d: %-8s  %s%s%s\n" \
        "$ORG_24H" "$ORG_7D" "$CYAN" "${SPARK_LINE:-(warming up)}" "$RESET"
    echo
    echo "  ${BOLD}Acquisition queue${RESET} (OpenBooks) -- refreshed every ${SLOW_EVERY}x${FAST_REFRESH}s"
    printf "  %s  total %s\n" "$(segbar "$Q_APPROVED" "$Q_PENDING" "$Q_UNSEARCHED" "$Q_NOMATCH" 60)" "$Q_TOTAL"
    printf "  %s# got %-6s%s %s+ queued %-6s%s %s- not searched %-6s%s %s. no match %-6s%s   %sfailed: %-4s%s   hit rate: %s\n" \
        "$GREEN" "$Q_APPROVED" "$RESET" "$YELLOW" "$Q_PENDING" "$RESET" "$DIM" "$Q_UNSEARCHED" "$RESET" \
        "$RED" "$Q_NOMATCH" "$RESET" "$RED" "$Q_FAILED" "$RESET" "$HIT_PCT"
    if [ -n "$LAST_GOT_AT" ]; then
        echo "  Last book downloaded via auto-get: $(ago "$LAST_GOT_AT")"
    else
        echo "  Last book downloaded via auto-get: n/a"
    fi
    echo
    echo "  ${BOLD}Last 5 searched${RESET}"
    hr
    if [ "$(echo "$SEARCHED_JSON" | jq 'length' 2>/dev/null)" != "0" ]; then
        echo "$SEARCHED_JSON" | jq -r '.[] | [.resolved_at, .status, .title, (.author // "")] | @tsv' 2>/dev/null |
        while IFS=$'\t' read -r ts status title author; do
            printf "  %-8s %s%-10s%s %-42s %-22s\n" "$(ago "$ts")" "$(status_color "$status")" "$status" "$RESET" "${title:0:42}" "${author:0:22}"
        done
    else
        echo "  nothing searched yet"
    fi
    echo
    echo "  ${BOLD}Last 5 got${RESET}"
    hr
    if [ "$(echo "$GOT_JSON" | jq 'length' 2>/dev/null)" != "0" ]; then
        echo "$GOT_JSON" | jq -r '.[] | [.resolved_at, .title, (.author // ""), (.candidate.server // "")] | @tsv' 2>/dev/null |
        while IFS=$'\t' read -r ts title author server; do
            printf "  %-8s %s%-42s%s %-22s %s\n" "$(ago "$ts")" "$GREEN" "${title:0:42}" "$RESET" "${author:0:22}" "$server"
        done
    else
        echo "  nothing downloaded yet"
    fi
    echo
    echo "  ${BOLD}Recently organized${RESET}"
    hr
    if [ -n "$RECENT" ] && [ "$(echo "$RECENT" | jq '.organized | length' 2>/dev/null)" != "0" ]; then
        echo "$RECENT" | jq -r '.organized[:6][] | [.organized_at, .title, .author, .series, .series_number, .confidence] | @tsv' 2>/dev/null |
        while IFS=$'\t' read -r ts title author series seriesnum conf; do
            when=$(ago "$ts")
            seriesbit=""
            [ -n "$series" ] && [ "$series" != "null" ] && seriesbit=" ($series #${seriesnum%.0})"
            if [ -z "$conf" ]; then
                printf "  %-8s %-45s %-25s %s%4s%s\n" "$when" "${title:0:45}${seriesbit}" "${author:0:25}" "$DIM" "  ?" "$RESET"
            else
                printf "  %-8s %-45s %-25s %s%3s%%%s\n" "$when" "${title:0:45}${seriesbit}" "${author:0:25}" "$(conf_color "$conf")" "$conf" "$RESET"
            fi
        done
    else
        echo "  nothing organized in the last 24h"
    fi
    echo
    hr
    echo "  Last nightly job: $(status_color "$NIGHTLY_STATUS")${NIGHTLY_STATUS}${RESET} -- finished $(ago "$NIGHTLY_FINISHED")"
    echo
    hr
    echo "  refreshing every ${FAST_REFRESH}s -- ssh in for a real shell"

    sleep "$FAST_REFRESH"
done
