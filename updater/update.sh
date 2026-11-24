#!/bin/sh
# Oink updater: a tiny sidecar with the Docker socket that does exactly one thing — when the
# app asks (data/updater/request.json), pull the Oink image and recreate the Oink container.
# The database migrates itself when the new version starts. Optional: without it, the app
# shows the two commands to run by hand.
set -eu
DIR=/data/updater
SERVICE="${OINK_SERVICE:-oink}"
mkdir -p "$DIR"
status() { printf '{"state":"%s","at":%s}\n' "$1" "$(date +%s)" > "$DIR/status.json"; }
while true; do
  date +%s > "$DIR/alive"
  if [ -f "$DIR/request.json" ]; then
    mv "$DIR/request.json" "$DIR/working.json"
    status pulling
    if docker compose pull "$SERVICE" > "$DIR/log.txt" 2>&1 \
      && docker compose up -d --no-deps "$SERVICE" >> "$DIR/log.txt" 2>&1; then
      status done
    else
      status failed
    fi
    rm -f "$DIR/working.json"
  fi
  sleep 5
done
