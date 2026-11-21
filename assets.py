"""Small binary assets (logo, watermark image, uploaded fonts) kept inside the database, so they are backed up with it.
`path(key)` materialises an asset as a local cache file (the PDF engine needs real file paths)."""
import os, hashlib, mimetypes, re
import db

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def put(key, name, data, mime=None):
    sha = hashlib.sha256(data).hexdigest()
    mime = mime or mimetypes.guess_type(name)[0] or "application/octet-stream"
    c = db.conn()
    try:
        c.execute("""INSERT INTO assets(key,name,mime,sha,size,data) VALUES(?,?,?,?,?,?)
                     ON CONFLICT(key) DO UPDATE SET name=excluded.name, mime=excluded.mime, sha=excluded.sha, size=excluded.size, data=excluded.data""",
                  (key, name, mime, sha, len(data), data if not c.pg else __import__("psycopg2").Binary(data)))
        c.commit()
    finally:
        c.close()
    return key


def delete(key):
    c = db.conn()
    try:
        c.execute("DELETE FROM assets WHERE key=?", (key,)); c.commit()
    finally:
        c.close()


def path(key):
    """Local file path for asset `key` (or None)."""
    if not key:
        return None
    c = db.conn()
    try:
        r = c.execute("SELECT name, sha FROM assets WHERE key=?", (key,)).fetchone()
        if not r:
            return None
        d = os.path.join(db.CACHE_DIR, "assets"); os.makedirs(d, exist_ok=True)
        p = os.path.join(d, f"{r['sha'][:12]}_{_SAFE.sub('_', r['name'])}")
        if not os.path.exists(p):
            row = c.execute("SELECT data FROM assets WHERE key=?", (key,)).fetchone()
            tmp = p + ".tmp"
            with open(tmp, "wb") as fh: fh.write(bytes(row["data"]))
            os.replace(tmp, p)
        return p
    finally:
        c.close()


def materialize_fonts(font_dir):
    """Write uploaded fonts (assets whose key starts with 'font/') into font_dir if they are missing."""
    c = db.conn()
    try:
        for r in c.execute("SELECT key, sha FROM assets WHERE key LIKE 'font/%'").fetchall():
            fn = r["key"][5:]
            p = os.path.join(font_dir, fn)
            if not os.path.exists(p):
                row = c.execute("SELECT data FROM assets WHERE key=?", (r["key"],)).fetchone()
                with open(p, "wb") as fh: fh.write(bytes(row["data"]))
    finally:
        c.close()
