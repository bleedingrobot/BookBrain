#!/usr/bin/env bash
# Starts (or restarts) the FlareSolverr sidecar AnnasArchiveProvider falls
# back to when a direct request is blocked by Cloudflare/DDoS-Guard. Run this
# once, or again after bumping the image tag.
#
# Bound to 127.0.0.1 only — FlareSolverr's own README: never expose this
# port to the internet (it's an unauthenticated browser-automation proxy).
#
# Set ANNAS_ARCHIVE_FLARESOLVERR_URL=http://127.0.0.1:8191/v1 in backend/.env
# once this is running, then restart bookbrain.service.
#
# See also flaresolverr-restart.service/.timer (installed separately) — a
# scheduled daily restart, since FlareSolverr has known memory growth over
# long uptimes (community reports ~1.2GB after 24h on some setups).

set -euo pipefail

docker rm -f flaresolverr >/dev/null 2>&1 || true
docker run -d --name flaresolverr \
  -p 127.0.0.1:8191:8191 \
  -e LOG_LEVEL=info \
  --restart unless-stopped \
  ghcr.io/flaresolverr/flaresolverr:latest

echo "flaresolverr started — waiting for it to come up..."
for _ in $(seq 1 15); do
  if curl -sf http://127.0.0.1:8191 >/dev/null 2>&1; then
    echo "flaresolverr is up on http://127.0.0.1:8191"
    exit 0
  fi
  sleep 1
done
echo "flaresolverr didn't respond within 15s — check 'docker logs flaresolverr'" >&2
exit 1
