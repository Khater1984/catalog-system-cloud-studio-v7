#!/usr/bin/env python3
"""Prove that a backup file can really be restored - WITHOUT touching production.
Restores the backup ZIP (from /backup/download or `manage.py backup`) into a scratch SQLite database, checks the table hashes
stored inside the backup, the foreign keys, the integrity rules, and prints the row counts.

  python scripts/restore_drill.py catalog_backup_2026....zip
"""
import os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
tmp = tempfile.mkdtemp(prefix="restore_drill_")
os.environ.update(DATA_DIR=tmp, DATABASE_URL="sqlite:///" + tmp + "/drill.db", STORAGE_BACKEND="local")
import db, dataio, integrity

if len(sys.argv) != 2:
    raise SystemExit(__doc__)
z = open(sys.argv[1], "rb").read()
m = dataio.verify(z); print("backup created", m["created"], "- table hashes OK")
db.init(); c = db.conn()
counts = dataio.restore(c, z)
bad = c.execute("PRAGMA foreign_key_check").fetchall()
print("rows restored:", counts)
print("foreign key violations:", len(bad))
issues = integrity.run(c)
for i in issues: print("integrity:", i["kind"], i["count"], i["label"])
print("RESTORE DRILL OK" if not bad else "RESTORE DRILL FAILED"); sys.exit(1 if bad else 0)
