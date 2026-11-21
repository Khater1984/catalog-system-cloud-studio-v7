"""Database layer.

One code base, two backends:
  * PostgreSQL  - when DATABASE_URL=postgresql://...   (production on Cloud Run / Cloud SQL)
  * SQLite      - when DATABASE_URL is empty or sqlite:///path  (local development / tests only)

The application code keeps using `?` placeholders, `row["col"]`, `cursor.lastrowid`; this module translates
them for PostgreSQL. Schema changes go through the numbered MIGRATIONS list (table `schema_version`).
"""
import os, re, sqlite3, threading, contextvars

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DATA_DIR") or os.path.join(BASE, "data")
IMG_DIR = os.path.join(DATA, "images")          # local mode only (cloud mode keeps images in Google Drive + cache)
UPLOAD_DIR = os.path.join(DATA, "uploads")      # local mode only
CACHE_DIR = os.environ.get("CACHE_DIR") or os.path.join(DATA, "cache")
EXPORT_DIR = os.environ.get("EXPORT_DIR") or os.path.join(DATA, "exports")
VIDEO_DIR = os.environ.get("VIDEO_DIR") or os.path.join(DATA, "videos")       # local mode only (cloud mode keeps videos in Google Drive + cache)
for _d in (DATA, IMG_DIR, UPLOAD_DIR, CACHE_DIR, EXPORT_DIR, VIDEO_DIR):
    os.makedirs(_d, exist_ok=True)

URL = os.environ.get("DATABASE_URL", "").strip()
IS_PG = URL.startswith(("postgres://", "postgresql://"))
DB_PATH = URL[len("sqlite:///"):] if URL.startswith("sqlite:///") else os.path.join(DATA, "catalog.db")

SCHEMA_VERSION = 2
ID_TABLES = {"catalogs", "families", "products", "models", "specs", "images", "image_links", "review", "audit", "profiles", "users", "spec_changes", "deleted_records", "spec_revisions", "product_image_links", "profile_versions", "export_versions", "product_videos"}
# parent -> child order (used by backup / restore / migration)
TABLE_ORDER = ["catalogs", "families", "products", "models", "specs", "images", "image_links", "product_image_links", "product_videos", "review", "drive_files",
               "profiles", "profile_versions", "export_versions", "attr_map", "settings", "users", "assets", "spec_changes", "spec_revisions", "deleted_records", "audit"]

# ------------------------------------------------------------------ actor (who is doing the operation)
_actor = contextvars.ContextVar("actor", default="system")


def set_actor(name):
    _actor.set((name or "system").strip() or "system")


def actor():
    return _actor.get()


# ------------------------------------------------------------------ connections
_pool = None
_pool_lock = threading.Lock()


def use_sqlite(path):
    """Point the module at a SQLite file (tests / local tools)."""
    global URL, IS_PG, DB_PATH
    URL, IS_PG, DB_PATH = "sqlite:///" + path, False, path


def use_url(url):
    """Point the module at any DATABASE_URL (used by the migration / restore tools)."""
    global URL, IS_PG, DB_PATH, _pool
    URL = url
    IS_PG = url.startswith(("postgres://", "postgresql://"))
    if url.startswith("sqlite:///"):
        DB_PATH = url[len("sqlite:///"):]
    _pool = None


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                from psycopg2.pool import ThreadedConnectionPool
                _pool = ThreadedConnectionPool(1, int(os.environ.get("DB_POOL_MAX", "10")), URL, connect_timeout=10)
    return _pool


def _pg_sql(sql, has_params):
    if has_params:
        sql = sql.replace("%", "%%")
    sql = sql.replace("?", "%s")
    sql = re.sub(r"\bLIKE\b", "ILIKE", sql)
    m = re.match(r"^\s*INSERT\s+INTO\s+(\w+)", sql, re.I)
    ret = bool(m and m.group(1).lower() in ID_TABLES)
    if ret and not re.search(r"\bRETURNING\b", sql, re.I):
        sql = sql.rstrip().rstrip(";") + " RETURNING id"
    return sql, ret


class Cur:
    def __init__(self, cur, lastrowid=None):
        self._c, self.lastrowid = cur, lastrowid

    def fetchone(self):
        return self._c.fetchone()

    def fetchall(self):
        return self._c.fetchall()

    def __iter__(self):
        return iter(self._c.fetchall())

    @property
    def rowcount(self):
        return self._c.rowcount


class Conn:
    def __init__(self):
        self.pg = IS_PG
        if self.pg:
            self._pool = _get_pool()
            self.raw = self._pool.getconn()
            try:
                self.raw.cursor().execute("SELECT 1")
            except Exception:                      # stale connection (Cloud SQL restart etc.)
                self._pool.putconn(self.raw, close=True)
                self.raw = self._pool.getconn()
        else:
            self.raw = sqlite3.connect(DB_PATH, timeout=30)
            self.raw.row_factory = sqlite3.Row
            self.raw.execute("PRAGMA foreign_keys=ON")
            try:
                self.raw.execute("PRAGMA journal_mode=WAL")
            except Exception:
                pass

    def execute(self, sql, params=()):
        if isinstance(params, (str, bytes)) or not hasattr(params, "__iter__"):
            params = (params,)
        params = tuple(params)
        if not self.pg:
            cur = self.raw.execute(sql, params)
            return Cur(cur, cur.lastrowid)
        from psycopg2.extras import DictCursor
        sql, ret = _pg_sql(sql, bool(params))
        cur = self.raw.cursor(cursor_factory=DictCursor)
        cur.execute(sql, params if params else None)
        lid = None
        if ret:
            row = cur.fetchone()
            lid = row[0] if row else None
        return Cur(cur, lid)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        if self.pg:
            try:
                self.raw.rollback()
            except Exception:
                pass
            try:
                self._pool.putconn(self.raw, close=bool(self.raw.closed))
            except Exception:
                pass
        else:
            self.raw.close()


def conn():
    return Conn()


# ------------------------------------------------------------------ schema
def _tables(pg):
    ID = "INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY" if pg else "INTEGER PRIMARY KEY"
    REAL = "DOUBLE PRECISION" if pg else "REAL"
    BLOB = "BYTEA" if pg else "BLOB"
    NOW = "(to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'))" if pg else "CURRENT_TIMESTAMP"
    return [
        f"""CREATE TABLE IF NOT EXISTS catalogs(id {ID}, name TEXT, supplier TEXT, file_sha TEXT UNIQUE, status TEXT, msg TEXT, pages INTEGER,
            created TEXT DEFAULT {NOW}, version TEXT DEFAULT '', catalog_date TEXT DEFAULT '', meta_source TEXT DEFAULT '', text_sha TEXT,
            previous_catalog_id INTEGER REFERENCES catalogs(id) ON DELETE SET NULL, dup_info TEXT DEFAULT '', uploaded_by TEXT DEFAULT '',
            drive_id TEXT, heartbeat TEXT DEFAULT '', size_bytes INTEGER DEFAULT 0, text_chars INTEGER DEFAULT 0)""",
        f"CREATE TABLE IF NOT EXISTS families(id {ID}, name TEXT, parent_id INTEGER REFERENCES families(id))",
        f"""CREATE TABLE IF NOT EXISTS products(id {ID}, name TEXT, ar_name TEXT DEFAULT '', description TEXT DEFAULT '',
            family_id INTEGER REFERENCES families(id) ON DELETE SET NULL, suggested INTEGER DEFAULT 0, active INTEGER DEFAULT 1,
            version INTEGER DEFAULT 1, created TEXT DEFAULT {NOW})""",
        f"""CREATE TABLE IF NOT EXISTS models(id {ID}, catalog_id INTEGER REFERENCES catalogs(id), code TEXT, series TEXT, page INTEGER,
            family_id INTEGER REFERENCES families(id) ON DELETE SET NULL, ar_name TEXT DEFAULT '', status TEXT DEFAULT 'staging',
            visible INTEGER DEFAULT 1, suggested_group TEXT DEFAULT '', product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
            active INTEGER DEFAULT 1, version INTEGER DEFAULT 1)""",
        f"""CREATE TABLE IF NOT EXISTS audit(id {ID}, ts TEXT DEFAULT {NOW}, entity TEXT, entity_id INTEGER, action TEXT, detail TEXT,
            username TEXT DEFAULT '')""",
        f"""CREATE TABLE IF NOT EXISTS specs(id {ID}, model_id INTEGER REFERENCES models(id) ON DELETE CASCADE, attr TEXT, supplier_value TEXT,
            company_value TEXT DEFAULT '', rule TEXT DEFAULT '', hidden INTEGER DEFAULT 0, sort INTEGER DEFAULT 0,
            source_page INTEGER, source_text TEXT DEFAULT '', verified INTEGER DEFAULT 1)""",
        f"""CREATE TABLE IF NOT EXISTS images(id {ID}, catalog_id INTEGER REFERENCES catalogs(id), page INTEGER, file TEXT, sha TEXT,
            w INTEGER, h INTEGER, dpi {REAL}, source TEXT, rejected INTEGER DEFAULT 0, drive_id TEXT)""",
        f"""CREATE TABLE IF NOT EXISTS image_links(id {ID}, image_id INTEGER REFERENCES images(id) ON DELETE CASCADE,
            model_id INTEGER REFERENCES models(id) ON DELETE CASCADE, confidence TEXT, is_primary INTEGER DEFAULT 0,
            ok_catalog INTEGER DEFAULT 0, ok_sheet INTEGER DEFAULT 0, UNIQUE(image_id, model_id))""",
        f"""CREATE TABLE IF NOT EXISTS review(id {ID}, catalog_id INTEGER REFERENCES catalogs(id), kind TEXT, ref TEXT, note TEXT,
            resolved INTEGER DEFAULT 0)""",
        "CREATE TABLE IF NOT EXISTS drive_files(image_id INTEGER PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE, drive_id TEXT)",
        f"CREATE TABLE IF NOT EXISTS profiles(id {ID}, name TEXT, data TEXT)",
        "CREATE TABLE IF NOT EXISTS attr_map(raw TEXT PRIMARY KEY, canonical TEXT, ar TEXT DEFAULT '', sort INTEGER DEFAULT 999, suggested INTEGER DEFAULT 0)",
        "CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT)",
        f"""CREATE TABLE IF NOT EXISTS users(id {ID}, username TEXT UNIQUE NOT NULL, display_name TEXT DEFAULT '', password_hash TEXT NOT NULL,
            is_owner INTEGER DEFAULT 0, active INTEGER DEFAULT 1, created TEXT DEFAULT {NOW}, last_login TEXT DEFAULT '',
            failed_count INTEGER DEFAULT 0, locked_until TEXT DEFAULT '')""",
        # marketing videos of a machine (a file in the store, or a link); caption / hashtags / platforms are kept for social media use
        f"""CREATE TABLE IF NOT EXISTS product_videos(id {ID}, product_id INTEGER REFERENCES products(id), model_id INTEGER REFERENCES models(id),
            title TEXT DEFAULT '', caption TEXT DEFAULT '', hashtags TEXT DEFAULT '', platforms TEXT DEFAULT '', status TEXT DEFAULT 'draft',
            kind TEXT DEFAULT 'file', file TEXT DEFAULT '', drive_id TEXT, url TEXT DEFAULT '', mime TEXT DEFAULT '', size INTEGER DEFAULT 0,
            duration {REAL} DEFAULT 0, width INTEGER DEFAULT 0, height INTEGER DEFAULT 0, sha TEXT DEFAULT '', active INTEGER DEFAULT 1,
            uploaded_by TEXT DEFAULT '', created TEXT DEFAULT {NOW})""",
        # large uploads arrive in 8 MB pieces (Cloud Run refuses requests above 32 MB, and mobile connections drop): staged here, then assembled
        f"CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY, filename TEXT, size INTEGER, product_id INTEGER, created TEXT DEFAULT {NOW})",
        f"CREATE TABLE IF NOT EXISTS upload_chunks(upload_id TEXT, idx INTEGER, data {BLOB}, PRIMARY KEY(upload_id, idx))",
        f"CREATE TABLE IF NOT EXISTS assets(key TEXT PRIMARY KEY, name TEXT, mime TEXT, sha TEXT, size INTEGER, data {BLOB}, created TEXT DEFAULT {NOW})",
        # append-only history of every change to a specification: the source value (specs.supplier_value) is never edited, changes are logged here
        f"""CREATE TABLE IF NOT EXISTS spec_changes(id {ID}, ts TEXT DEFAULT {NOW}, username TEXT DEFAULT '', product_id INTEGER, model_id INTEGER, spec_id INTEGER,
            action TEXT, attr TEXT, source_value TEXT, old_value TEXT, new_value TEXT)""",
        f"""CREATE TABLE IF NOT EXISTS spec_revisions(id {ID}, product_id INTEGER, variant_id INTEGER,
            specification_id INTEGER NOT NULL, operation TEXT NOT NULL, old_value TEXT DEFAULT '',
            new_value TEXT DEFAULT '', modified_at TEXT DEFAULT {NOW}, modified_by TEXT DEFAULT 'user')""",
        f"""CREATE TABLE IF NOT EXISTS product_image_links(id {ID}, image_id INTEGER REFERENCES images(id) ON DELETE CASCADE,
            product_id INTEGER REFERENCES products(id) ON DELETE CASCADE, image_role TEXT DEFAULT 'main_product',
            confidence TEXT DEFAULT 'confirmed', is_primary INTEGER DEFAULT 0, ok_catalog INTEGER DEFAULT 1,
            ok_sheet INTEGER DEFAULT 1, sort_order INTEGER DEFAULT 0, UNIQUE(image_id, product_id))""",
        f"""CREATE TABLE IF NOT EXISTS profile_versions(id {ID}, profile_id INTEGER REFERENCES profiles(id) ON DELETE CASCADE,
            version_num INTEGER NOT NULL, name TEXT, data TEXT, created TEXT DEFAULT {NOW}, UNIQUE(profile_id, version_num))""",
        f"""CREATE TABLE IF NOT EXISTS export_versions(id {ID}, doc_type TEXT NOT NULL, template_name TEXT NOT NULL,
            profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL, title TEXT DEFAULT '', file_name TEXT NOT NULL,
            options_json TEXT DEFAULT '{{}}', page_count INTEGER DEFAULT 1, drive_id TEXT, created TEXT DEFAULT {NOW})""",
        # snapshots of deleted records, so an accidental deletion can be restored
        f"""CREATE TABLE IF NOT EXISTS deleted_records(id {ID}, ts TEXT DEFAULT {NOW}, username TEXT DEFAULT '', entity TEXT, entity_id INTEGER, label TEXT,
            snapshot TEXT, restored INTEGER DEFAULT 0)""",
        "CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL)",
    ]


# columns added after the first public version of the local app (upgrade of an existing database)
ADD_COLUMNS = {
    "models": [("product_id", "INTEGER"), ("active", "INTEGER DEFAULT 1"), ("version", "INTEGER DEFAULT 1")],
    "products": [("version", "INTEGER DEFAULT 1")],
    "catalogs": [("version", "TEXT DEFAULT ''"), ("catalog_date", "TEXT DEFAULT ''"), ("meta_source", "TEXT DEFAULT ''"), ("text_sha", "TEXT"),
                 ("previous_catalog_id", "INTEGER"), ("dup_info", "TEXT DEFAULT ''"), ("uploaded_by", "TEXT DEFAULT ''"), ("drive_id", "TEXT"),
                 ("heartbeat", "TEXT DEFAULT ''"), ("size_bytes", "INTEGER DEFAULT 0"), ("text_chars", "INTEGER DEFAULT 0")],
    "audit": [("username", "TEXT DEFAULT ''")],
    "specs": [("source_page", "INTEGER"), ("source_text", "TEXT DEFAULT ''"), ("verified", "INTEGER DEFAULT 1")],
    "images": [("drive_id", "TEXT")],
}

# Columns used by the local Product Studio and Export Center are added in place
# for existing Cloud databases as well as fresh installations.
for _table, _cols in {
    "families": [("sort_order", "INTEGER DEFAULT 0")],
    "products": [("supplier", "TEXT DEFAULT ''"), ("manufacturer", "TEXT DEFAULT ''"),
                 ("status", "TEXT DEFAULT 'staging'"), ("sort_order", "INTEGER DEFAULT 0"),
                 ("created", "TEXT DEFAULT ''")],
    "models": [("name", "TEXT DEFAULT ''"), ("sort_order", "INTEGER DEFAULT 0"),
               ("public_enabled", "INTEGER DEFAULT 0")],
    "specs": [("product_id", "INTEGER"), ("supplier_unit", "TEXT DEFAULT ''"),
              ("company_unit", "TEXT DEFAULT ''"), ("deleted", "INTEGER DEFAULT 0"),
              ("source_tag", "TEXT DEFAULT 'catalog'")],
    "images": [("phash", "TEXT DEFAULT ''"), ("dhash", "TEXT DEFAULT ''"),
               ("original_format", "TEXT DEFAULT ''"), ("extraction_method", "TEXT DEFAULT ''"),
               ("image_type", "TEXT DEFAULT 'model_image'"), ("link_status", "TEXT DEFAULT 'unlinked'"),
               ("quality_flag", "TEXT DEFAULT 'ok'"), ("has_alpha", "INTEGER DEFAULT 0"),
               ("bbox_json", "TEXT DEFAULT ''"), ("neighbor_text", "TEXT DEFAULT ''"),
               ("preview_file", "TEXT DEFAULT ''"), ("disabled", "INTEGER DEFAULT 0")],
    "image_links": [("image_role", "TEXT DEFAULT 'model_image'"), ("sort_order", "INTEGER DEFAULT 0")],
}.items():
    ADD_COLUMNS.setdefault(_table, []).extend(_cols)

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_specs_model ON specs(model_id)",
    "CREATE INDEX IF NOT EXISTS ix_links_model ON image_links(model_id)",
    "CREATE INDEX IF NOT EXISTS ix_links_image ON image_links(image_id)",
    "CREATE INDEX IF NOT EXISTS ix_models_catalog ON models(catalog_id)",
    "CREATE INDEX IF NOT EXISTS ix_models_product ON models(product_id)",
    "CREATE INDEX IF NOT EXISTS ix_models_family ON models(family_id)",
    "CREATE INDEX IF NOT EXISTS ix_models_code ON models(code)",
    "CREATE INDEX IF NOT EXISTS ix_images_catalog ON images(catalog_id, page)",
    "CREATE INDEX IF NOT EXISTS ix_images_sha ON images(sha)",
    "CREATE INDEX IF NOT EXISTS ix_images_file ON images(file)",
    "CREATE INDEX IF NOT EXISTS ix_review_catalog ON review(catalog_id)",
    "CREATE INDEX IF NOT EXISTS ix_audit_entity ON audit(entity, entity_id)",
    "CREATE INDEX IF NOT EXISTS ix_catalogs_supplier ON catalogs(supplier)",
    "CREATE INDEX IF NOT EXISTS ix_catalogs_text ON catalogs(text_sha)",
    "CREATE INDEX IF NOT EXISTS ix_specchg_model ON spec_changes(model_id)",
]
INDEXES.extend([
    "CREATE INDEX IF NOT EXISTS ix_product_image_product ON product_image_links(product_id)",
    "CREATE INDEX IF NOT EXISTS ix_export_versions_created ON export_versions(created)",
])

# Future schema changes: append (version, description, [sql statements]) here. Fresh installs are stamped with SCHEMA_VERSION.
# Example:  (2, "add column x", ["ALTER TABLE models ADD COLUMN x TEXT DEFAULT ''"]),  then bump SCHEMA_VERSION to 2 and mirror it in _tables().
MIGRATIONS = [(2, "Product Studio and Export Center tables", ["SELECT 1"])]


def table_columns(c, table):
    if c.pg:
        return {r[0] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=?", (table,))}
    return {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}


def get_version(c):
    r = c.execute("SELECT MAX(version) v FROM schema_version").fetchone()
    return (r["v"] if r and r["v"] is not None else 0)


def init():
    c = conn()
    try:
        if c.pg:
            c.execute("SELECT pg_advisory_lock(7351001)")      # several Cloud Run instances may start together
        for stmt in _tables(c.pg):
            c.execute(stmt)
        for table, cols in ADD_COLUMNS.items():
            have = table_columns(c, table)
            for name, ddl in cols:
                if name not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        for stmt in INDEXES:
            c.execute(stmt)
        ver = get_version(c)
        if ver == 0:
            c.execute("INSERT INTO schema_version(version) VALUES(?)", (SCHEMA_VERSION,))
            ver = SCHEMA_VERSION
        for v, _desc, stmts in MIGRATIONS:
            if v > ver:
                for s in stmts:
                    c.execute(s)
                c.execute("UPDATE schema_version SET version=?", (v,))
                ver = v
        c.commit()
    finally:
        if c.pg:
            try:
                c.execute("SELECT pg_advisory_unlock(7351001)"); c.commit()
            except Exception:
                pass
        c.close()


def reset_sequences(c):
    """After loading rows with explicit ids (migration / restore) the identity sequences must continue after MAX(id)."""
    if not c.pg:
        return
    for t in ID_TABLES:
        c.execute(f"SELECT setval(pg_get_serial_sequence('{t}','id'), COALESCE((SELECT MAX(id) FROM {t}),0)+1, false)")


# ------------------------------------------------------------------ helpers used all over the app
def log(c, entity, eid, action, detail=""):
    c.execute("INSERT INTO audit(entity,entity_id,action,detail,username) VALUES(?,?,?,?,?)", (entity, eid, action, detail, actor()))


def log_spec_rev(c, product_id, variant_id, spec_id, op, old_val="", new_val="", who=None):
    c.execute("""INSERT INTO spec_revisions(product_id,variant_id,specification_id,operation,old_value,new_value,modified_by)
                 VALUES(?,?,?,?,?,?,?)""",
              (product_id, variant_id, spec_id, op, str(old_val or ""), str(new_val or ""), who or actor()))


def compute_final_spec(sp_row):
    val = sp_row["company_value"] if (sp_row["company_value"] or "").strip() else (sp_row["supplier_value"] or "")
    unit = sp_row["company_unit"] if (sp_row["company_unit"] or "").strip() else (sp_row["supplier_unit"] or "")
    full = f"{val} {unit}".strip() if unit and unit not in val else val.strip()
    return val.strip(), unit.strip(), full


def get_setting(c, k, default=""):
    r = c.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
    return r["v"] if r else default


def set_setting(c, k, v):
    c.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))


def touch(c, table, ids):
    """Bump the version of rows that changed: a person who opened the edit form earlier will be told about the conflict instead of overwriting."""
    ids = [int(i) for i in ids]
    for i in ids:
        c.execute(f"UPDATE {table} SET version=COALESCE(version,1)+1 WHERE id=?", (i,))


def spec_change(c, action, spec, new_value="", old_value=None, model=None):
    """Record a change of a specification (ADD / UPDATE / DELETE(hide) / RESTORE(show) / RULE). `spec` is the specs row."""
    m = model or c.execute("SELECT product_id FROM models WHERE id=?", (spec["model_id"],)).fetchone()
    c.execute("INSERT INTO spec_changes(username,product_id,model_id,spec_id,action,attr,source_value,old_value,new_value) VALUES(?,?,?,?,?,?,?,?,?)",
              (actor(), m["product_id"] if m else None, spec["model_id"], spec["id"], action, spec["attr"], spec["supplier_value"] or "",
               old_value if old_value is not None else (spec["company_value"] or ""), new_value))
