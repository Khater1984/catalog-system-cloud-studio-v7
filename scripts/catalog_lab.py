#!/usr/bin/env python3
"""Run the extraction + duplicate detection on a FOLDER OF REAL CATALOG PDFs in a throw-away database and print what the system decides.
Nothing touches the production database or Drive. Use it to check the 4 detection levels against real files (different names / versions).

  python scripts/catalog_lab.py /path/to/pdfs [--supplier "Henan Ideal"] [--version-from-name]

Files are processed in name order. When a duplicate warning needs a decision the lab continues anyway (and prints the warning).
Add a small CSV (name,supplier,version,date) as <folder>/meta.csv to give each file its supplier / version / date.
"""
import os, sys, csv, shutil, tempfile, argparse, json, hashlib
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
tmp = tempfile.mkdtemp(prefix="catalog_lab_")
os.environ.update(DATA_DIR=tmp, DATABASE_URL="sqlite:///" + tmp + "/lab.db", STORAGE_BACKEND="local")
import db, extractor, storage, dedupe


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("folder"); ap.add_argument("--supplier", default=""); a = ap.parse_args()
    db.init(); db.set_actor("lab")
    meta = {}
    mp = os.path.join(a.folder, "meta.csv")
    if os.path.exists(mp):
        for r in csv.DictReader(open(mp, encoding="utf-8")):
            meta[r["name"]] = r
    files = sorted(f for f in os.listdir(a.folder) if f.lower().endswith(".pdf"))
    print(f"{'file':40} {'verdict':16} {'models':>6} {'specs':>6}  review kinds / findings")
    for f in files:
        data = open(os.path.join(a.folder, f), "rb").read(); sha = dedupe.sha256_bytes(data); m = meta.get(f, {})
        c = db.conn()
        old = dedupe.exact_match(c, sha)
        if old:
            print(f"{f[:40]:40} {'EXACT COPY':16} {'-':>6} {'-':>6}  same file as «{old['name']}» (level 1: blocked)"); c.close(); continue
        did = storage.save_catalog_pdf(sha, f, data)
        cid = c.execute("INSERT INTO catalogs(name,supplier,file_sha,status,version,catalog_date,meta_source,uploaded_by,drive_id,size_bytes) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (f, m.get("supplier") or a.supplier, sha, "checking", m.get("version", ""), m.get("date", ""), "user" if (m.get("version") or m.get("date")) else "", "lab", did, len(data))).lastrowid
        c.commit(); c.close()
        extractor.pipeline(cid, "lab")
        c = db.conn(); cat = c.execute("SELECT * FROM catalogs WHERE id=?", (cid,)).fetchone(); verdict = cat["status"]; info = {}
        if verdict == "needs_decision":
            info = json.loads(cat["dup_info"]); verdict = "WARN:" + info["kind"]; extractor.pipeline(cid, "lab", skip_checks=True); cat = c.execute("SELECT * FROM catalogs WHERE id=?", (cid,)).fetchone()
        n_m = c.execute("SELECT COUNT(*) n FROM models WHERE catalog_id=?", (cid,)).fetchone()["n"]; n_s = c.execute("SELECT COUNT(*) n FROM specs s JOIN models m ON m.id=s.model_id WHERE m.catalog_id=?", (cid,)).fetchone()["n"]
        kinds = {}
        for r in c.execute("SELECT kind FROM review WHERE catalog_id=?", (cid,)).fetchall():
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        extra = f"version={cat['version'] or '-'}({cat['meta_source'] or '-'}) date={cat['catalog_date'] or '-'} prev={cat['previous_catalog_id'] or '-'}"
        if info: extra += f" | {info['message']}"
        print(f"{f[:40]:40} {('ERROR' if cat['status']=='error' else verdict)[:16]:16} {n_m:>6} {n_s:>6}  {kinds} {extra}")
        if cat["status"] == "error": print("    ", (cat["msg"] or "")[-300:])
        unv = c.execute("SELECT COUNT(*) n FROM specs s JOIN models m ON m.id=s.model_id WHERE m.catalog_id=? AND s.verified=0", (cid,)).fetchone()["n"]
        if unv: print(f"     {unv} specs could not be found in the source page text (check them)")
        c.close()
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
