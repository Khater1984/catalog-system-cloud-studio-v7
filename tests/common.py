"""Shared test setup: isolated temp data dir + SQLite database, helpers to build synthetic catalog PDFs."""
import os, sys, tempfile, io, shutil
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if "CATALOG_TEST_TMP" not in os.environ:
    os.environ["CATALOG_TEST_TMP"] = tempfile.mkdtemp(prefix="catalog_tests_")
TMP = os.environ["CATALOG_TEST_TMP"]
MAIN_URL = os.environ.get("TEST_DATABASE_URL") or ("sqlite:///" + TMP + "/test.db")      # TEST_DATABASE_URL=postgresql://... runs the same suite on PostgreSQL
os.environ.update(DATA_DIR=TMP, DATABASE_URL=MAIN_URL, SECRET_KEY="t" * 40, STORAGE_BACKEND="local")
for k in ("AUTH_DISABLED", "APP_ENV", "OWNER_USERNAME", "OWNER_PASSWORD"):
    os.environ.pop(k, None)
sys.path.insert(0, ROOT)

import db, auth, extractor, dataio
import app as A

# run the background pipeline synchronously in tests
extractor.start = lambda cid, actor="system", skip_checks=False: extractor.pipeline(cid, actor, skip_checks)

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors
from PIL import Image

FILLER = ("This catalog describes industrial rebar processing machines manufactured under strict quality control. "
          "Every machine is tested before delivery and supplied with a complete set of documents and spare parts. ")


def make_image(path, color, size=(400, 300)):
    im = Image.new("RGB", size, color); px = im.load()
    for x in range(size[0]):
        for y in range(size[1]):
            px[x, y] = ((color[0] + x) % 256, (color[1] + y) % 256, color[2])
    im.save(path)


def make_pdf(path, pages, title="catalog", tag=""):
    """pages: list of dicts(title, models={code: {attr: value}}, image=path|None, transposed=bool, filler=str)."""
    c = canvas.Canvas(path, pagesize=A4, invariant=1); c.setTitle(title)
    W, H = A4
    for i, pg in enumerate(pages):
        c.setFont("Helvetica-Bold", 16); c.drawString(50, H - 60, pg.get("title", f"Series {i + 1}"))
        c.setFont("Helvetica", 9)
        y = H - 85
        for line in (pg.get("filler", FILLER + tag + f" page {i + 1}. ") * 2).split(". "):
            c.drawString(50, y, line[:110]); y -= 12
        models = pg["models"]; codes = list(models)
        attrs = []
        for m in models.values():
            attrs += [a for a in m if a not in attrs]
        if pg.get("transposed"):
            data = [["Model"] + attrs] + [[code] + [models[code].get(a, "") for a in attrs] for code in codes]
        else:
            data = [["Model"] + codes] + [[a] + [models[code].get(a, "") for code in codes] for a in attrs]
        t = Table(data, colWidths=[110] + [90] * (len(data[0]) - 1)); t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
        t.wrapOn(c, W, H); t.drawOn(c, 50, H - 330)
        if pg.get("image"):
            c.drawImage(pg["image"], 50, H - 560, width=200, height=150)
        c.showPage()
    c.save()
    return path


def reset():
    """Empty database + storage, then create owner / alice / bob."""
    db.use_url(MAIN_URL); db.init()
    c = db.conn()
    try:
        dataio.wipe(c); c.commit()
        db.set_actor("tests")
        auth.create_user(c, "owner", "ownerpass1", "Owner", True)
        auth.create_user(c, "alice", "alicepass1", "Alice")
        auth.create_user(c, "bob", "bobpass123", "Bob")
        c.commit()
    finally:
        c.close()
    for d in (db.IMG_DIR, db.UPLOAD_DIR, os.path.join(db.CACHE_DIR)):
        shutil.rmtree(d, ignore_errors=True); os.makedirs(d, exist_ok=True)
    db.set_actor("system")


def client_for(user="alice", pw=None):
    cl = A.app.test_client()
    pw = pw or {"owner": "ownerpass1", "alice": "alicepass1", "bob": "bobpass123"}[user]
    r = cl.post("/login", data={"username": user, "password": pw})
    assert r.status_code == 302, r.status_code
    return cl


def upload(cl, path, supplier="", version="", date="", name=None):
    with open(path, "rb") as f:
        return cl.post("/upload", data={"pdf": (io.BytesIO(f.read()), name or os.path.basename(path)), "supplier": supplier, "version": version, "catalog_date": date},
                       content_type="multipart/form-data")


def q(sql, *a):
    c = db.conn()
    try:
        return c.execute(sql, a).fetchall()
    finally:
        c.close()


def scalar(sql, *a):
    r = q(sql, *a)
    return r[0][0] if r else None
