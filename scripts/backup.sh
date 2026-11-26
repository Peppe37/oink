#!/bin/sh
# Consistent backup of the Oink database (safe while Oink is running).
# Usage: scripts/backup.sh [keep]   (run it from the folder with docker-compose.yml; keeps the
# last N copies in ./data/backups, 14 by default). Copy ./data/backups somewhere private.
set -eu
KEEP="${1:-14}"
docker compose exec -T oink python - "$KEEP" <<'PY'
import sqlite3, sys, time
from pathlib import Path

data = Path("/data")
out = data / "backups"
out.mkdir(exist_ok=True)
target = out / time.strftime("oink-%Y%m%d-%H%M%S.db")
source, copy = sqlite3.connect(data / "oink.db"), sqlite3.connect(target)
with copy:
    source.backup(copy)  # SQLite's own backup API: includes what is still in the WAL
copy.close(); source.close()
for old in sorted(out.glob("oink-*.db"))[: -int(sys.argv[1])]:
    old.unlink()
print("backup:", target)
PY
