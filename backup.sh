#!/usr/bin/env bash
set -euo pipefail
SRC=/mnt/storage/docker-data/position-sizing-a/sizer.sqlite3
DST=/mnt/storage/backups/position-sizing-a
mountpoint -q /mnt/storage || { echo "storage not mounted, abort"; exit 1; }
[ -f "$SRC" ] || { echo "no db yet at $SRC, abort"; exit 1; }
mkdir -p "$DST"
stamp=$(date +%Y%m%d-%H%M%S)
out="$DST/sizer-$stamp.sqlite3"

# SQLite's own backup API — safe against a concurrent writer, unlike cp/tar
# on a live db file. /mnt/storage has no SMART health, so verify with an
# integrity check rather than trusting the copy blindly.
python3 - "$SRC" "$out" <<'PY'
import sqlite3, sys
src_path, dst_path = sys.argv[1], sys.argv[2]
src = sqlite3.connect(src_path)
dst = sqlite3.connect(dst_path)
with dst:
    src.backup(dst)
result = dst.execute("pragma integrity_check").fetchone()[0]
src.close()
dst.close()
if result != "ok":
    sys.exit(f"integrity check failed: {result}")
PY

gzip "$out"

# keep the 7 most recent, delete older
ls -1t "$DST"/sizer-*.sqlite3.gz | tail -n +8 | xargs -r rm --
echo "backed up -> $out.gz (integrity verified)"
ls -lh "$DST"
