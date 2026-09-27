#!/usr/bin/env bash
# How much traffic is MovieMood getting? Reads nginx access logs only: no cookies,
# no tracking script, no query text (Gradio sends queries in the request body,
# which nginx doesn't log). See D-031.
#
# Usage (on the server):
#   sudo bash deploy/traffic.sh            # per-day table, last 7 days
#   sudo bash deploy/traffic.sh 30         # last 30 days
#   sudo bash deploy/traffic.sh --report   # GoAccess HTML dashboard -> /tmp/moviemood-report.html
#
# Then view the report on your laptop (it isn't served publicly):
#   scp root@<IP>:/tmp/moviemood-report.html . && open moviemood-report.html
#
# Logs rotate daily and are kept ~14 days by Ubuntu's logrotate, so older days drop off.

set -euo pipefail

LOG_DIR="${LOG_DIR:-/var/log/nginx}"  # overridable for testing

# All access logs, oldest first, plain or gzipped (access.log.2.gz ... access.log)
logs() {
    local f
    for f in $(ls -1 "$LOG_DIR"/access.log* 2>/dev/null | sort -t. -k3,3nr); do
        gzip -dcf "$f"
    done
}

if [[ "${1:-}" == "--report" ]]; then
    command -v goaccess >/dev/null || { echo "goaccess not installed: sudo apt install goaccess"; exit 1; }
    out=/tmp/moviemood-report.html
    logs | goaccess - --log-format=COMBINED --anonymize-ip --no-query-string -o "$out" 2>/dev/null
    echo "Report: $out"
    echo "On your laptop: scp root@<IP>:$out . && open moviemood-report.html"
    exit 0
fi

DAYS="${1:-7}"

logs | awk -v days="$DAYS" '
BEGIN {
    split("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec", m, " ")
    for (i = 1; i <= 12; i++) mon[m[i]] = sprintf("%02d", i)
}
{
    # combined format: IP - - [27/Sep/2026:10:00:00 +0000] "METHOD PATH HTTP/1.1" STATUS ...
    split(substr($4, 2), t, "[/:]")
    day = t[3] "-" mon[t[2]] "-" t[1]
    method = substr($6, 2); path = $7; status = $9
    days_seen[day] = 1

    if (!((day, $1) in ip)) { ip[day, $1] = 1; visitors[day]++ }
    if (method == "GET" && (path == "/" || path ~ /^\/\?/)) pages[day]++
    if (method == "POST" && path ~ /^\/gradio_api\/queue\/join/ && status < 400) joins[day]++
    if (method == "POST" && path == "/api/v1/recommend" && status < 400) api[day]++
    if (status == 429) limited[day]++
}
END {
    n = 0
    for (d in days_seen) list[++n] = d
    # simple sort (few days), newest last
    for (i = 1; i <= n; i++) for (j = i + 1; j <= n; j++) if (list[j] < list[i]) { x = list[i]; list[i] = list[j]; list[j] = x }
    start = (n > days) ? n - days + 1 : 1

    printf "%-10s  %8s  %10s  %12s  %9s  %12s\n", "day", "visitors", "page views", "UI searches", "API calls", "rate-limited"
    for (i = start; i <= n; i++) {
        d = list[i]
        # every UI search joins the Gradio queue 3 times (steps 1-3)
        printf "%-10s  %8d  %10d  %12d  %9d  %12d\n", d, visitors[d], pages[d], int((joins[d] + 2) / 3), api[d], limited[d]
        tv += visitors[d]; tp += pages[d]; tj += joins[d]; ta += api[d]; tl += limited[d]
    }
    printf "%-10s  %8s  %10d  %12d  %9d  %12d\n", "total", "-", tp, int((tj + 2) / 3), ta, tl
    print "\nvisitors = unique IPs that day (includes bots). UI searches are approximate (queue joins / 3)."
}'
