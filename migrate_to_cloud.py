#!/usr/bin/env python3
"""Move an existing local installation (data/catalog.db + data/images + data/uploads + data/profiles + data/fonts)
into the central database and file storage.

  python migrate_to_cloud.py --source-dir data --dest "$DATABASE_URL" [--reuse-drive-ids] [--dry-run]

 * The source is never modified (the SQLite file is copied first).
 * All ids (families, products, models, specs, images, links ...) are kept; sequences continue after the maximum id.
 * Dangling references are reported (migration_report.json), never silently dropped: optional references become NULL,
   rows whose parent is gone (specs of a missing model ...) are listed with their ids before being skipped.
 * Everything in the database is written in ONE transaction. Files (Google Drive) are uploaded afterwards and the script can be
   re-run with --files-only; already uploaded images are skipped.
 * At the end the result is verified (counts, ids, row contents, every image has a stored file) and a report is written.
Set STORAGE_BACKEND=drive plus the Google variables (see README_CLOUD_AR.md) to upload files to Drive.
"""
import os, sys, json, shutil, sqlite3, tempfile, argparse, hashlib, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# table -> [(column, parent table, nullable)]
REFS = {
    "models": [("catalog_id", "catalogs", True), ("family_id", "families", True), ("product_id", "products", True)],
    "products": [("family_id", "families", True)],
    "families": [("parent_id", "families", True)],
    "catalogs": [("previous_catalog_id", "catalogs", True)],
    "images": [("catalog_id", "catalogs", True)],
    "review": [("catalog_id", "catalogs", True)],
    "specs": [("model_id", "models", False), ("product_id", "products", True)],   # empty model_id = product-level spec (legal); a non-empty one must exist
    "image_links": [("image_id", "images", False), ("model_id", "models", False)],
    "product_image_links": [("image_id", "images", False), ("product_id", "products", False)],
    "profile_versions": [("profile_id", "profiles", False)],
    "export_versions": [("profile_id", "profiles", True)],
    "spec_revisions": [("product_id", "products", True), ("variant_id", "models", True), ("specification_id", "specs", False)],
    "drive_files": [("image_id", "images", False)],
}


def read_source(source_dir):
    path = os.path.join(source_dir, "catalog.db")
    if not os.path.exists(path):
        raise SystemExit(f"{path} not found")
    tmp = os.path.join(tempfile.mkdtemp(), "source.db"); shutil.copy2(path, tmp)
    for ext in ("-wal", "-shm"):
        if os.path.exists(path + ext):
            shutil.copy2(path + ext, tmp + ext)
    s = sqlite3.connect(tmp); s.row_factory = sqlite3.Row
    import db
    out = {}
    have = {r["name"] for r in s.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in db.TABLE_ORDER:
        if t in have:
            out[t] = [dict(r) for r in s.execute(f"SELECT * FROM {t}")]
    s.close()
    return out


def transform(tables, report):
    """Fix what the old local app left behind, keeping ids. Returns the rows to insert."""
    ids = {t: {r["id"] for r in rows} for t, rows in tables.items() if rows and "id" in rows[0]}
    ids["drive_files"] = set()
    out = {t: [dict(r) for r in rows] for t, rows in tables.items()}
    for t, refs in REFS.items():
        keep = []
        for r in out.get(t, []):
            drop = False
            for col, parent, nullable in refs:
                v = r.get(col)
                if v in (None, ""):
                    r[col] = None; continue
                if v not in ids.get(parent, set()):
                    if nullable:
                        report["nulled_references"].append(dict(table=t, id=r.get("id", r.get("image_id")), column=col, was=v)); r[col] = None
                    else:
                        drop = True; report["skipped_rows"].append(dict(table=t, id=r.get("id", r.get("image_id")), reason=f"{col}={v} not found in {parent}"))
            if not drop:
                keep.append(r)
        out[t] = keep
    # a surviving row can still point to a skipped parent (e.g. a link to a skipped image): second pass
    for t in ("image_links", "drive_files"):
        good_images = {r["id"] for r in out.get("images", [])}; good_models = {r["id"] for r in out.get("models", [])}
        out[t] = [r for r in out.get(t, []) if r.get("image_id") in good_images and ("model_id" not in r or r["model_id"] in good_models)]
    for r in out.get("catalogs", []):
        if r.get("status") in ("processing", "checking"):
            r["status"] = "error"; r["msg"] = "interrupted before migration - retry the extraction"
            report["notes"].append(f"catalog {r['id']} was still 'processing': set to error so it can be retried")
        r.setdefault("uploaded_by", "legacy")
        if not r.get("uploaded_by"):
            r["uploaded_by"] = "legacy"
    for r in out.get("audit", []):
        if not r.get("username"):
            r["username"] = r.get("actor") or "legacy"
    return out


def load_db(rows, dest_url, force, report):
    import db, dataio
    db.use_url(dest_url); db.init()
    c = db.conn()
    try:
        existing = c.execute("SELECT (SELECT COUNT(*) FROM models) m, (SELECT COUNT(*) FROM catalogs) c").fetchone()
        if existing["m"] or existing["c"]:
            if not force:
                raise SystemExit("destination database is not empty (use --force to replace its content)")
            dataio.wipe(c)
        for t in db.TABLE_ORDER:
            if t in rows and rows[t]:
                keep_cols = db.table_columns(c, t)
                dataio.insert_rows(c, t, [{k: v for k, v in r.items() if k in keep_cols} for r in rows[t]])
                report["loaded"][t] = len(rows[t])
        db.reset_sequences(c)
        c.commit()
    except Exception:
        c.rollback(); raise
    finally:
        c.close()


def move_files(source_dir, reuse_drive_ids, verify_drive, report):
    import db, storage, assets
    c = db.conn()
    try:
        old_drive = {r["image_id"]: r["drive_id"] for r in c.execute("SELECT image_id, drive_id FROM drive_files").fetchall()}
        n_up = n_skip = 0
        for r in c.execute("SELECT id, file, drive_id FROM images ORDER BY id").fetchall():
            if storage.IS_DRIVE and r["drive_id"]:
                n_skip += 1; continue
            src = os.path.join(source_dir, "images", r["file"])
            if storage.IS_DRIVE and reuse_drive_ids and r["id"] in old_drive and (not verify_drive or storage.exists_in_drive(old_drive[r["id"]])):
                c.execute("UPDATE images SET drive_id=? WHERE id=?", (old_drive[r["id"]], r["id"])); c.commit(); n_skip += 1; continue
            if not os.path.exists(src):
                report["missing_files"].append(dict(kind="image", id=r["id"], file=r["file"])); continue
            data = open(src, "rb").read()
            did = storage.save_image(r["file"], data)
            if did:
                c.execute("UPDATE images SET drive_id=? WHERE id=?", (did, r["id"])); c.commit()
            n_up += 1
        report["files"]["images_uploaded"], report["files"]["images_already_stored"] = n_up, n_skip
        n_pdf = 0
        for r in c.execute("SELECT id, name, file_sha, drive_id FROM catalogs WHERE file_sha<>'manual'").fetchall():
            if storage.IS_DRIVE and r["drive_id"]:
                continue
            src = os.path.join(source_dir, "uploads", r["file_sha"][:16] + ".pdf")
            if not os.path.exists(src):
                report["missing_files"].append(dict(kind="catalog_pdf", id=r["id"], file=r["name"])); continue
            did = storage.save_catalog_pdf(r["file_sha"], r["name"], open(src, "rb").read())
            if did:
                c.execute("UPDATE catalogs SET drive_id=? WHERE id=?", (did, r["id"])); c.commit()
            n_pdf += 1
        report["files"]["catalog_pdfs_stored"] = n_pdf
        n_export = 0
        for r in c.execute("SELECT id,file_name,drive_id FROM export_versions ORDER BY id").fetchall():
            if storage.IS_DRIVE and r["drive_id"]:
                continue
            src = os.path.join(source_dir, "exports", r["file_name"])
            if not os.path.exists(src):
                report["missing_files"].append(dict(kind="export_pdf", id=r["id"], file=r["file_name"])); continue
            did = storage.save_export_pdf(r["file_name"], open(src, "rb").read())
            if did:
                c.execute("UPDATE export_versions SET drive_id=? WHERE id=?", (did, r["id"])); c.commit()
            n_export += 1
        report["files"]["export_pdfs_stored"] = n_export
        # profile images (logo, watermark, and paper background) and uploaded fonts -> database assets
        import profiles
        for table in ("profiles", "profile_versions"):
            for p in c.execute(f"SELECT id, data FROM {table}").fetchall():
                d = json.loads(p["data"] or "{}"); changed = False
                for k in ("logo", "wm_image", "bg_image"):
                    v = d.get(k) or ""
                    if not v or v.startswith("asset:"):
                        continue
                    cand = [v, os.path.join(source_dir, "profiles", os.path.basename(v)), os.path.join(source_dir, os.path.basename(v))]
                    f = next((x for x in cand if os.path.exists(x)), None)
                    if f:
                        key = f"profile-{p['id']}-{k}" if table == "profiles" else f"profile-version-{p['id']}-{k}"
                        assets.put(key, os.path.basename(f), open(f, "rb").read()); d[k] = "asset:" + key
                    else:
                        report["missing_files"].append(dict(kind=f"profile_{k}", id=p["id"], file=v)); d[k] = ""
                    changed = True
                if changed:
                    c.execute(f"UPDATE {table} SET data=? WHERE id=?", (json.dumps(d, ensure_ascii=False), p["id"])); c.commit()
        fdir = os.path.join(source_dir, "fonts")
        if os.path.isdir(fdir):
            for fn in sorted(os.listdir(fdir)):
                if fn.endswith(".ttf"):
                    assets.put("font/" + fn, fn, open(os.path.join(fdir, fn), "rb").read(), "font/ttf"); report["files"]["fonts"] = report["files"].get("fonts", 0) + 1
    finally:
        c.close()


def verify(expected, report):
    import db, dataio, storage
    c = db.conn(); bad = []
    try:
        for t in db.TABLE_ORDER:
            exp = expected.get(t, [])
            got = dataio.read_table(c, t)
            if len(exp) != len(got):
                bad.append(f"{t}: expected {len(exp)} rows, found {len(got)}")
                continue
            key = "id" if t in db.ID_TABLES else {"drive_files": "image_id", "attr_map": "raw", "settings": "k", "assets": "key"}[t]
            gmap = {r[key]: r for r in got}
            for r in exp:
                g = gmap.get(r[key])
                if g is None:
                    bad.append(f"{t}: id {r[key]} missing"); continue
                for col, v in r.items():
                    if col in ("drive_id",) or col not in g:
                        continue
                    if col == "data" and t in ("profiles", "profile_versions"):
                        continue                    # profile asset paths are rewritten to database references on purpose
                    if g[col] != v and not (isinstance(v, float) and abs((g[col] or 0) - v) < 1e-9):
                        bad.append(f"{t} #{r[key]}.{col}: {v!r} != {g[col]!r}"); break
        if storage.IS_DRIVE:
            n = c.execute("SELECT COUNT(*) n FROM images WHERE drive_id IS NULL OR drive_id=''").fetchone()["n"]
            if n:
                bad.append(f"{n} images have no Drive file id")
            n = c.execute("SELECT COUNT(*) n FROM export_versions WHERE drive_id IS NULL OR drive_id=''").fetchone()["n"]
            if n:
                bad.append(f"{n} exported PDFs have no Drive file id")
        else:
            for r in c.execute("SELECT file FROM images").fetchall():
                if not os.path.exists(os.path.join(db.IMG_DIR, r["file"])):
                    bad.append(f"image file missing in storage: {r['file']}")
            for r in c.execute("SELECT file_name FROM export_versions").fetchall():
                if not os.path.exists(os.path.join(db.EXPORT_DIR, r["file_name"])):
                    bad.append(f"export PDF missing in storage: {r['file_name']}")
    finally:
        c.close()
    report["verify_problems"] = bad[:200]
    return not bad


def migrate(source_dir, dest_url, force=False, dry_run=False, db_only=False, files_only=False, reuse_drive_ids=False, verify_drive=False):
    report = dict(started=datetime.datetime.utcnow().isoformat() + "Z", source=os.path.abspath(source_dir), source_counts={}, loaded={}, nulled_references=[],
                  skipped_rows=[], notes=[], missing_files=[], files={}, verify_problems=[], ok=False)
    tables = read_source(source_dir)
    report["source_counts"] = {t: len(r) for t, r in tables.items()}
    rows = transform(tables, report)
    if dry_run:
        report["notes"].append("dry run: nothing written")
        report["ok"] = not report["skipped_rows"]
        return report
    if not files_only:
        load_db(rows, dest_url, force, report)
    else:
        import db; db.use_url(dest_url); db.init()
    if not db_only:
        move_files(source_dir, reuse_drive_ids, verify_drive, report)
    report["ok"] = verify(rows, report) and not report["skipped_rows"] and not report["missing_files"]
    report["finished"] = datetime.datetime.utcnow().isoformat() + "Z"
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source-dir", default=os.path.join(HERE, "data"))
    ap.add_argument("--dest", default=os.environ.get("DATABASE_URL", ""), help="DATABASE_URL of the central database")
    ap.add_argument("--force", action="store_true", help="replace the content of a non-empty destination")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db-only", action="store_true"); ap.add_argument("--files-only", action="store_true")
    ap.add_argument("--reuse-drive-ids", action="store_true", help="reuse Drive files created by the old Sheets sync (drive_files table)")
    ap.add_argument("--verify-drive", action="store_true", help="with --reuse-drive-ids: check that each reused Drive file still exists")
    ap.add_argument("--report", default="migration_report.json")
    a = ap.parse_args()
    if not a.dest and not a.dry_run:
        raise SystemExit("--dest (or DATABASE_URL) is required")
    rep = migrate(a.source_dir, a.dest, a.force, a.dry_run, a.db_only, a.files_only, a.reuse_drive_ids, a.verify_drive)
    json.dump(rep, open(a.report, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps({k: rep[k] for k in ("source_counts", "loaded", "files", "ok")}, ensure_ascii=False, indent=2))
    for k in ("nulled_references", "skipped_rows", "missing_files", "verify_problems"):
        if rep[k]:
            print(f"!! {k}: {len(rep[k])} (see {a.report})")
    print("MIGRATION OK" if rep["ok"] else "MIGRATION FINISHED WITH PROBLEMS - read the report before switching users over")
    sys.exit(0 if rep["ok"] else 2)


if __name__ == "__main__":
    main()
