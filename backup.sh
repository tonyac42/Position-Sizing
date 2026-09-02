#!/usr/bin/env bash
# set -euo pipefail: exits immediately on any error, unset variable, or failed pipeline stage.
set -euo pipefail
# SRC: path to the live database file on the external drive.
SRC=/mnt/storage/docker-data/position-sizing-a/sizer.sqlite3
# DST: directory where rotated backup archives are written.
DST=/mnt/storage/backups/position-sizing-a
# mountpoint check: aborts before touching anything if the external drive isn't mounted.
mountpoint -q /mnt/storage || { echo "storage not mounted, abort"; exit 1; }
# file check: aborts if the app has never written a DB yet, so there's nothing to back up.
[ -f "$SRC" ] || { echo "no db yet at $SRC, abort"; exit 1; }
mkdir -p "$DST"
# stamp: timestamp string embedded in the backup filename for uniqueness/ordering.
stamp=$(date +%Y%m%d-%H%M%S)
# out: full path of this run's backup file, before compression.
out="$DST/sizer-$stamp.sqlite3"

# SQLite's own backup API — safe against a concurrent writer, unlike cp/tar
# on a live db file. /mnt/storage has no SMART health, so verify with an
# integrity check rather than trusting the copy blindly.
python3 - "$SRC" "$out" <<'PY'
import sqlite3, sys
# src_path/dst_path: the source (live) and destination (backup) file paths from argv.
src_path, dst_path = sys.argv[1], sys.argv[2]
src = sqlite3.connect(src_path)
dst = sqlite3.connect(dst_path)
# src.backup(dst): SQLite's built-in online backup API — copies the DB page-by-page
# without corrupting it even if src is being written to concurrently.
with dst:
    src.backup(dst)
# result: the output of `pragma integrity_check`, "ok" if the backup file is structurally sound.
result = dst.execute("pragma integrity_check").fetchone()[0]
src.close()
dst.close()
if result != "ok":
    sys.exit(f"integrity check failed: {result}")
PY

# gzip: compresses the verified backup file in place.
gzip "$out"

# keep the 7 most recent, delete older
ls -1t "$DST"/sizer-*.sqlite3.gz | tail -n +8 | xargs -r rm --
echo "backed up -> $out.gz (integrity verified)"
ls -lh "$DST"
