"""Catalog upload + duplicate-detection decisions + per-catalog page."""
import os, re, json, datetime
from flask import Blueprint, request, redirect, render_template, flash, abort, send_file, Response
import db, storage, extractor, dedupe

bp = Blueprint("catalogs", __name__)
STALE_MINUTES = 10


def _utc():
    return datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def annotate(rows):
    """Adds `state`: the DB status, except that an extraction with no heartbeat for a while is shown as 'stale' (instance restarted)."""
    limit = (datetime.datetime.utcnow() - datetime.timedelta(minutes=STALE_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    out = []
    for r in rows:
        d = dict(r)
        beat = d.get("heartbeat") or d.get("created") or ""
        d["state"] = "stale" if d["status"] in ("processing", "checking") and beat < limit else d["status"]
        out.append(d)
    return out


def _unique_violation(e):
    return "unique" in str(e).lower() or "duplicate key" in str(e).lower()


@bp.route("/upload", methods=["POST"])
def upload():
    f = request.files.get("pdf")
    if not f or not f.filename.lower().endswith(".pdf"):
        flash("يرجى اختيار ملف PDF."); return redirect("/")
    data = f.read()
    if not data.startswith(b"%PDF"):
        flash("الملف المحدد ليس ملف PDF صالحًا."); return redirect("/")
    sha = dedupe.sha256_bytes(data)
    c = db.conn()
    try:
        old = dedupe.exact_match(c, sha)                                      # level 1: exact copy -> blocked
        if old:
            flash(f"سبق رفع هذا الكتالوج باسم «{old['name']}» بواسطة {old['uploaded_by'] or 'مستخدم غير معروف'} بتاريخ {old['created']}. لن يُرفع الملف مرة أخرى.")
            return redirect(f"/catalog/{old['id']}")
        supplier = request.form.get("supplier", "").strip()
        version = request.form.get("version", "").strip()
        cdate = request.form.get("catalog_date", "").strip()
        name = os.path.basename(f.filename.replace("\\", "/"))
        try:
            drive_id = storage.save_catalog_pdf(sha, name, data)
        except Exception as e:
            flash(f"تعذر حفظ الملف في مخزن الملفات: {e}"); return redirect("/")
        try:
            cid = c.execute("""INSERT INTO catalogs(name,supplier,file_sha,status,version,catalog_date,meta_source,uploaded_by,drive_id,size_bytes,heartbeat)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                            (name, supplier, sha, "checking", version, cdate, "user" if (version or cdate) else "", db.actor(), drive_id, len(data), _utc())).lastrowid
            db.log(c, "catalog", cid, "upload", f"{name} ({supplier})")
            c.commit()
        except Exception as e:                                                # two people uploading the same file at the same moment
            c.rollback()
            if _unique_violation(e):
                flash("سبق رفع هذا الكتالوج في الوقت نفسه؛ أُلغيت عملية الرفع المكررة."); return redirect("/")
            raise
    finally:
        c.close()
    extractor.start(cid, db.actor())
    return redirect(f"/catalog/{cid}")


def _row(c, cid):
    r = c.execute("SELECT * FROM catalogs WHERE id=?", (cid,)).fetchone()
    if not r:
        abort(404)
    return r


@bp.route("/catalog/<int:cid>")
def detail(cid):
    c = db.conn()
    try:
        cat = annotate([_row(c, cid)])[0]
        try:
            dup = json.loads(cat["dup_info"]) if cat["dup_info"] else {}
        except Exception:
            dup = {}
        prev = c.execute("SELECT id,name,supplier,version,catalog_date FROM catalogs WHERE id=?", (cat["previous_catalog_id"],)).fetchone() if cat["previous_catalog_id"] else None
        newer = c.execute("SELECT id,name,version,catalog_date FROM catalogs WHERE previous_catalog_id=?", (cid,)).fetchall()
        stats = c.execute("""SELECT (SELECT COUNT(*) FROM models WHERE catalog_id=?) models,
                                    (SELECT COUNT(*) FROM models WHERE catalog_id=? AND status='approved') approved,
                                    (SELECT COUNT(*) FROM specs s JOIN models m ON m.id=s.model_id WHERE m.catalog_id=?) specs,
                                    (SELECT COUNT(*) FROM specs s JOIN models m ON m.id=s.model_id WHERE m.catalog_id=? AND s.verified=0) unverified,
                                    (SELECT COUNT(*) FROM images WHERE catalog_id=?) images""", (cid, cid, cid, cid, cid)).fetchone()
        reviews = c.execute("SELECT * FROM review WHERE catalog_id=? ORDER BY resolved, kind, id", (cid,)).fetchall()
        similar = []
        for r in reviews:
            m = re.match(r"catalog (\d+)$", r["ref"] or "")
            if m and r["kind"] in ("SIMILAR_CATALOG", "LIKELY_UPDATE", "SUPERSEDES"):
                o = c.execute("SELECT id,name,version,catalog_date FROM catalogs WHERE id=?", (int(m.group(1)),)).fetchone()
                if o:
                    similar.append(dict(review=r, other=o))
        others = c.execute("SELECT id,name,supplier,version FROM catalogs WHERE id<>? AND file_sha<>'manual' ORDER BY id DESC", (cid,)).fetchall()
        hist = c.execute("SELECT * FROM audit WHERE entity='catalog' AND entity_id=? ORDER BY id DESC LIMIT 20", (cid,)).fetchall()
        flow = c.execute("""SELECT
            (SELECT COUNT(*) FROM review WHERE catalog_id=? AND resolved=0) open_reviews,
            (SELECT COUNT(*) FROM models m WHERE m.catalog_id=? AND m.active=1 AND m.status<>'approved' AND EXISTS (SELECT 1 FROM specs s WHERE s.model_id=m.id)) ready,
            (SELECT COUNT(*) FROM models m WHERE m.catalog_id=? AND m.active=1 AND NOT EXISTS (SELECT 1 FROM specs s WHERE s.model_id=m.id)) nospec,
            (SELECT COUNT(*) FROM models m WHERE m.catalog_id=? AND m.status='approved' AND NOT EXISTS
                (SELECT 1 FROM image_links l WHERE l.model_id=m.id AND l.ok_sheet=1)) approved_noimg""", (cid, cid, cid, cid)).fetchone()
    finally:
        c.close()
    return render_template("catalog", cat=cat, dup=dup, prev=prev, newer=newer, stats=stats, reviews=reviews, similar=similar, others=others, hist=hist, flow=flow)


@bp.route("/catalog/<int:cid>/meta", methods=["POST"])
def meta(cid):
    f = request.form
    c = db.conn()
    try:
        old = _row(c, cid)
        new = (f.get("supplier", "").strip(), f.get("version", "").strip(), f.get("catalog_date", "").strip())
        c.execute("UPDATE catalogs SET supplier=?, version=?, catalog_date=?, meta_source='user' WHERE id=?", (*new, cid))
        db.log(c, "catalog", cid, "edit", f"supplier '{old['supplier']}'->'{new[0]}', version '{old['version']}'->'{new[1]}', date '{old['catalog_date']}'->'{new[2]}'")
        c.commit()
    finally:
        c.close()
    flash("حُفظت بيانات الكتالوج. يُفحص التكرار عند الرفع؛ إذا تغيّر المورد أو الإصدار، فاربط الإصدار السابق يدويًا عند الحاجة.")
    return redirect(f"/catalog/{cid}")


@bp.route("/catalog/<int:cid>/continue", methods=["POST"])
def cont(cid):
    """The user looked at the duplicate warning and decided to process the file anyway."""
    c = db.conn()
    try:
        cat = _row(c, cid)
        if cat["status"] != "needs_decision":
            return redirect(f"/catalog/{cid}")
        db.log(c, "catalog", cid, "duplicate_accepted", cat["msg"] or "")
        c.execute("UPDATE catalogs SET status='processing', msg='', heartbeat=? WHERE id=?", (_utc(), cid)); c.commit()
    finally:
        c.close()
    extractor.start(cid, db.actor(), skip_checks=True)
    return redirect(f"/catalog/{cid}")


@bp.route("/catalog/<int:cid>/retry", methods=["POST"])
def retry(cid):
    c = db.conn()
    try:
        cat = annotate([_row(c, cid)])[0]
        if cat["state"] not in ("error", "stale"):
            flash("تتوفر إعادة المحاولة للكتالوجات التي فشل استخراجها أو توقف فقط."); return redirect(f"/catalog/{cid}")
        if c.execute("SELECT 1 FROM models WHERE catalog_id=? AND status='approved' LIMIT 1", (cid,)).fetchone():
            flash("يتضمن الكتالوج طرازات معتمدة؛ لذلك لا يمكن إعادة استخراجه."); return redirect(f"/catalog/{cid}")
        db.log(c, "catalog", cid, "retry", "")
        c.execute("UPDATE catalogs SET status='processing', msg='', heartbeat=? WHERE id=?", (_utc(), cid)); c.commit()
    finally:
        c.close()
    extractor.start(cid, db.actor(), skip_checks=True)
    return redirect(f"/catalog/{cid}")


@bp.route("/catalog/<int:cid>/delete", methods=["POST"])
def delete(cid):
    c = db.conn()
    try:
        _row(c, cid)
        msg = extractor.delete_catalog(c, cid)
    finally:
        c.close()
    if msg:
        flash(msg); return redirect(f"/catalog/{cid}")
    flash("حُذف الكتالوج وجميع البيانات المستخرجة منه.")
    return redirect("/")


@bp.route("/catalog/<int:cid>/previous", methods=["POST"])
def link_previous(cid):
    pid = request.form.get("previous_id") or None
    c = db.conn()
    try:
        _row(c, cid)
        if pid:
            pid = int(pid)
            seen, cur = set(), pid                                  # no cycles in the version chain
            while cur and cur not in seen:
                if cur == cid:
                    flash("سيؤدي هذا الربط إلى إنشاء دورة في تسلسل الإصدارات."); return redirect(f"/catalog/{cid}")
                seen.add(cur)
                r = c.execute("SELECT previous_catalog_id p FROM catalogs WHERE id=?", (cur,)).fetchone()
                cur = r["p"] if r else None
            _row(c, pid)
        c.execute("UPDATE catalogs SET previous_catalog_id=? WHERE id=?", (pid, cid))
        c.execute("UPDATE review SET resolved=1 WHERE catalog_id=? AND kind IN ('LIKELY_UPDATE','SIMILAR_CATALOG') AND resolved=0", (cid,)) if pid else None
        db.log(c, "catalog", cid, "link_previous" if pid else "unlink_previous", f"previous={pid}")
        c.commit()
    finally:
        c.close()
    return redirect(f"/catalog/{cid}")


@bp.route("/catalog/<int:cid>/not-duplicate", methods=["POST"])
def not_duplicate(cid):
    c = db.conn()
    try:
        _row(c, cid)
        c.execute("UPDATE review SET resolved=1 WHERE catalog_id=? AND kind IN ('SIMILAR_CATALOG','LIKELY_UPDATE') AND resolved=0", (cid,))
        db.log(c, "catalog", cid, "not_duplicate", "user decision")
        c.commit()
    finally:
        c.close()
    return redirect(f"/catalog/{cid}")


@bp.route("/catalog/<int:cid>/compare/<int:oid>")
def compare(cid, oid):
    c = db.conn()
    try:
        a, b = _row(c, cid), _row(c, oid)
        res = dedupe.compare(c, cid, oid)
    finally:
        c.close()
    return render_template("compare", a=a, b=b, r=res)


@bp.route("/catalog/<int:cid>/page/<int:n>.png")
def page_png(cid, n):
    """Source page of the catalog as an image: lets a person check an extracted spec against the original."""
    c = db.conn()
    try:
        cat = _row(c, cid)
    finally:
        c.close()
    d = os.path.join(db.CACHE_DIR, "pages"); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{cat['file_sha'][:16]}-{n}.png")
    if not os.path.exists(p):
        try:
            data = extractor.render_page_png(storage.catalog_path(cat["file_sha"], cat["drive_id"]), n)
        except Exception:
            abort(404)
        tmp = p + ".tmp"
        with open(tmp, "wb") as fh: fh.write(data)
        os.replace(tmp, p)
    resp = send_file(p, mimetype="image/png")
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@bp.route("/catalog/<int:cid>/file")
def original(cid):
    c = db.conn()
    try:
        cat = _row(c, cid)
    finally:
        c.close()
    try:
        return send_file(storage.catalog_path(cat["file_sha"], cat["drive_id"]), mimetype="application/pdf", download_name=cat["name"], as_attachment=True)
    except FileNotFoundError:
        abort(404)
