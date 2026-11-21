"""Backup / restore / copy of the whole database as a portable zip of JSON-lines (same format for SQLite and PostgreSQL).
File *contents* (images, PDFs) live in Google Drive; the backup keeps their Drive ids + hashes (files.csv)."""
import io, json, zipfile, hashlib, base64, datetime, csv
import db

FORMAT = 1
SELF_REF = {"families": "parent_id", "catalogs": "previous_catalog_id"}


def _jsonable(v):
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"__b64__": base64.b64encode(bytes(v)).decode()}
    return v


def _unjson(v, pg):
    if isinstance(v, dict) and "__b64__" in v:
        raw = base64.b64decode(v["__b64__"])
        if pg:
            import psycopg2
            return psycopg2.Binary(raw)
        return raw
    return v


def read_table(c, t):
    order = "id" if t in db.ID_TABLES else {"drive_files": "image_id", "attr_map": "raw", "settings": "k", "assets": "key"}[t]
    rows = c.execute(f"SELECT * FROM {t} ORDER BY {order}").fetchall()
    return [{k: _jsonable(r[k]) for k in r.keys()} for r in rows]


def dump(c, tables=None):
    """-> zip bytes"""
    tables = tables or db.TABLE_ORDER
    buf = io.BytesIO(); counts, hashes = {}, {}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for t in tables:
            rows = read_table(c, t)
            body = "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows)
            counts[t] = len(rows); hashes[t] = hashlib.sha256(body.encode()).hexdigest()
            z.writestr(f"tables/{t}.jsonl", body)
        out = io.StringIO(); w = csv.writer(out); w.writerow(["kind", "name", "sha", "drive_id"])
        for r in c.execute("SELECT file, sha, drive_id FROM images").fetchall():
            w.writerow(["image", r["file"], r["sha"], r["drive_id"] or ""])
        for r in c.execute("SELECT name, file_sha, drive_id FROM catalogs").fetchall():
            w.writerow(["catalog", r["name"], r["file_sha"], r["drive_id"] or ""])
        z.writestr("files.csv", out.getvalue())
        z.writestr("manifest.json", json.dumps(dict(format=FORMAT, created=datetime.datetime.utcnow().isoformat() + "Z", schema_version=db.SCHEMA_VERSION,
                                                    counts=counts, sha256=hashes), indent=2))
    return buf.getvalue()


def verify(zbytes):
    z = zipfile.ZipFile(io.BytesIO(zbytes)); m = json.loads(z.read("manifest.json"))
    if m.get("format") != FORMAT:
        raise ValueError("unsupported backup format")
    for t, h in m["sha256"].items():
        if hashlib.sha256(z.read(f"tables/{t}.jsonl")).hexdigest() != h:
            raise ValueError(f"backup is corrupt: table {t}")
    return m


def insert_rows(c, t, rows):
    """Insert rows keeping their ids. Self references are written after the rows exist."""
    if not rows:
        return
    ref = SELF_REF.get(t)
    for r in rows:
        r = {k: _unjson(v, c.pg) for k, v in r.items()}
        deferred = r.pop(ref, None) if ref else None
        if ref:
            r[ref] = None
        cols = list(r)
        c.execute(f"INSERT INTO {t}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", [r[k] for k in cols])
        if deferred is not None:
            c.execute(f"UPDATE {t} SET {ref}=? WHERE id=?", (deferred, r["id"]))


def wipe(c):
    for t in reversed(db.TABLE_ORDER):
        c.execute(f"DELETE FROM {t}")


def restore(c, zbytes):
    """Replace the content of the (already initialised) database with the backup. One transaction: all or nothing."""
    m = verify(zbytes)
    z = zipfile.ZipFile(io.BytesIO(zbytes))
    try:
        wipe(c)
        for t in db.TABLE_ORDER:
            if t in m["counts"]:
                body = z.read(f"tables/{t}.jsonl").decode()
                insert_rows(c, t, [json.loads(line) for line in body.splitlines() if line.strip()])
        db.reset_sequences(c)
        c.commit()
    except Exception:
        c.rollback(); raise
    return m["counts"]
