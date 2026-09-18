#!/usr/bin/env bash
# Console status dashboard for tty1. Loops forever, redrawing every FAST_REFRESH
# seconds. A few BookBrain queries are heavier (the acquisition queue is
# thousands of rows), so those are only re-fetched every SLOW_EVERY ticks.
set -u
export TERM=${TERM:-linux}
FAST_REFRESH=30
SLOW_EVERY=2    # 2 * 30s = 60s
API="http://localhost:8000"
STATUS_DIR="/opt/bookbrain/dashboard/mobile"
STATUS_FILE="$STATUS_DIR/status.json"
mkdir -p "$STATUS_DIR"

# ANSI colors -- tty1's default Linux console font handles the basic 16 fine.
RESET=$'\033[0m'
BOLD=$'\033[1m'
DIM=$'\033[2m'
RED=$'\033[31m'
GREEN=$'\033[32m'
YELLOW=$'\033[33m'
CYAN=$'\033[36m'
MAGENTA=$'\033[35m'

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
        RUNNING|ok|responding|success|approved) printf '%s' "$GREEN" ;;
        DOWN|failed|unreachable|unavailable) printf '%s' "$RED" ;;
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

# cline <width> <color> <text> -- <text> colored and padded/truncated to
# exactly <width> visible columns (the padding is plain spaces added after
# the color's reset, so it's never miscounted the way padding *inside* an
# ANSI-colored string would be).
cline() {
    local width=$1 color=$2 text=$3
    text=${text:0:width}
    local padlen=$(( width - ${#text} ))
    (( padlen < 0 )) && padlen=0
    printf '%s%s%s%*s' "$color" "$text" "$RESET" "$padlen" ""
}

provider_label() {  # provider_label <raw provider id> -> short display name
    case "$1" in
        openbooks) echo "OpenBooks" ;;
        annas_archive) echo "Anna's Archive" ;;
        libgen) echo "LibGen" ;;
        torrent) echo "Torrent" ;;
        "" | unknown) echo "unknown" ;;
        *) echo "$1" ;;
    esac
}

dur() {
    # dur <seconds> -> "45m" / "3h12m"
    local s=$1
    if (( s < 3600 )); then printf '%dm' "$(( s/60 ))"
    else printf '%dh%02dm' "$(( s/3600 ))" "$(( (s%3600)/60 ))"
    fi
}

BOX_WIDTH=31

# provider_box <key in $PROVIDERS_JSON> <label> <header-color> <output array name>
# Builds one auto-get process's status panel (counts + last searched/got) as
# an array of BOX_WIDTH-wide colored lines, so three of these can be printed
# side by side -- see the "Acquisition processes" render section below.
provider_box() {
    local key=$1 label=$2 hcolor=$3
    local -n out=$4
    local got queued qstale failed fetching nomatch
    got=$(jq -r ".${key}.got // 0" <<< "$PROVIDERS_JSON")
    queued=$(jq -r ".${key}.queued // 0" <<< "$PROVIDERS_JSON")
    qstale=$(jq -r ".${key}.queued_stale // 0" <<< "$PROVIDERS_JSON")
    failed=$(jq -r ".${key}.failed // 0" <<< "$PROVIDERS_JSON")
    fetching=$(jq -r ".${key}.fetching // 0" <<< "$PROVIDERS_JSON")
    # Searches that came back with nothing, from the event log. Without this
    # line OpenBooks read "got 130  failed 6" -- a 96% success rate -- while
    # sitting on hundreds of searches that found no book at all. `failed` has
    # only ever meant "found it, couldn't fetch it".
    nomatch=$(jq -r "(.${key}.no_match // 0) + (.${key}.unavailable // 0)" <<< "$PROVIDERS_JSON")

    out=()
    out+=("$(cline "$BOX_WIDTH" "${BOLD}${hcolor}" "$label")")
    if [ "$key" = "torrent" ]; then
        out+=("$(cline "$BOX_WIDTH" "$RESET" "got $got  fetching $fetching  failed $failed")")
    else
        out+=("$(cline "$BOX_WIDTH" "$RESET" "got $got  pending $queued  failed $failed")")
    fi
    # "pending 61" of which 28 were last really searched a week ago is not a
    # queue that's about to move; say so rather than implying it is.
    out+=("$(cline "$BOX_WIDTH" "$DIM" "no match $nomatch   $qstale stale")")

    # The windowed rate, right under the lifetime totals -- this is the line
    # that should have moved on 2026-09-17. Red when the backend says the
    # provider is dead (working the queue, getting nowhere, while another
    # provider succeeds) or stalled (enabled, but not even trying). Torrent is
    # deliberately never either: at 4 got / 2210 failed all-time, failing is
    # its normal state and a permanent red line is one nobody reads.
    local whours wgot wattempts wsearches wdead wstalled wcolor wtext
    whours=$(jq -r ".${key}.window_hours // 6" <<< "$PROVIDERS_JSON")
    wgot=$(jq -r ".${key}.window_got // 0" <<< "$PROVIDERS_JSON")
    wattempts=$(jq -r ".${key}.window_attempts // 0" <<< "$PROVIDERS_JSON")
    wsearches=$(jq -r ".${key}.window_searches // 0" <<< "$PROVIDERS_JSON")
    wdead=$(jq -r ".${key}.dead // false" <<< "$PROVIDERS_JSON")
    wstalled=$(jq -r ".${key}.stalled // false" <<< "$PROVIDERS_JSON")
    # Red is reserved for the two flagged conditions; a provider that simply
    # isn't succeeding shouldn't read as healthy either, so 0 successes is
    # yellow rather than green. Torrent lives there permanently and that is
    # honest. "idle" is now dim only when nothing was *flagged* -- silence
    # that the backend calls stalled is the loudest line in the box.
    wtext="${whours}h: ${wgot}/${wattempts}"
    (( wsearches > 0 )) && wtext="$wtext  +${wsearches} nm"
    if [ "$wstalled" = "true" ]; then
        wcolor="${BOLD}${RED}"
        wtext="${whours}h: STALLED, 0 tries"
    elif [ "$wdead" = "true" ]; then wcolor="${BOLD}${RED}"
    elif [ "$wattempts" = "0" ] && [ "$wsearches" = "0" ]; then wcolor="$DIM"
    elif [ "$wgot" = "0" ]; then wcolor="$YELLOW"
    else wcolor="$GREEN"; fi
    out+=("$(cline "$BOX_WIDTH" "$wcolor" "$wtext")")

    # Each section pads to a fixed row count (rather than however many
    # entries exist) so "last searched"/"last got" land on the same row
    # across all three boxes -- the three-column render below relies on
    # that to keep the grid looking like a grid instead of a ragged list.
    out+=("$(cline "$BOX_WIDTH" "$DIM" "last searched")")
    local tsv n=0
    tsv=$(jq -r ".${key}.searched_recent[:3][]? | [.resolved_at, .status, .title] | @tsv" <<< "$PROVIDERS_JSON" 2>/dev/null)
    if [ -n "$tsv" ]; then
        while IFS=$'\t' read -r ts status title; do
            # Short labels because the box is 31 columns and the status is the
            # part worth keeping: "none" = searched, hasn't got it; "no-ans" =
            # the provider itself didn't answer, which is the one to worry
            # about. Colour still comes from the raw status word.
            local slabel
            case "$status" in
                approved) slabel="got" ;;
                failed) slabel="fail" ;;
                no_match) slabel="none" ;;
                unavailable) slabel="no-ans" ;;
                *) slabel="$status" ;;
            esac
            out+=("$(cline "$BOX_WIDTH" "$(status_color "$status")" "  $(ago "$ts") $slabel ${title:0:18}")")
            n=$(( n + 1 ))
        done <<< "$tsv"
    fi
    while (( n < 3 )); do
        out+=("$(cline "$BOX_WIDTH" "$DIM" "  --")")
        n=$(( n + 1 ))
    done

    out+=("$(cline "$BOX_WIDTH" "$DIM" "last got")")
    tsv=$(jq -r ".${key}.got_recent[:2][]? | [.resolved_at, .title] | @tsv" <<< "$PROVIDERS_JSON" 2>/dev/null)
    n=0
    if [ -n "$tsv" ]; then
        while IFS=$'\t' read -r ts title; do
            out+=("$(cline "$BOX_WIDTH" "$GREEN" "  $(ago "$ts") ${title:0:22}")")
            n=$(( n + 1 ))
        done <<< "$tsv"
    fi
    while (( n < 2 )); do
        out+=("$(cline "$BOX_WIDTH" "$DIM" "  --")")
        n=$(( n + 1 ))
    done
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
    # The backend stores/serialises most timestamps as naive UTC (no "Z" or
    # offset) — `date -d` treats a naive string as *local* time, which was a
    # harmless coincidence while the box ran on UTC. Now that it's on
    # Pacific/Auckland (2026-09-15), that silently added a 12h skew to every
    # "Xh ago" on the dashboard. `-u` forces naive input to parse as UTC
    # (timestamps that already carry an explicit offset, e.g. "+00:00",
    # parse the same either way, so this is safe for both formats).
    then=$(date -u -d "$ts" +%s 2>/dev/null) || { echo "n/a"; return; }
    now=$(date +%s)
    diff=$(( now - then ))
    (( diff < 0 )) && diff=0
    if (( diff < 3600 )); then echo "$(( diff/60 ))m ago"
    elif (( diff < 86400 )); then echo "$(( diff/3600 ))h ago"
    else echo "$(( diff/86400 ))d ago"
    fi
}

# --- Alerts (prompts/45) --------------------------------------------------
#
# If BookBrain breaks at 3am, what tells James? Before this: nothing. He found
# out when he noticed books had stopped arriving, which for the 2026-09-17
# OpenBooks outage took 18 hours. The signals mostly existed; the problem is
# that every one of them is pull-based, and tty1 is unwatched at 3am. This
# block is the one red thing, and alerts.log is how it survives the night.
ALERTS_LOG="/opt/bookbrain/dashboard/alerts.log"
ALERTS_STATE="/opt/bookbrain/dashboard/.alerts-state"
ALERTS_LOG_MAX_LINES=2000
SILENCE_ALARM_SECONDS=300
PREV_ALERT_KEYS=""

# compute_alerts -> fills ALERT_KEYS / ALERT_MSGS (parallel arrays)
compute_alerts() {
    ALERT_KEYS=(); ALERT_MSGS=()

    # 1. Backend silent. The 5-minute threshold is measured, not guessed: over
    #    the 7-day journal the p99 inter-line gap is 31s, and the only silences
    #    past 5 minutes were the 09-18 hang (14.3 min) and two install-day gaps.
    #    THIS DEPENDS ON THE UVICORN ACCESS LOG STAYING ON -- see the comment in
    #    backend/app/main.py. BookBrain's own `app.*` stream is 456 lines per
    #    7 days with a routine 16-minute gap, so if someone ever disables the
    #    access log to cut journal volume, this alarm dies silently.
    local last_line_at now_epoch silent_for
    last_line_at=$(journalctl -u bookbrain.service -n 1 -o short-unix 2>/dev/null | cut -d. -f1)
    now_epoch=$(date +%s)
    if [[ "$last_line_at" =~ ^[0-9]+$ ]]; then
        silent_for=$(( now_epoch - last_line_at ))
        if (( silent_for > SILENCE_ALARM_SECONDS )); then
            ALERT_KEYS+=("backend-silent")
            ALERT_MSGS+=("backend-silent: no journal line for $(( silent_for / 60 ))m")
        fi
    fi

    # 2. A provider has stopped working. The backend owns both conditions
    #    (prompts/44) so the tty1 panel, the mobile page and this block agree.
    #
    #    `dead` = it is working the queue and getting nowhere. `stalled` = it
    #    is switched on and not even trying, which is the shape both real
    #    OpenBooks outages actually had and which nothing here could see until
    #    2026-09-18: `dead` needed a download attempt, and a provider that is
    #    down never gets as far as attempting one. The backend's `reason`
    #    carries which kind, so this line doesn't send you to the journal to
    #    find out what "dead" meant this time.
    local flagged
    flagged=$(jq -r 'to_entries[]
                     | select(.value.dead == true or .value.stalled == true)
                     | [(if .value.stalled == true then "stalled" else "dead" end),
                        .key,
                        (.value.reason // "no successes in the window")] | @tsv' \
              <<< "$PROVIDERS_JSON" 2>/dev/null)
    if [ -n "$flagged" ]; then
        while IFS=$'\t' read -r pkind pname preason; do
            [ -z "$pname" ] && continue
            ALERT_KEYS+=("provider-$pkind:$pname")
            ALERT_MSGS+=("provider-$pkind: $pname — $preason")
        done <<< "$flagged"
    fi

    # 3. status.json is stale -- i.e. this script itself has wedged on a curl.
    #    Computed from the file's mtime in the *reader*, deliberately: a writer
    #    that has hung cannot report that it has hung.
    if [ -f "$STATUS_FILE" ]; then
        local age=$(( now_epoch - $(stat -c %Y "$STATUS_FILE") ))
        if (( age > FAST_REFRESH * 3 )); then
            ALERT_KEYS+=("status-stale")
            ALERT_MSGS+=("status-stale: status.json ${age}s old (> $(( FAST_REFRESH * 3 ))s)")
        fi
    fi

    # 4. Tracebacks in the last hour. Cheap, and precise only because prompts/43
    #    gave library errors a real formatter. Expect this to be non-zero on day
    #    one -- there were 202 in the 7 days to 09-18, 32 of them `database is
    #    locked`. If it ends up permanently red, fix the locking or tune the
    #    threshold; do not just delete the alarm.
    local tb
    tb=$(journalctl -u bookbrain.service --since '1 hour ago' --no-pager 2>/dev/null | grep -c 'Traceback')
    if [[ "$tb" =~ ^[0-9]+$ ]] && (( tb > 0 )); then
        ALERT_KEYS+=("tracebacks")
        ALERT_MSGS+=("tracebacks: $tb in the last hour")
    fi

    # 5. Nightly job. Already fetched every tick; just promoted into the block.
    if [ -n "$NIGHTLY_STATUS" ] && [ "$NIGHTLY_STATUS" != "success" ] && [ "$NIGHTLY_STATUS" != "n/a" ]; then
        ALERT_KEYS+=("nightly")
        ALERT_MSGS+=("nightly: last run $NIGHTLY_STATUS")
    fi
}

# Append one line per alert *transition* -- not per tick, which would be 2,880
# lines a day. This is the part that converts "a red thing nobody was awake to
# see" into "at 18:04 OpenBooks stopped, at 07:00 it was still stopped".
#
# The previous tick's active set lives in a FILE, not a shell variable, and the
# whole read-diff-append-write is under flock. That is not over-engineering:
# bookbrain-dashboard-web.service runs this same script under ttyd, one process
# per connected browser, so several instances tick concurrently against one
# alerts.log. With per-process state each of them re-RAISED every already-active
# alert -- observed live, the same "tracebacks" alert logged twice 11s apart.
# Sharing the state file also means a restart doesn't re-announce conditions
# that were already active before it.
log_alert_transitions() {
    local now_iso cur
    now_iso=$(date -Iseconds)
    cur=" ${ALERT_KEYS[*]-} "
    {
        flock 9
        PREV_ALERT_KEYS=" $(cat "$ALERTS_STATE" 2>/dev/null) "
        local key i msg
        for key in ${ALERT_KEYS[@]+"${ALERT_KEYS[@]}"}; do
            [[ "$PREV_ALERT_KEYS" == *" $key "* ]] && continue
            msg="$key"
            for i in "${!ALERT_KEYS[@]}"; do
                [ "${ALERT_KEYS[$i]}" = "$key" ] && msg="${ALERT_MSGS[$i]}"
            done
            printf '%s  RAISED   %s\n' "$now_iso" "$msg" >> "$ALERTS_LOG"
        done
        for key in $PREV_ALERT_KEYS; do
            [[ "$cur" == *" $key "* ]] && continue
            printf '%s  CLEARED  %s\n' "$now_iso" "$key" >> "$ALERTS_LOG"
        done
        printf '%s' "${ALERT_KEYS[*]-}" > "$ALERTS_STATE"

        # One line per transition is slow growth, but not zero growth.
        if [ -f "$ALERTS_LOG" ] && (( $(wc -l < "$ALERTS_LOG") > ALERTS_LOG_MAX_LINES )); then
            tail -n "$(( ALERTS_LOG_MAX_LINES / 2 ))" "$ALERTS_LOG" > "$ALERTS_LOG.tmp" \
                && mv "$ALERTS_LOG.tmp" "$ALERTS_LOG"
        fi
    } 9>>"$ALERTS_STATE.lock"
    PREV_ALERT_KEYS="$cur"
}

render_alerts() {
    local n=${#ALERT_KEYS[@]}
    if (( n == 0 )); then
        printf "  %sALERTS%s  %sno alerts%s\n" "$BOLD" "$RESET" "$GREEN" "$RESET"
    else
        local i
        for i in "${!ALERT_MSGS[@]}"; do
            if (( i == 0 )); then
                printf "  %s%sALERTS%s  %s%s%s\n" "$BOLD" "$RED" "$RESET" "$BOLD$RED" "${ALERT_MSGS[$i]}" "$RESET"
            else
                printf "  %-8s%s%s%s\n" "" "$BOLD$RED" "${ALERT_MSGS[$i]}" "$RESET"
            fi
        done
    fi
    if [ -s "$ALERTS_LOG" ]; then
        local line
        while IFS= read -r line; do
            printf "  %s%-8s%s%s\n" "$DIM" "" "$line" "$RESET"
        done < <(tail -n 3 "$ALERTS_LOG")
    fi
}

TICK=0
Q_UNSEARCHED=0; Q_PENDING=0; Q_APPROVED=0; Q_NOMATCH=0; Q_FAILED=0; Q_TOTAL=0
HIT_PCT="n/a"
ORG_24H="?"; ORG_7D="?"
SEARCHED_JSON="[]"; GOT_JSON="[]"; LAST_GOT_AT=""; SOURCE_JSON="[]"
PROVIDERS_JSON='{"openbooks":{},"libgen":{},"torrent":{}}'
LIBGEN_LINES=(); OPENBOOKS_LINES=()
TAG_ENABLED="?"; TAG_DONE="?"; TAG_PENDING="?"
TAG_CUR_TITLE=""; TAG_CUR_AUTHOR=""; TAG_CUR_STATUS=""; TAG_CUR_DONE=0; TAG_CUR_TOTAL=0
TAG_RECENT_JSON="[]"
TAG_ERR_TITLE=""; TAG_ERR_AUTHOR=""; TAG_ERR_MSG=""; TAG_ERR_AT=""
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
        # Live-measured 2026-09-16: this call takes ~4.3s with a 7k-row
        # wishlist (it does a live Drive API listing under the hood via
        # gather_acquisition_targets, not just a DB read), right up against
        # the old 5s timeout -- an intermittent miss here silently left
        # Q_*/SOURCE_JSON/GOT_JSON on their stale previous values instead of
        # refreshing, which is what made a just-approved source (e.g. a new
        # Libgen download) flicker in and out on the mobile dashboard instead
        # of showing up reliably. 20s gives real headroom as the wishlist
        # grows further.
        REQ=$(curl -s -m 20 "$API/api/acquire/requests" 2>/dev/null)
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
            # `slim` keeps exactly the fields the two readers of status.json
            # actually render -- dashboard.sh's own jq filters and
            # mobile/index.html -- and drops the rest. Each full request row
            # carried an opaque `full` handle plus an `alternatives` array of
            # complete candidate objects (200-character
            # `!Bot Author - [Series 01] - Title (epub).rar` strings, five deep,
            # per provider); the providers block alone was 37KB of a 100KB file
            # rewritten every 30s, and nothing rendered any of it. `candidate`
            # is projected to `provider` alone because the queue's got_recent
            # list is labelled with it (index.html:472).
            SLIM='def slim: {title, author, status, resolved_at,
                             candidate: (if .candidate then {provider: .candidate.provider} else null end)};'
            SEARCHED_JSON=$(echo "$REQ" | jq -c "$SLIM"'
                [.[] | select(.status != "unsearched" and .resolved_at != null)]
                | sort_by(.resolved_at) | reverse | .[:5] | map(slim)' 2>/dev/null)
            [ -z "$SEARCHED_JSON" ] && SEARCHED_JSON="[]"
            GOT_JSON=$(echo "$REQ" | jq -c "$SLIM"'
                [.[] | select(.status == "approved" and .resolved_at != null)]
                | sort_by(.resolved_at) | reverse | .[:5] | map(slim)' 2>/dev/null)
            [ -z "$GOT_JSON" ] && GOT_JSON="[]"
            LAST_GOT_AT=$(echo "$GOT_JSON" | jq -r '.[0].resolved_at // empty' 2>/dev/null)
            # Where got books actually came from -- openbooks / annas_archive /
            # libgen -- counted across every approved request, not just the
            # last 5, so a rarely-used provider still shows up.
            SOURCE_JSON=$(echo "$REQ" | jq -c '
                [.[] | select(.status == "approved") | (.candidate.provider // "unknown")]
                | group_by(.) | map({provider: .[0], count: length}) | sort_by(-.count)' 2>/dev/null)
            [ -z "$SOURCE_JSON" ] && SOURCE_JSON="[]"

            # Per-process view for the "Acquisition processes" panel below --
            # one auto-get cycle's activity (torrent/libgen/openbooks), each
            # attributed by the winning candidate's provider. Only the live
            # `queued`/`fetching` counts survive from here (see below), which
            # is all this is now used for: per-provider *history* comes from
            # the event log, including the no-match counts, which the queue
            # cannot answer for -- a `no_match` row does keep its
            # `candidate.provider` (contrary to an earlier comment here), but
            # only the provider that searched it last.
            #
            # Only `queued`, `queued_stale` and `fetching` survive from this
            # block; the history fields it computes are replaced wholesale by
            # the acquisition-log merge below. They are still computed here so
            # the panel degrades to something rather than nothing if that
            # request fails.
            #
            # `queued_stale` exists because the count on its own reads as a
            # work plan and isn't one: `list_requests` re-ranks stored
            # candidates on every page load, which can put a row back to
            # `pending` without re-touching the provider, so most of
            # OpenBooks' 61 "queued" on 2026-09-18 had last actually been
            # searched days earlier. Labelled `pending` in the panels for the
            # same reason -- it is the queue's own word for this state, and it
            # doesn't promise anything is about to happen.
            #
            # Measured on `searched_at` (the row's `updated_at`), never
            # `resolved_at`: a search that finds candidates *clears*
            # `resolved_at`, so a fresh pending row has none and filtering on
            # it counts the freshest rows as the stalest.
            STALE_BEFORE=$(date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%S)
            PROVIDERS_JSON=$(echo "$REQ" | jq -c --arg stale "$STALE_BEFORE" "$SLIM"'
                def provstats(p):
                    ([.[] | select((.candidate.provider // "") == p)]) as $rows
                    | {
                        queued: ([$rows[] | select(.status=="pending")] | length),
                        queued_stale: ([$rows[] | select(.status=="pending"
                                        and (.searched_at // "") < $stale)] | length),
                        fetching: ([$rows[] | select(.status=="fetching")] | length),
                        got: ([$rows[] | select(.status=="approved")] | length),
                        failed: ([$rows[] | select(.status=="failed")] | length),
                        searched_recent: ([$rows[] | select(.resolved_at != null)] | sort_by(.resolved_at) | reverse | .[:5] | map(slim)),
                        got_recent: ([$rows[] | select(.status=="approved" and .resolved_at != null)] | sort_by(.resolved_at) | reverse | .[:5] | map(slim))
                    };
                {openbooks: provstats("openbooks"), libgen: provstats("libgen"), torrent: provstats("torrent")}
            ' 2>/dev/null)
            [ -z "$PROVIDERS_JSON" ] && PROVIDERS_JSON='{"openbooks":{},"libgen":{},"torrent":{}}'

            # The counts above are lifetime totals, and a monotonic counter can
            # never say "this stopped working two hours ago" -- during the
            # 18-hour OpenBooks outage on 2026-09-17 `got` sat pinned at its
            # lifetime value while Libgen absorbed the load, and no panel on
            # this dashboard moved. /api/acquire/provider-health adds a windowed
            # rate; the backend owns the "dead" definition so the mobile page
            # and the viewer can share it rather than reimplementing it in jq.
            # Merged into a temporary and only swapped in if jq actually
            # succeeded: an unreachable or pre-deploy backend answers this path
            # with the SPA's index.html, which is not JSON, and clobbering
            # PROVIDERS_JSON with the empty output of a failed jq would blank
            # all three boxes. Losing the windowed line is acceptable; losing
            # the panel is not.
            # `got`, `failed` and both recent lists are overwritten from the
            # backend's append-only acquisition log, because the $REQ-derived
            # versions above are structurally wrong: the queue deletes an
            # approved row once the file is organised and hides it once the
            # wishlist item is reconciled, so a success disappears from $REQ
            # within a minute or two of happening. On 2026-09-18 OpenBooks got
            # 6 books in an hour and this panel showed 2. LibGen looked fine
            # through the same bug only because at ~1 book/min something is
            # always still inside the deletion lag.
            #
            # `queued` and `fetching` are deliberately NOT overwritten: those
            # are live queue state, which is exactly what $REQ is right about.
            #
            # `occurred_at` is renamed to `resolved_at` so provider_box's jq
            # and mobile/index.html keep reading the field they already read.
            # `no_match` and `unavailable` outcomes pass through under their
            # own names (both renderers colour them) rather than being
            # flattened into "failed": a provider that can't answer and a book
            # that isn't there are not the same news.
            PHEALTH=$(curl -s -m 3 "$API/api/acquire/provider-health" 2>/dev/null)
            if [ -n "$PHEALTH" ]; then
                MERGED=$(jq -c --argjson h "$PHEALTH" '
                    def status: if . == "got" then "approved" else . end;
                    reduce keys[] as $p (.; .[$p] += {
                        window_hours: $h.window_hours,
                        window_got: ($h.providers[$p].window_got // 0),
                        window_attempts: ($h.providers[$p].window_attempts // 0),
                        window_no_match: ($h.providers[$p].window_no_match // 0),
                        window_unavailable: ($h.providers[$p].window_unavailable // 0),
                        window_searches: ($h.providers[$p].window_searches // 0),
                        window_resolutions: ($h.providers[$p].window_resolutions // 0),
                        dead: ($h.providers[$p].dead // false),
                        stalled: ($h.providers[$p].stalled // false),
                        enabled: ($h.providers[$p].enabled // false),
                        reason: ($h.providers[$p].reason // null),
                        got: ($h.providers[$p].lifetime_got // 0),
                        failed: ($h.providers[$p].lifetime_failed // 0),
                        no_match: ($h.providers[$p].lifetime_no_match // 0),
                        unavailable: ($h.providers[$p].lifetime_unavailable // 0),
                        searched_recent: [($h.providers[$p].searched_recent // [])[]
                                          | {resolved_at: .occurred_at, title, author,
                                             status: (.outcome | status)}],
                        got_recent: [($h.providers[$p].got_recent // [])[]
                                     | {resolved_at: .occurred_at, title, author, status: "approved"}]
                    })' <<< "$PROVIDERS_JSON" 2>/dev/null)
                [ -n "$MERGED" ] && PROVIDERS_JSON="$MERGED"
            fi

            # Torrent box dropped from the display 2026-09-18 (TORRENT_ENABLED=false,
            # see README's "Acquisition providers" section) -- still fetched into
            # PROVIDERS_JSON above so /api/acquire/provider-health data and the
            # alerts logic keep working, just not rendered here.
            provider_box libgen "LIBGEN" "$YELLOW" LIBGEN_LINES
            provider_box openbooks "OPENBOOKS" "$CYAN" OPENBOOKS_LINES
        fi
        TAGGING=$(curl -s -m 3 "$API/api/library/llm-tagging" 2>/dev/null)
        if [ -n "$TAGGING" ]; then
            TAG_ENABLED=$(echo "$TAGGING" | jq -r 'if .enabled and .configured then "RUNNING" elif .enabled then "pending" else "off" end' 2>/dev/null)
            TAG_DONE=$(echo "$TAGGING" | jq -r '.full_done // "?"' 2>/dev/null)
            TAG_PENDING=$(echo "$TAGGING" | jq -r '.full_pending // "?"' 2>/dev/null)

            TAG_CUR_TITLE=$(echo "$TAGGING" | jq -r '.current.title // ""' 2>/dev/null)
            TAG_CUR_AUTHOR=$(echo "$TAGGING" | jq -r '.current.author // ""' 2>/dev/null)
            TAG_CUR_STATUS=$(echo "$TAGGING" | jq -r '.current.status // ""' 2>/dev/null)
            TAG_CUR_DONE=$(echo "$TAGGING" | jq -r '.current.chunks_done // 0' 2>/dev/null)
            TAG_CUR_TOTAL=$(echo "$TAGGING" | jq -r '.current.chunks_total // 0' 2>/dev/null)

            TAG_RECENT_JSON=$(echo "$TAGGING" | jq -c '.recent // []' 2>/dev/null)
            [ -z "$TAG_RECENT_JSON" ] && TAG_RECENT_JSON="[]"

            TAG_ERR_TITLE=$(echo "$TAGGING" | jq -r '.last_error.title // ""' 2>/dev/null)
            TAG_ERR_AUTHOR=$(echo "$TAGGING" | jq -r '.last_error.author // ""' 2>/dev/null)
            TAG_ERR_MSG=$(echo "$TAGGING" | jq -r '.last_error.error // ""' 2>/dev/null)
            TAG_ERR_AT=$(echo "$TAGGING" | jq -r '.last_error.failed_at // ""' 2>/dev/null)
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

    # Computed here, just before the snapshot is written and the screen is
    # redrawn, so the tty1 block, alerts.log and the mobile page all describe
    # the same tick.
    compute_alerts
    log_alert_transitions
    ALERTS_JSON=$(printf '%s\n' ${ALERT_MSGS[@]+"${ALERT_MSGS[@]}"} | jq -R . | jq -sc 'map(select(. != ""))')
    [ -z "$ALERTS_JSON" ] && ALERTS_JSON="[]"

    # --- Write a JSON snapshot for the mobile web dashboard (ttyd only
    # mirrors this tty, it can't reflow the fixed-width layout for a phone
    # screen -- the mobile page polls this file instead). Written atomically
    # so the web server never serves a half-written file.
    jq -n \
        --argjson alerts "$ALERTS_JSON" \
        --arg generated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --arg host "$(hostname)" \
        --arg now "$NOW" \
        --arg uptime "$UPTIME" \
        --arg load "$LOAD" \
        --argjson nproc "$NPROC" \
        --arg lan_ip "${LAN_IP:-n/a}" \
        --arg ts_ip "${TS_IP:-n/a}" \
        --argjson cpu_pct "$CPU_PCT" \
        --argjson mem_pct "$MEM_PCT" \
        --arg mem_used_gb "$MEM_USED_GB" \
        --arg mem_total_gb "$MEM_TOTAL_GB" \
        --argjson disk_pct "$DISK_PCT_NUM" \
        --arg disk_used "$DISK_USED" \
        --arg disk_size "$DISK_SIZE" \
        --arg ssh_line "$SSH_LINE" \
        --argjson vscode_windows "${VSCODE_WINDOWS:-0}" \
        --argjson claude_count "${CLAUDE_COUNT:-0}" \
        --arg claude_durs "$CLAUDE_DURS" \
        --arg activity_line "$ACTIVITY_LINE" \
        --arg bb_state "$BB_STATE" \
        --arg api_health "${HEALTH_OK:-unreachable}" \
        --arg book_count "$BOOK_COUNT" \
        --arg review_n "$REVIEW_N" \
        --arg inbox_n "$INBOX_N" \
        --arg dup_n "$DUP_N" \
        --arg localscan_n "$LOCALSCAN_N" \
        --arg org_24h "$ORG_24H" \
        --arg org_7d "$ORG_7D" \
        --arg spark_line "$SPARK_LINE" \
        --arg tag_enabled "$TAG_ENABLED" \
        --arg tag_done "$TAG_DONE" \
        --arg tag_pending "$TAG_PENDING" \
        --arg tag_cur_title "$TAG_CUR_TITLE" \
        --arg tag_cur_author "$TAG_CUR_AUTHOR" \
        --arg tag_cur_status "$TAG_CUR_STATUS" \
        --argjson tag_cur_done "${TAG_CUR_DONE:-0}" \
        --argjson tag_cur_total "${TAG_CUR_TOTAL:-0}" \
        --argjson tag_recent "$TAG_RECENT_JSON" \
        --arg tag_err_title "$TAG_ERR_TITLE" \
        --arg tag_err_author "$TAG_ERR_AUTHOR" \
        --arg tag_err_msg "$TAG_ERR_MSG" \
        --arg tag_err_at "$TAG_ERR_AT" \
        --argjson q_unsearched "$Q_UNSEARCHED" \
        --argjson q_pending "$Q_PENDING" \
        --argjson q_approved "$Q_APPROVED" \
        --argjson q_nomatch "$Q_NOMATCH" \
        --argjson q_failed "$Q_FAILED" \
        --argjson q_total "$Q_TOTAL" \
        --arg hit_pct "$HIT_PCT" \
        --arg last_got_at "$LAST_GOT_AT" \
        --argjson searched_recent "$SEARCHED_JSON" \
        --argjson got_recent "$GOT_JSON" \
        --argjson sources "$SOURCE_JSON" \
        --argjson providers "$PROVIDERS_JSON" \
        --argjson recently_organized "$(printf '%s' "$RECENT" | jq -c '(.organized // [])[:5]' 2>/dev/null || echo '[]')" \
        --arg nightly_status "$NIGHTLY_STATUS" \
        --arg nightly_finished_at "$NIGHTLY_FINISHED" \
        --arg nightly_summary "$NIGHTLY_SUMMARY" \
        '{
            alerts: $alerts,
            generated_at: $generated_at, host: $host, now: $now, uptime: $uptime,
            load: $load, nproc: $nproc, lan_ip: $lan_ip, ts_ip: $ts_ip,
            cpu_pct: $cpu_pct, mem_pct: $mem_pct, mem_used_gb: $mem_used_gb, mem_total_gb: $mem_total_gb,
            disk_pct: $disk_pct, disk_used: $disk_used, disk_size: $disk_size,
            ssh_line: $ssh_line, vscode_windows: $vscode_windows,
            claude_count: $claude_count, claude_durs: $claude_durs, activity_line: $activity_line,
            bookbrain: {
                service_state: $bb_state, api_health: $api_health, book_count: $book_count,
                reviews: $review_n, inbox: $inbox_n, duplicates: $dup_n, local_scan: $localscan_n,
                organized_24h: $org_24h, organized_7d: $org_7d, spark_line: $spark_line,
                tagging: {
                    enabled: $tag_enabled, done: $tag_done, pending: $tag_pending,
                    current: (if $tag_cur_title != "" then
                        {title: $tag_cur_title, author: $tag_cur_author, status: $tag_cur_status,
                         chunks_done: $tag_cur_done, chunks_total: $tag_cur_total}
                        else null end),
                    recent: $tag_recent,
                    last_error: (if $tag_err_msg != "" then
                        {title: $tag_err_title, author: $tag_err_author, message: $tag_err_msg, at: $tag_err_at}
                        else null end)
                },
                queue: {
                    unsearched: $q_unsearched, pending: $q_pending, approved: $q_approved,
                    no_match: $q_nomatch, failed: $q_failed, total: $q_total, hit_pct: $hit_pct,
                    last_got_at: $last_got_at, searched_recent: $searched_recent, got_recent: $got_recent,
                    sources: $sources
                },
                providers: $providers,
                recently_organized: $recently_organized,
                nightly: {status: $nightly_status, finished_at: $nightly_finished_at, summary: $nightly_summary}
            }
        }' > "$STATUS_FILE.tmp" 2>/dev/null && mv "$STATUS_FILE.tmp" "$STATUS_FILE"

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
    render_alerts
    hr
    # `api:` is a liveness probe and nothing more -- /api/health returns a
    # literal, so it proves the event loop can still serve a request (which is
    # exactly what the 09-18 hang broke) but says nothing about the database,
    # Drive credentials, the scheduler or any provider. Labelled "responding"
    # rather than "ok" so the word stops carrying weight it hasn't earned; the
    # alerts block above is what covers the rest.
    printf "  BookBrain   service: %s%-9s%s api: %s%-12s%s\n" \
        "$(status_color "$BB_STATE")" "$BB_STATE" "$RESET" \
        "$(status_color "${HEALTH_OK:-unreachable}")" \
        "$([ "${HEALTH_OK:-}" = "ok" ] && echo "responding" || echo "${HEALTH_OK:-unreachable}")" "$RESET"
    hr
    printf "  %-18s %-18s %-18s %-18s %-18s\n" \
        "books: $BOOK_COUNT" "reviews: $REVIEW_N" "inbox: $INBOX_N" "duplicates: $DUP_N" "local scan: $LOCALSCAN_N"
    printf "  organized last 24h: %-8s organized last 7d: %-8s  %s%s%s\n" \
        "$ORG_24H" "$ORG_7D" "$CYAN" "${SPARK_LINE:-(warming up)}" "$RESET"
    printf "  LLM tagging: %s%-18s%s tagged: %-6s remaining: %-6s\n" \
        "$(status_color "$TAG_ENABLED")" "$TAG_ENABLED" "$RESET" "$TAG_DONE" "$TAG_PENDING"
    if [ -n "$TAG_CUR_TITLE" ]; then
        tag_cur_pct=0
        (( TAG_CUR_TOTAL > 0 )) && tag_cur_pct=$(( 100 * TAG_CUR_DONE / TAG_CUR_TOTAL ))
        printf "    now %s: %s%.42s%s%s  %s chunk %s/%s %s\n" \
            "$TAG_CUR_STATUS" "$CYAN" "$TAG_CUR_TITLE" "$RESET" \
            "${TAG_CUR_AUTHOR:+ by ${TAG_CUR_AUTHOR:0:22}}" \
            "$(bar "$tag_cur_pct" 20)" "$TAG_CUR_DONE" "$TAG_CUR_TOTAL"
    fi
    if [ -n "$TAG_ERR_MSG" ]; then
        printf "    %slast tagging error%s: %.60s (%s%s, %s)\n" \
            "$RED" "$RESET" "$TAG_ERR_MSG" "$TAG_ERR_TITLE" "${TAG_ERR_AUTHOR:+ by $TAG_ERR_AUTHOR}" "$(ago "$TAG_ERR_AT")"
    fi
    if [ "$(echo "$TAG_RECENT_JSON" | jq 'length' 2>/dev/null)" != "0" ]; then
        echo "$TAG_RECENT_JSON" | jq -r '.[:3][] | [.generated_at, .title, (.author // ""), (.genres // [] | join(", "))] | @tsv' 2>/dev/null |
        while IFS=$'\t' read -r ts title author genres; do
            printf "    %-8s %-32s %-18s %s%-30s%s\n" "$(ago "$ts")" "${title:0:32}" "${author:0:18}" "$DIM" "${genres:0:30}" "$RESET"
        done
    fi
    echo
    echo "  ${BOLD}Acquisition queue${RESET} -- refreshed every ${SLOW_EVERY}x${FAST_REFRESH}s"
    printf "  %s  total %s\n" "$(segbar "$Q_APPROVED" "$Q_PENDING" "$Q_UNSEARCHED" "$Q_NOMATCH" 60)" "$Q_TOTAL"
    printf "  %s# got %-6s%s %s+ queued %-6s%s %s- not searched %-6s%s %s. no match %-6s%s   %sfailed: %-4s%s   hit rate: %s\n" \
        "$GREEN" "$Q_APPROVED" "$RESET" "$YELLOW" "$Q_PENDING" "$RESET" "$DIM" "$Q_UNSEARCHED" "$RESET" \
        "$RED" "$Q_NOMATCH" "$RESET" "$RED" "$Q_FAILED" "$RESET" "$HIT_PCT"
    if [ -n "$LAST_GOT_AT" ]; then
        echo "  Last book downloaded via auto-get: $(ago "$LAST_GOT_AT")"
    else
        echo "  Last book downloaded via auto-get: n/a"
    fi
    if [ "$(echo "$SOURCE_JSON" | jq 'length' 2>/dev/null)" != "0" ]; then
        src_total=$(echo "$SOURCE_JSON" | jq '[.[].count] | add' 2>/dev/null)
        printf "  Sources (all-time gets): "
        echo "$SOURCE_JSON" | jq -r '.[] | [.provider, .count] | @tsv' 2>/dev/null |
        while IFS=$'\t' read -r provider count; do
            pct=0
            (( src_total > 0 )) && pct=$(( 100 * count / src_total ))
            printf "%s%s %s (%s%%)%s  " "$CYAN" "$(provider_label "$provider")" "$count" "$pct" "$RESET"
        done
        printf "\n"
    fi
    echo
    echo "  ${BOLD}Acquisition processes${RESET} -- one box per auto-get source"
    hr
    box_lines=${#LIBGEN_LINES[@]}
    (( ${#OPENBOOKS_LINES[@]} > box_lines )) && box_lines=${#OPENBOOKS_LINES[@]}
    box_blank="$(cline "$BOX_WIDTH" "$RESET" "")"
    for (( bi=0; bi<box_lines; bi++ )); do
        printf "  %s  %s\n" \
            "${LIBGEN_LINES[bi]:-$box_blank}" "${OPENBOOKS_LINES[bi]:-$box_blank}"
    done
    echo
    echo "  ${BOLD}Recently organized${RESET} -- last 5"
    hr
    if [ -n "$RECENT" ] && [ "$(echo "$RECENT" | jq '.organized | length' 2>/dev/null)" != "0" ]; then
        echo "$RECENT" | jq -r '.organized[:5][] | [.organized_at, .title, .author, .series, .series_number, .confidence] | @tsv' 2>/dev/null |
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
