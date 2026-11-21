"""Product workspace: ONE page per product with tabs, a comparison page, and the marketing video library.

Tabs of /product/<id>:  overview | models | specs | images | video | export | history
Everything here reads existing tables; edits post to the existing, tested endpoints (studio spec/image/variant, product save),
which send the person back to the page they came from. New in this module: comparison and videos.
"""
import os, re, json, uuid, shutil, subprocess, tempfile, hashlib, datetime
from flask import Blueprint, request, render_template_string, redirect, flash, abort, jsonify, send_file, Response
import db, storage

bp = Blueprint("hub", __name__)

MAX_COMPARE = 6
VIDEO_MAX_MB = int(os.environ.get("VIDEO_MAX_MB", "500"))
CHUNK_MAX = 16 * 1024 * 1024
PLATFORMS = [("instagram", "Instagram"), ("tiktok", "TikTok"), ("youtube", "YouTube"), ("facebook", "Facebook"),
             ("x", "X"), ("linkedin", "LinkedIn"), ("snapchat", "Snapchat"), ("whatsapp", "WhatsApp")]
VIDEO_STATUS = [("draft", "مسودة"), ("ready", "جاهز للنشر"), ("published", "نُشر")]
TABS = [("overview", "نظرة عامة"), ("models", "الطرازات"), ("specs", "المواصفات"), ("images", "الصور"),
        ("video", "الفيديو"), ("export", "التصدير"), ("history", "السجل")]
THUMB_SQL = """(SELECT i.file FROM image_links l JOIN images i ON i.id=l.image_id
                WHERE l.model_id=m.id AND i.disabled=0 ORDER BY l.is_primary DESC, l.sort_order ASC LIMIT 1)"""


# ------------------------------------------------------------------ comparison data (also the spec matrix of a product)
def _norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def compare_data(c, ids):
    """Models side by side: rows = attributes (in the order they first appear), one value per model, `differs` when values are not all equal."""
    models, per = [], {}
    for mid in ids[:MAX_COMPARE]:
        m = c.execute(f"""SELECT m.id, m.code, m.name, m.series, m.product_id, p.name pname, {THUMB_SQL} thumb
                          FROM models m LEFT JOIN products p ON p.id=m.product_id WHERE m.id=?""", (mid,)).fetchone()
        if not m:
            continue
        models.append(dict(m)); vals = {}
        for s in c.execute("SELECT * FROM specs WHERE model_id=? AND deleted=0 AND COALESCE(hidden,0)=0 ORDER BY sort, id", (mid,)).fetchall():
            _, _, full = db.compute_final_spec(s)
            vals.setdefault(_norm(s["attr"]), (s["attr"], full or ""))
        per[mid] = vals
    order, labels = [], {}
    for m in models:
        for k, (label, _) in per[m["id"]].items():
            if k not in labels:
                labels[k] = label; order.append(k)
    rows = []
    for k in order:
        vs = [per[m["id"]].get(k, (None, ""))[1] for m in models]
        rows.append(dict(attr=labels[k], vals=vs, differs=len({_norm(v) for v in vs}) > 1))
    return models, rows


# ------------------------------------------------------------------ the workspace
@bp.route("/product/<int:pid>")
def workspace(pid):
    tab = request.args.get("tab", "overview")
    if tab not in dict(TABS):
        tab = "overview"
    c = db.conn()
    try:
        p = c.execute("SELECT p.*, f.name fam_name FROM products p LEFT JOIN families f ON f.id=p.family_id WHERE p.id=?", (pid,)).fetchone()
        if not p:
            abort(404)
        models = c.execute(f"""SELECT m.*, (SELECT COUNT(*) FROM specs s WHERE s.model_id=m.id AND s.deleted=0) spec_cnt, {THUMB_SQL} thumb,
                  (SELECT COUNT(*) FROM image_links l WHERE l.model_id=m.id AND l.ok_sheet=1) img_ok,
                  (SELECT COUNT(*) FROM product_videos v WHERE v.model_id=m.id AND v.active=1) vids
                  FROM models m WHERE m.product_id=? ORDER BY m.active DESC, m.sort_order ASC, m.code ASC""", (pid,)).fetchall()
        active = [m for m in models if m["active"]]
        counts = dict(
            models=len(active), approved=sum(1 for m in active if m["status"] == "approved"),
            specs=sum(m["spec_cnt"] for m in active),
            img_ok=sum(1 for m in active if m["img_ok"]),
            images=c.execute("SELECT COUNT(*) FROM product_image_links WHERE product_id=?", (pid,)).fetchone()[0],
            videos=c.execute("SELECT COUNT(*) FROM product_videos WHERE product_id=? AND active=1", (pid,)).fetchone()[0])
        ctx = dict(p=p, models=models, tab=tab, tabs=TABS, counts=counts, platforms=PLATFORMS, vstatus=VIDEO_STATUS,
                   vmax=VIDEO_MAX_MB, ff=bool(shutil.which("ffmpeg")))
        if tab == "overview":
            ctx["fams"] = c.execute("SELECT * FROM families ORDER BY name").fetchall()
            hero = c.execute("""SELECT i.file FROM product_image_links pl JOIN images i ON i.id=pl.image_id
                                WHERE pl.product_id=? AND i.disabled=0 ORDER BY pl.is_primary DESC, pl.sort_order LIMIT 1""", (pid,)).fetchone()
            ctx["hero"] = hero["file"] if hero else next((m["thumb"] for m in active if m["thumb"]), None)
            ctx["others"] = c.execute("SELECT id, name FROM products WHERE active=1 AND id<>? ORDER BY name", (pid,)).fetchall()
        if tab == "models":
            ctx["others"] = c.execute("SELECT id, name FROM products WHERE active=1 AND id<>? ORDER BY name", (pid,)).fetchall()
        if tab == "specs":
            ctx["cmp_models"], ctx["cmp_rows"] = compare_data(c, [m["id"] for m in active])
            ctx["prod_specs"] = []
            for s in c.execute("SELECT * FROM specs WHERE product_id=? AND model_id IS NULL ORDER BY deleted ASC, sort ASC, id ASC", (pid,)).fetchall():
                fv, fu, ffull = db.compute_final_spec(s)
                ctx["prod_specs"].append(dict(s, final_full=ffull))
        if tab == "images":
            ctx["prod_imgs"] = c.execute("""SELECT pl.*, i.file, i.w, i.h, i.page, i.disabled, c2.name cat_name
                FROM product_image_links pl JOIN images i ON i.id=pl.image_id LEFT JOIN catalogs c2 ON c2.id=i.catalog_id
                WHERE pl.product_id=? ORDER BY i.disabled ASC, pl.is_primary DESC, pl.sort_order ASC""", (pid,)).fetchall()
        if tab == "video":
            ctx["videos"] = c.execute("""SELECT v.*, m.code mcode FROM product_videos v LEFT JOIN models m ON m.id=v.model_id
                                         WHERE v.product_id=? AND v.active=1 ORDER BY v.id DESC""", (pid,)).fetchall()
            ctx["sel_model"] = request.args.get("model", "")
        if tab == "history":
            ctx["revisions"] = c.execute("""SELECT r.*, s.attr FROM spec_revisions r LEFT JOIN specs s ON s.id=r.specification_id
                                            WHERE r.product_id=? ORDER BY r.id DESC LIMIT 60""", (pid,)).fetchall()
            ctx["hist"] = c.execute("SELECT * FROM audit WHERE entity='product' AND entity_id=? ORDER BY id DESC LIMIT 30", (pid,)).fetchall()
    finally:
        c.close()
    return render_template_string(WORKSPACE, **ctx)


# ------------------------------------------------------------------ comparison page
@bp.route("/compare")
def compare():
    ids = [int(x) for x in request.args.get("ids", "").split(",") if x.strip().isdigit()]
    c = db.conn()
    try:
        prod = request.args.get("product", "")
        if prod.isdigit() and not ids:
            ids = [r["id"] for r in c.execute("SELECT id FROM models WHERE product_id=? AND active=1 ORDER BY sort_order, code", (int(prod),)).fetchall()]
        ids = list(dict.fromkeys(ids))[:MAX_COMPARE]
        models, rows = compare_data(c, ids)
        ids = [m["id"] for m in models]
        pool = c.execute("""SELECT m.id, m.code, p.name pname FROM models m LEFT JOIN products p ON p.id=m.product_id
                            WHERE m.active=1 ORDER BY m.code""").fetchall()
        profs = c.execute("SELECT id, name FROM profiles ORDER BY id").fetchall()
    finally:
        c.close()
    return render_template_string(COMPARE, models=models, rows=rows, ids=ids, pool=[r for r in pool if r["id"] not in ids],
                                  profs=profs, maxn=MAX_COMPARE, diff_n=sum(1 for r in rows if r["differs"]))


# ------------------------------------------------------------------ videos
def _sniff(head, filename):
    """Real container check (do not trust the file name): MP4/MOV have 'ftyp' at byte 4, WebM/Matroska starts with the EBML magic."""
    ext = os.path.splitext(filename or "")[1].lower()
    if head[4:8] == b"ftyp" and ext in (".mp4", ".m4v", ".mov"):
        return ("video/quicktime", "mov") if head[8:12] == b"qt  " else ("video/mp4", "mp4")
    if head[:4] == b"\x1a\x45\xdf\xa3" and ext in (".webm", ".mkv"):
        return "video/webm", "webm"
    return None, None


def _probe(path):
    """Duration / size / thumbnail with ffmpeg when it is installed (optional)."""
    info, thumb = {}, None
    if shutil.which("ffprobe"):
        try:
            out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
                                  "-of", "json", path], capture_output=True, text=True, timeout=90)
            j = json.loads(out.stdout or "{}")
            st = (j.get("streams") or [{}])[0]
            info = dict(width=int(st.get("width") or 0), height=int(st.get("height") or 0), duration=float((j.get("format") or {}).get("duration") or 0))
        except Exception:
            info = {}
    if shutil.which("ffmpeg"):
        fd, tmp = tempfile.mkstemp(suffix=".jpg"); os.close(fd)
        try:
            for ss in ("1", "0"):
                subprocess.run(["ffmpeg", "-y", "-ss", ss, "-i", path, "-frames:v", "1", "-vf", "scale=480:-2", tmp],
                               capture_output=True, timeout=90)
                if os.path.getsize(tmp) > 0:
                    thumb = open(tmp, "rb").read(); break
        except Exception:
            thumb = None
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    return info, thumb


def _plat(form):
    return ",".join(k for k, _ in PLATFORMS if k in form.getlist("platforms"))


def _next(default):
    n = request.form.get("next", "")
    return n if n.startswith("/") and not n.startswith("//") else default


@bp.route("/video/begin", methods=["POST"])
def video_begin():
    j = request.get_json(silent=True) or {}
    name, size, pid = str(j.get("filename", "")), int(j.get("size") or 0), j.get("product_id")
    if os.path.splitext(name)[1].lower() not in (".mp4", ".m4v", ".mov", ".webm", ".mkv"):
        return jsonify(error="الصيغ المقبولة: MP4 وMOV وWebM."), 400
    if size <= 0 or size > VIDEO_MAX_MB * 1024 * 1024:
        return jsonify(error=f"الحد الأقصى لحجم الفيديو {VIDEO_MAX_MB} ميغابايت."), 400
    c = db.conn()
    try:
        if not c.execute("SELECT 1 FROM products WHERE id=?", (pid,)).fetchone():
            return jsonify(error="المنتج غير موجود."), 404
        old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        c.execute("DELETE FROM upload_chunks WHERE upload_id IN (SELECT id FROM uploads WHERE created < ?)", (old,))
        c.execute("DELETE FROM uploads WHERE created < ?", (old,))
        uid = uuid.uuid4().hex
        c.execute("INSERT INTO uploads(id, filename, size, product_id) VALUES(?,?,?,?)", (uid, name, size, pid))
        c.commit()
    finally:
        c.close()
    return jsonify(id=uid)


@bp.route("/video/upload/<uid>/<int:idx>", methods=["PUT", "POST"])
def video_chunk(uid, idx):
    data = request.get_data()
    if not data or len(data) > CHUNK_MAX or not re.fullmatch(r"[0-9a-f]{32}", uid):
        return jsonify(error="قطعة غير صالحة."), 400
    c = db.conn()
    try:
        if not c.execute("SELECT 1 FROM uploads WHERE id=?", (uid,)).fetchone():
            return jsonify(error="جلسة الرفع غير موجودة."), 404
        c.execute("DELETE FROM upload_chunks WHERE upload_id=? AND idx=?", (uid, idx))          # a retried piece replaces the earlier one
        c.execute("INSERT INTO upload_chunks(upload_id, idx, data) VALUES(?,?,?)", (uid, idx, data))
        c.commit()
    finally:
        c.close()
    return jsonify(ok=True)


@bp.route("/video/upload/<uid>/complete", methods=["POST"])
def video_complete(uid):
    f = request.form
    c = db.conn()
    tmp = None
    try:
        up = c.execute("SELECT * FROM uploads WHERE id=?", (uid,)).fetchone()
        if not up:
            return jsonify(error="جلسة الرفع غير موجودة أو انتهت."), 404
        idxs = [r["idx"] for r in c.execute("SELECT idx FROM upload_chunks WHERE upload_id=? ORDER BY idx", (uid,)).fetchall()]
        if not idxs or idxs != list(range(len(idxs))):
            return jsonify(error="القطع ناقصة؛ أعد الرفع."), 400
        fd, tmp = tempfile.mkstemp(dir=db.CACHE_DIR, suffix=".part"); h = hashlib.sha256(); total = 0
        with os.fdopen(fd, "wb") as out:
            for i in idxs:
                blob = bytes(c.execute("SELECT data FROM upload_chunks WHERE upload_id=? AND idx=?", (uid, i)).fetchone()["data"])
                out.write(blob); h.update(blob); total += len(blob)
        if total != up["size"]:
            return jsonify(error="حجم الملف المستلم لا يطابق المعلن؛ أعد الرفع."), 400
        with open(tmp, "rb") as fh:
            mime, ext = _sniff(fh.read(16), up["filename"])
        if not mime:
            return jsonify(error="الملف ليس فيديو MP4 أو MOV أو WebM صالحًا."), 400
        sha = h.hexdigest()
        dup = c.execute("SELECT id FROM product_videos WHERE sha=? AND product_id=? AND active=1", (sha, up["product_id"])).fetchone()
        if dup:
            return jsonify(error="هذا الفيديو مرفوع لهذا المنتج من قبل.", id=dup["id"]), 409
        info, thumb = _probe(tmp)
        fname = f"v{sha[:20]}.{ext}"
        drive_id = storage.save_video(fname, tmp, mime)
        mid = f.get("model_id") if (f.get("model_id") or "").isdigit() else None
        title = (f.get("title") or os.path.splitext(up["filename"])[0]).strip()[:150]
        cur = c.execute("""INSERT INTO product_videos(product_id, model_id, title, caption, hashtags, platforms, status, kind, file, drive_id,
                           mime, size, duration, width, height, sha, uploaded_by) VALUES(?,?,?,?,?,?,?,'file',?,?,?,?,?,?,?,?,?)""",
                        (up["product_id"], mid, title, (f.get("caption") or "").strip(), (f.get("hashtags") or "").strip(), _plat(f),
                         f.get("status") if f.get("status") in dict(VIDEO_STATUS) else "draft", fname, drive_id, mime, total,
                         info.get("duration", 0), info.get("width", 0), info.get("height", 0), sha, db.actor()))
        vid = cur.lastrowid
        if thumb:                                                          # same connection: a second one would wait for this transaction's lock
            c.execute("INSERT INTO assets(key, name, mime, sha, size, data) VALUES(?,?,?,?,?,?)",
                      (f"vthumb:{vid}", f"{vid}.jpg", "image/jpeg", hashlib.sha256(thumb).hexdigest(), len(thumb), thumb))
        c.execute("DELETE FROM upload_chunks WHERE upload_id=?", (uid,)); c.execute("DELETE FROM uploads WHERE id=?", (uid,))
        db.log(c, "product", up["product_id"], "video", f"uploaded: {title} ({total // 1024} KB)")
        c.commit()
    finally:
        c.close()
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass
    return jsonify(id=vid, ok=True)


@bp.route("/product/<int:pid>/video/link", methods=["POST"])
def video_link(pid):
    f = request.form
    url = (f.get("url") or "").strip()
    if not re.match(r"^https?://[^\s]+$", url):
        flash("الصق رابطًا صحيحًا يبدأ بـ https://"); return redirect(_next(f"/product/{pid}?tab=video"))
    c = db.conn()
    try:
        if not c.execute("SELECT 1 FROM products WHERE id=?", (pid,)).fetchone():
            abort(404)
        mid = f.get("model_id") if (f.get("model_id") or "").isdigit() else None
        c.execute("""INSERT INTO product_videos(product_id, model_id, title, caption, hashtags, platforms, status, kind, url)
                     VALUES(?,?,?,?,?,?,?,'link',?)""", (pid, mid, (f.get("title") or url)[:150], (f.get("caption") or "").strip(),
                                                          (f.get("hashtags") or "").strip(), _plat(f), "draft", url))
        db.log(c, "product", pid, "video", "link added: " + url[:120]); c.commit()
    finally:
        c.close()
    flash("أُضيف رابط الفيديو.")
    return redirect(_next(f"/product/{pid}?tab=video"))


@bp.route("/video/<int:vid>/update", methods=["POST"])
def video_update(vid):
    f = request.form
    c = db.conn()
    try:
        v = c.execute("SELECT * FROM product_videos WHERE id=?", (vid,)).fetchone()
        if not v:
            abort(404)
        mid = f.get("model_id") if (f.get("model_id") or "").isdigit() else None
        c.execute("UPDATE product_videos SET title=?, caption=?, hashtags=?, platforms=?, status=?, model_id=? WHERE id=?",
                  ((f.get("title") or v["title"]).strip()[:150], (f.get("caption") or "").strip(), (f.get("hashtags") or "").strip(), _plat(f),
                   f.get("status") if f.get("status") in dict(VIDEO_STATUS) else v["status"], mid, vid))
        db.log(c, "product", v["product_id"], "video", f"updated #{vid}: {f.get('status', '')}"); c.commit()
        pid = v["product_id"]
    finally:
        c.close()
    flash("حُفظت بيانات الفيديو.")
    return redirect(_next(f"/product/{pid}?tab=video"))


@bp.route("/video/<int:vid>/delete", methods=["POST"])
def video_delete(vid):
    c = db.conn()
    try:
        v = c.execute("SELECT product_id, title FROM product_videos WHERE id=?", (vid,)).fetchone()
        if not v:
            abort(404)
        c.execute("UPDATE product_videos SET active=0 WHERE id=?", (vid,))        # soft delete: the file stays in the store
        db.log(c, "product", v["product_id"], "video", f"removed #{vid}: {v['title']}"); c.commit()
    finally:
        c.close()
    flash("أُزيل الفيديو من القائمة. الملف محفوظ في المخزن.")
    return redirect(_next(f"/product/{v['product_id']}?tab=video"))


def _video_row(vid):
    c = db.conn()
    try:
        v = c.execute("SELECT * FROM product_videos WHERE id=?", (vid,)).fetchone()
    finally:
        c.close()
    if not v:
        abort(404)
    return v


@bp.route("/video/<int:vid>/file")
def video_file(vid):
    v = _video_row(vid)
    if v["kind"] == "link":
        return redirect(v["url"])
    try:
        path = storage.video_path(v["file"], v["drive_id"])
    except FileNotFoundError:
        abort(404)
    ext = os.path.splitext(v["file"])[1]
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", v["title"] or "video").strip("_")[:60] or "video"
    return send_file(path, mimetype=v["mime"] or "video/mp4", conditional=True,                     # conditional = HTTP Range, so the player can seek
                     as_attachment=request.args.get("dl") == "1", download_name=safe + ext)


@bp.route("/video/<int:vid>/thumb")
def video_thumb(vid):
    c = db.conn()
    try:
        r = c.execute("SELECT data, mime FROM assets WHERE key=?", (f"vthumb:{vid}",)).fetchone()
    finally:
        c.close()
    if not r:
        abort(404)
    return Response(bytes(r["data"]), mimetype=r["mime"] or "image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@bp.route("/product/<int:pid>/video/caption")
def video_caption(pid):
    """Draft post text from the facts in the specification table (no invented claims)."""
    c = db.conn()
    try:
        p = c.execute("SELECT name, ar_name FROM products WHERE id=?", (pid,)).fetchone()
        if not p:
            abort(404)
        mid = request.args.get("model_id", "")
        m = c.execute("SELECT id, code FROM models WHERE id=? AND product_id=?", (int(mid), pid)).fetchone() if mid.isdigit() else \
            c.execute("SELECT id, code FROM models WHERE product_id=? AND active=1 ORDER BY sort_order, code LIMIT 1", (pid,)).fetchone()
        lines = []
        if m:
            for s in c.execute("SELECT * FROM specs WHERE model_id=? AND deleted=0 AND COALESCE(hidden,0)=0 ORDER BY sort, id LIMIT 6", (m["id"],)).fetchall():
                _, _, full = db.compute_final_spec(s)
                if full:
                    lines.append(f"• {s['attr']}: {full}")
    finally:
        c.close()
    head = (p["ar_name"] or p["name"]) + (f" - {m['code']}" if m else "")
    tags = ["#" + re.sub(r"[^\w]+", "", p["name"])[:30]] + (["#" + re.sub(r"[^\w]+", "", m["code"])] if m else [])
    return jsonify(caption=head + ("\n" + "\n".join(lines) if lines else ""), hashtags=" ".join(t for t in tags if len(t) > 1))


@bp.route("/videos")
def videos_library():
    q, st = request.args.get("q", "").strip(), request.args.get("status", "")
    where, a = ["v.active=1"], []
    if q:
        where.append("(v.title LIKE ? OR v.caption LIKE ? OR p.name LIKE ? OR m.code LIKE ?)"); a += [f"%{q}%"] * 4
    if st in dict(VIDEO_STATUS):
        where.append("v.status=?"); a.append(st)
    c = db.conn()
    try:
        rows = c.execute(f"""SELECT v.*, p.name pname, m.code mcode FROM product_videos v LEFT JOIN products p ON p.id=v.product_id
                             LEFT JOIN models m ON m.id=v.model_id WHERE {' AND '.join(where)} ORDER BY v.id DESC LIMIT 200""", a).fetchall()
        n_all = c.execute("SELECT COUNT(*) FROM product_videos WHERE active=1").fetchone()[0]
    finally:
        c.close()
    return render_template_string(VIDEOS, rows=rows, q=q, st=st, vstatus=VIDEO_STATUS, platforms=dict(PLATFORMS), n_all=n_all)


# ------------------------------------------------------------------ templates
_PLAT_CHECKS = """{% for k, n in platforms %}<label class="pl"><input type="checkbox" name="platforms" value="{{ k }}" {{ 'checked' if k in (pl or '').split(',') }}> {{ n }}</label>{% endfor %}"""

WORKSPACE = """{% extends "base" %}{% block head %}<style>
.ph{display:flex;gap:14px;align-items:center;flex-wrap:wrap}.ph h2{margin:0}.ph .sp{flex:1}
.ptabs{display:flex;gap:4px;overflow-x:auto;margin:12px 0 14px;border-bottom:1px solid var(--line);scrollbar-width:none}
.ptabs a{white-space:nowrap;text-decoration:none;color:var(--muted);padding:9px 14px;border-bottom:3px solid transparent;margin-bottom:-1px}
.ptabs a[aria-current=page]{color:var(--ink);border-color:var(--accent);font-weight:600}
.ptabs .n{background:#e3e7ef;border-radius:9px;padding:0 7px;font-size:12px;margin-inline-start:4px;color:var(--ink)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px}
.tile{background:#fff;border:1px solid var(--line);border-radius:10px;padding:10px 12px}.tile b{display:block;font-size:22px}.tile span{color:var(--muted);font-size:13px}
.two{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.3fr);gap:16px;align-items:start}.two .card{margin:0}
.ready li{margin:6px 0;list-style:none}.ready{padding:0;margin:0}
.f2{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}.f2 label{display:block;font-size:14px;font-weight:600}
.f2 input,.f2 select,.f2 textarea{width:100%;font-weight:400;margin-top:3px}
.vcard{display:grid;grid-template-columns:minmax(220px,320px) 1fr;gap:16px;align-items:start}
.vcard video,.vcard .poster{width:100%;border-radius:10px;background:#111;max-height:340px}
.f2 input[type=checkbox]{width:auto;margin:0}
.pl{display:inline-flex!important;align-items:center;gap:6px;margin-inline-end:14px;font-weight:400!important;font-size:14px}
.bar{height:8px;background:#e3e7ef;border-radius:6px;overflow:hidden;margin-top:8px}.bar i{display:block;height:100%;width:0;background:var(--accent);transition:width .2s}
.mx{overflow-x:auto}.mx table{min-width:520px}.mx td:first-child,.mx th:first-child{position:sticky;inset-inline-start:0;background:#fff;font-weight:600;min-width:150px}
.mx tr.d td{background:#fff8e6}.mx tr.d td:first-child{background:#fff4d6}
.imgs .im.off{opacity:.45}
@media(max-width:860px){.two,.vcard{grid-template-columns:1fr}}
</style>{% endblock %}
{% block body %}
<div class="crumb"><a href="/products">المنتجات</a><span>›</span><b>{{ p.name }}</b><span class="sp"></span>
<a class="btn sec" href="/compare?product={{ p.id }}">قارن الطرازات</a><a class="btn" href="/export-center/?doc_type=tds&product_id={{ p.id }}">ورقة بيانات</a></div>
<div class="card"><div class="ph"><h2>{{ p.name }}</h2>{% if p.ar_name %}<span class="muted">{{ p.ar_name }}</span>{% endif %}
{% if p.suggested %}<span class="tag y">مقترح: راجعه ثم احفظه</span>{% endif %}{% if not p.active %}<span class="tag r">مؤرشف</span>{% endif %}
<span class="tag {{ 'g' if p.status=='approved' else 'y' }}">{{ 'معتمد' if p.status=='approved' else 'مسودة' }}</span><span class="sp"></span>
<span class="muted">{{ p.fam_name or 'بلا تصنيف' }}{% if p.supplier %} · {{ p.supplier }}{% endif %}</span></div></div>
<nav class="ptabs" aria-label="أقسام المنتج">{% for k, n in tabs %}<a href="/product/{{ p.id }}?tab={{ k }}"{% if k==tab %} aria-current="page"{% endif %}>{{ n }}{% if k=='models' %}<span class="n">{{ counts.models }}</span>{% elif k=='video' %}<span class="n">{{ counts.videos }}</span>{% elif k=='images' %}<span class="n">{{ counts.images }}</span>{% endif %}</a>{% endfor %}</nav>

{% if tab=='overview' %}
<div class="tiles" style="margin-bottom:16px"><div class="tile"><b>{{ counts.models }}</b><span>طراز ({{ counts.approved }} معتمد)</span></div><div class="tile"><b>{{ counts.specs }}</b><span>مواصفة</span></div>
<div class="tile"><b>{{ counts.img_ok }}/{{ counts.models }}</b><span>طراز بصورة معتمدة</span></div><div class="tile"><b>{{ counts.videos }}</b><span>فيديو</span></div></div>
<div class="two"><section class="card"><h3>جاهزية المنتج</h3><ul class="ready">
<li>{{ '✅' if counts.models else '⬜' }} طرازات: {{ counts.models }} {% if not counts.models %}<a href="/product/{{ p.id }}?tab=models">أضف طرازًا</a>{% endif %}</li>
<li>{{ '✅' if counts.models and counts.approved==counts.models else '⬜' }} معتمدة: {{ counts.approved }} من {{ counts.models }} {% if counts.models and counts.approved<counts.models %}<a href="/models?product={{ p.id }}&status=staging">راجع واعتمد</a>{% endif %}</li>
<li>{{ '✅' if counts.models and counts.img_ok==counts.models else '⬜' }} صور معتمدة: {{ counts.img_ok }} من {{ counts.models }} {% if counts.models and counts.img_ok<counts.models %}<a href="/studio/unlinked-images">اربط الصور</a>{% endif %}</li>
<li>{{ '✅' if counts.specs else '⬜' }} مواصفات: {{ counts.specs }} {% if not counts.specs %}<a href="/product/{{ p.id }}?tab=specs">أضف</a>{% endif %}</li>
<li>{{ '✅' if counts.videos else '⬜' }} فيديو تسويقي: {{ counts.videos }} {% if not counts.videos %}<a href="/product/{{ p.id }}?tab=video">ارفع فيديو</a>{% endif %}</li></ul>
{% if hero %}<img src="/img/{{ hero }}" alt="" style="max-width:100%;max-height:220px;object-fit:contain;display:block;margin:12px auto 0">{% endif %}</section>
<section class="card"><h3>بيانات المنتج</h3>
<form method="post" action="/product/{{ p.id }}/save" class="f2"><input type="hidden" name="version" value="{{ p.version or 1 }}"><input type="hidden" name="next" value="/product/{{ p.id }}">
<label>الاسم الإنجليزي<input name="name" value="{{ p.name }}"></label><label>الاسم العربي<input name="ar_name" value="{{ p.ar_name }}"></label>
<label>المورد<input name="supplier" value="{{ p.supplier }}"></label><label>الشركة المصنعة<input name="manufacturer" value="{{ p.manufacturer }}"></label>
<label>التصنيف<select name="family_id"><option value="">—</option>{% for f in fams %}<option value="{{ f.id }}" {{ 'selected' if p.family_id==f.id }}>{{ f.name }}</option>{% endfor %}</select></label>
<label>الحالة<select name="status"><option value="staging" {{ 'selected' if p.status=='staging' }}>مسودة</option><option value="approved" {{ 'selected' if p.status=='approved' }}>معتمد</option></select></label>
<label style="grid-column:1/-1">الوصف العام<textarea name="description" rows="3">{{ p.description }}</textarea></label>
<div style="grid-column:1/-1"><button>حفظ</button></div></form>
<div style="margin-top:14px;display:flex;gap:8px;flex-wrap:wrap">
<form method="post" action="/studio/product/{{ p.id }}/update"><input type="hidden" name="next" value="/product/{{ p.id }}">{% if p.active %}<button class="sec" name="act" value="disable" title="يخفي المنتج دون حذف بياناته">تعطيل المنتج</button>{% else %}<button name="act" value="restore">استعادة المنتج</button>{% endif %}</form>
<form method="post" action="/product/{{ p.id }}/move"><select name="target"><option value="">— دون منتج —</option>{% for x in others %}<option value="{{ x.id }}">{{ x.name }}</option>{% endfor %}</select> <button class="sec">نقل كل الطرازات</button></form>
{% if not counts.models %}<form method="post" action="/product/{{ p.id }}/archive"><button class="red">أرشفة (منتج فارغ)</button></form>{% endif %}</div></section></div>

{% elif tab=='models' %}
<section class="card"><h3>الطرازات ({{ counts.models }})</h3>
<form method="post" action="/studio/product/{{ p.id }}/variant/add" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px"><input type="hidden" name="next" value="/product/{{ p.id }}?tab=models">
<input name="code" placeholder="كود الطراز (مثال: GQ40)" required><input name="vname" placeholder="الاسم"><input name="ar_name" placeholder="الاسم العربي"><input name="series" placeholder="السلسلة"><button>إضافة طراز</button></form>
<div class="mx"><table><tr><th></th><th>الصورة</th><th>الكود</th><th>السلسلة</th><th>الحالة</th><th>مواصفات</th><th>فيديو</th><th></th></tr>
{% for m in models %}<tr style="{{ 'opacity:.55' if not m.active }}"><td><input type="checkbox" class="pick" value="{{ m.id }}"></td>
<td>{% if m.thumb %}<img src="/img/{{ m.thumb }}" style="height:44px;width:60px;object-fit:contain" alt="">{% else %}—{% endif %}</td>
<td class="ltr"><a href="/model/{{ m.id }}"><b>{{ m.code }}</b></a></td><td class="ltr" style="font-size:13px">{{ m.series }}</td>
<td>{% if m.status=='approved' %}<span class="tag g">معتمد</span>{% else %}<span class="tag y">مراجعة</span>{% endif %}{% if not m.active %} <span class="tag r">معطّل</span>{% endif %}</td>
<td>{{ m.spec_cnt }}</td><td>{{ m.vids }}</td>
<td style="white-space:nowrap"><a href="/studio/variant/{{ m.id }}">تعديل المواصفات</a> · <a href="/datasheet/{{ m.id }}.pdf" target="_blank">ورقة بيانات</a></td></tr>
{% else %}<tr><td colspan="8" class="muted">لا طرازات بعد.</td></tr>{% endfor %}</table></div>
<div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap"><button type="button" onclick="pk('/compare?ids=')">قارن المحدد</button>
<button type="button" class="sec" onclick="pk('/export-center/?model_ids=')">تصدير المحدد...</button></div></section>
<script>function pk(u){var v=Array.prototype.map.call(document.querySelectorAll('.pick:checked'),function(x){return x.value}).join(',');if(!v){alert('اختر طرازًا واحدًا على الأقل');return}location.href=u+v}</script>

{% elif tab=='specs' %}
<section class="card"><h3>مصفوفة المواصفات</h3>
<p class="muted" style="margin-top:0">كل الطرازات جنبًا إلى جنب؛ الصفوف المظللة فيها اختلاف. للتعديل افتح الطراز من رأس العمود.</p>
{% if cmp_models %}<div class="mx"><table><tr><th>المواصفة</th>{% for m in cmp_models %}<th class="ltr"><a href="/studio/variant/{{ m.id }}">{{ m.code }}</a></th>{% endfor %}</tr>
{% for r in cmp_rows %}<tr class="{{ 'd' if r.differs }}"><td>{{ r.attr }}</td>{% for v in r.vals %}<td class="ltr">{{ v or '—' }}</td>{% endfor %}</tr>{% else %}<tr><td colspan="9" class="muted">لا مواصفات بعد.</td></tr>{% endfor %}</table></div>
{% else %}<p class="muted">أضف طرازات أولًا.</p>{% endif %}</section>
<section class="card"><h3>مواصفات مشتركة لكل المنتج</h3>
<form method="post" action="/studio/spec/mutate" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px"><input type="hidden" name="op" value="ADD"><input type="hidden" name="product_id" value="{{ p.id }}">
<input name="attr" placeholder="اسم المواصفة" required><input name="val" placeholder="القيمة" required><input name="unit" placeholder="الوحدة" style="width:90px"><button>إضافة مواصفة</button></form>
<div class="mx"><table><tr><th>المواصفة</th><th>قيمة المورد</th><th>قيمتنا</th><th>الوحدة</th><th>النهائي</th><th>مخفية</th><th>ترتيب</th><th></th><th></th></tr>
{% for s in prod_specs %}<tr style="{{ 'opacity:.5' if s.deleted }}"><form method="post" action="/studio/spec/mutate"><input type="hidden" name="op" value="UPDATE"><input type="hidden" name="spec_id" value="{{ s.id }}"><input type="hidden" name="product_id" value="{{ p.id }}">
<td><input name="attr" value="{{ s.attr }}" style="width:140px"></td><td class="muted ltr">{{ s.supplier_value }} {{ s.supplier_unit }}</td>
<td><input name="company_value" value="{{ s.company_value }}" style="width:110px"></td><td><input name="company_unit" value="{{ s.company_unit }}" style="width:65px"></td>
<td><b class="ok ltr">{{ s.final_full }}</b></td><td><input type="checkbox" name="hidden" value="1" {{ 'checked' if s.hidden }}></td><td><input type="number" name="sort" value="{{ s.sort }}" style="width:55px"></td><td><button>حفظ</button></td></form>
<td>{% if not s.deleted %}<form method="post" action="/studio/spec/mutate" style="display:inline"><input type="hidden" name="op" value="DELETE"><input type="hidden" name="spec_id" value="{{ s.id }}"><input type="hidden" name="product_id" value="{{ p.id }}"><button class="red">حذف</button></form>
{% else %}<form method="post" action="/studio/spec/mutate" style="display:inline"><input type="hidden" name="op" value="RESTORE"><input type="hidden" name="spec_id" value="{{ s.id }}"><input type="hidden" name="product_id" value="{{ p.id }}"><button>استعادة</button></form>{% endif %}</td></tr>
{% else %}<tr><td colspan="9" class="muted">لا مواصفات مشتركة.</td></tr>{% endfor %}</table></div></section>

{% elif tab=='images' %}
<section class="card"><h3>صور المنتج</h3>
<form method="post" action="/studio/image/upload" enctype="multipart/form-data" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px"><input type="hidden" name="product_id" value="{{ p.id }}">
<input type="file" name="file" accept="image/*" required><select name="image_role"><option value="main_product">صورة المنتج الرئيسية</option><option value="additional">صورة إضافية</option><option value="detail">تفصيل</option><option value="technical_drawing">رسم فني</option><option value="diagram">مخطط</option></select>
<label><input type="checkbox" name="is_primary" value="1" checked> رئيسية</label><button>رفع صورة</button></form>
<div class="imgs">{% for im in prod_imgs %}<div class="im {{ 'off' if im.disabled }}"><img src="/img/{{ im.file }}" alt=""><div><span class="tag">{{ im.image_role }}</span> {% if im.is_primary %}<span class="tag g">رئيسية ★</span>{% endif %}</div>
<div class="muted" style="font-size:12px">{{ im.w }}×{{ im.h }} · {{ im.cat_name or 'يدوي' }}{% if im.page %} ص{{ im.page }}{% endif %}</div>
<form method="post" action="/studio/image/manage" style="margin-top:4px;display:flex;gap:4px;flex-wrap:wrap"><input type="hidden" name="image_id" value="{{ im.image_id }}"><input type="hidden" name="product_id" value="{{ p.id }}">
<button name="act" value="set_primary">رئيسية</button><button class="sec" name="act" value="{{ 'restore' if im.disabled else 'disable' }}">{{ 'استعادة' if im.disabled else 'تعطيل' }}</button></form></div>
{% else %}<p class="muted">لا صور على مستوى المنتج. صور الطرازات تظهر في صفحة كل طراز.</p>{% endfor %}</div>
<p style="margin:12px 0 0"><a href="/studio/unlinked-images">ورشة الصور: ربط الصور المستخرجة بالطرازات</a></p></section>
<section class="card"><h3>صور الطرازات</h3><div class="imgs">{% for m in models if m.thumb %}<div class="im"><img src="/img/{{ m.thumb }}" alt=""><div class="ltr"><a href="/model/{{ m.id }}"><b>{{ m.code }}</b></a></div></div>{% else %}<p class="muted">لا صور معتمدة للطرازات بعد.</p>{% endfor %}</div></section>

{% elif tab=='video' %}
<section class="card"><h3>إضافة فيديو تسويقي</h3>
<p class="muted" style="margin-top:0">فيديو للماكينة يُحفظ مع بياناتها لاستخدامه لاحقًا في التسويق على مواقع التواصل. MP4 أو MOV أو WebM حتى {{ vmax }} ميغابايت، ويُرفع على دفعات فلا ينقطع على الاتصال الضعيف.</p>
<form id="vf" class="f2" onsubmit="return false">
<label>الملف<input type="file" id="vfile" accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.m4v,.webm" required></label>
<label>العنوان<input name="title" placeholder="مثال: GQ40 قطع حديد 28 مم"></label>
<label>الطراز (اختياري)<select name="model_id" id="vmodel"><option value="">كل المنتج</option>{% for m in models if m.active %}<option value="{{ m.id }}" {{ 'selected' if sel_model==m.id|string }}>{{ m.code }}</option>{% endfor %}</select></label>
<label>الحالة<select name="status">{% for k, n in vstatus %}<option value="{{ k }}">{{ n }}</option>{% endfor %}</select></label>
<label style="grid-column:1/-1">نص المنشور <textarea name="caption" id="vcap" rows="3"></textarea></label>
<label style="grid-column:1/-1">الوسوم <input name="hashtags" id="vtags" placeholder="#وسم1 #وسم2"></label>
<div style="grid-column:1/-1">{% set pl = '' %}""" + _PLAT_CHECKS + """</div>
<div style="grid-column:1/-1;display:flex;gap:8px;flex-wrap:wrap;align-items:center"><button id="vgo" type="button">رفع الفيديو</button><button type="button" class="sec" id="vsug">اقتراح النص من المواصفات</button><span id="vmsg" class="muted"></span></div>
<div class="bar" id="vbar" hidden style="grid-column:1/-1"><i></i></div></form>
<details style="margin-top:12px"><summary class="muted">أو أضف رابطًا (YouTube أو Drive أو غيرهما)</summary>
<form method="post" action="/product/{{ p.id }}/video/link" class="f2" style="margin-top:8px"><input type="hidden" name="next" value="/product/{{ p.id }}?tab=video">
<label>الرابط<input name="url" placeholder="https://" required></label><label>العنوان<input name="title"></label><button>إضافة الرابط</button></form></details></section>
<script>
(function(){var PID={{ p.id }},CH=8*1024*1024,go=document.getElementById('vgo'),msg=document.getElementById('vmsg'),bar=document.getElementById('vbar'),f=document.getElementById('vf');
function say(t){msg.textContent=t}function sleep(ms){return new Promise(function(r){setTimeout(r,ms)})}
document.getElementById('vsug').onclick=function(){fetch('/product/'+PID+'/video/caption?model_id='+document.getElementById('vmodel').value).then(function(r){return r.json()}).then(function(d){document.getElementById('vcap').value=d.caption;document.getElementById('vtags').value=d.hashtags})};
go.onclick=async function(){var file=document.getElementById('vfile').files[0];if(!file){say('اختر ملف الفيديو أولًا.');return}
 go.disabled=true;bar.hidden=false;var fill=bar.firstElementChild;
 try{var b=await fetch('/video/begin',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({filename:file.name,size:file.size,product_id:PID})});var j=await b.json();if(!b.ok){throw new Error(j.error)}
  var n=Math.ceil(file.size/CH);
  for(var i=0;i<n;i++){var ok=false;for(var t=0;t<4&&!ok;t++){try{var r=await fetch('/video/upload/'+j.id+'/'+i,{method:'PUT',body:file.slice(i*CH,(i+1)*CH)});ok=r.ok}catch(e){ok=false}if(!ok)await sleep(800*(t+1))}
   if(!ok)throw new Error('انقطع الرفع؛ أعد المحاولة.');fill.style.width=Math.round((i+1)/n*100)+'%';say('جارٍ الرفع '+Math.round((i+1)/n*100)+'%')}
  say('جارٍ الحفظ...');var fd=new FormData(f);var c=await fetch('/video/upload/'+j.id+'/complete',{method:'POST',body:fd});var d=await c.json();if(!c.ok)throw new Error(d.error);
  location.href='/product/'+PID+'?tab=video'}
 catch(e){say(e.message||'تعذّر الرفع');go.disabled=false}};})();
</script>
{% for v in videos %}<section class="card vcard" id="v{{ v.id }}">
<div>{% if v.kind=='file' %}<video controls preload="metadata" playsinline {% if v.id %}poster="/video/{{ v.id }}/thumb"{% endif %}><source src="/video/{{ v.id }}/file" type="{{ v.mime }}"></video>
{% else %}<div class="poster" style="display:grid;place-items:center;height:180px"><a class="btn" href="{{ v.url }}" target="_blank" rel="noopener">فتح الرابط</a></div>{% endif %}
<div class="muted" style="font-size:13px;margin-top:6px">{% if v.kind=='file' %}{{ (v.size/1048576)|round(1) }} MB{% if v.width %} · {{ v.width }}×{{ v.height }}{% if v.height>v.width %} · عمودي{% endif %}{% endif %}{% if v.duration %} · {{ v.duration|round|int }} ث{% endif %}{% else %}رابط خارجي{% endif %} · {{ v.created }}</div></div>
<div><form method="post" action="/video/{{ v.id }}/update" class="f2"><input type="hidden" name="next" value="/product/{{ p.id }}?tab=video">
<label>العنوان<input name="title" value="{{ v.title }}"></label>
<label>الطراز<select name="model_id"><option value="">كل المنتج</option>{% for m in models if m.active %}<option value="{{ m.id }}" {{ 'selected' if v.model_id==m.id }}>{{ m.code }}</option>{% endfor %}</select></label>
<label>الحالة<select name="status">{% for k, n in vstatus %}<option value="{{ k }}" {{ 'selected' if v.status==k }}>{{ n }}</option>{% endfor %}</select></label>
<label style="grid-column:1/-1">نص المنشور<textarea name="caption" rows="3" id="cap{{ v.id }}">{{ v.caption }}</textarea></label>
<label style="grid-column:1/-1">الوسوم<input name="hashtags" value="{{ v.hashtags }}" id="tag{{ v.id }}"></label>
<div style="grid-column:1/-1">{% set pl = v.platforms %}""" + _PLAT_CHECKS + """</div>
<div style="grid-column:1/-1;display:flex;gap:8px;flex-wrap:wrap"><button>حفظ</button>
<button type="button" class="sec" onclick="navigator.clipboard.writeText(document.getElementById('cap{{ v.id }}').value+'\\n\\n'+document.getElementById('tag{{ v.id }}').value);this.textContent='نُسخ'">نسخ النص والوسوم</button>
{% if v.kind=='file' %}<a class="btn sec" href="/video/{{ v.id }}/file?dl=1">تنزيل الفيديو</a>{% endif %}</div></form>
<form method="post" action="/video/{{ v.id }}/delete" style="margin-top:8px" onsubmit="return confirm('إزالة الفيديو من القائمة؟')"><input type="hidden" name="next" value="/product/{{ p.id }}?tab=video"><button class="red">إزالة</button></form></div></section>
{% else %}<div class="card"><p class="muted" style="margin:0">لا فيديوهات لهذا المنتج بعد.</p></div>{% endfor %}

{% elif tab=='export' %}
<div class="two"><section class="card"><h3>مستندات المنتج</h3>
<p><a class="btn" href="/export-center/?doc_type=tds&product_id={{ p.id }}">ورقة بيانات المنتج</a></p>
<p><a class="btn sec" href="/export-center/?doc_type=product_catalog&product_id={{ p.id }}">كتالوج المنتج</a></p>
<form method="post" action="/pro/family-matrix.pdf" target="_blank" style="margin-bottom:10px"><input type="hidden" name="product_id" value="{{ p.id }}"><button class="sec">مصفوفة مقارنة المنتج PDF</button></form>
<p><a class="btn sec" href="/compare?product={{ p.id }}">قارن الطرازات على الشاشة</a></p></section>
<section class="card"><h3>ورقة بيانات لكل طراز</h3><table>{% for m in models if m.active %}<tr><td class="ltr"><b>{{ m.code }}</b></td><td><a href="/datasheet/{{ m.id }}.pdf" target="_blank">PDF مباشر</a> · <a href="/export-center/?doc_type=tds&model_id={{ m.id }}">خيارات</a></td></tr>{% else %}<tr><td class="muted">لا طرازات.</td></tr>{% endfor %}</table></section></div>

{% elif tab=='history' %}
<section class="card"><h3>سجل المنتج</h3><table>{% for h in hist %}<tr><td class="ltr" style="font-size:12px">{{ h.ts }}</td><td>{{ h.username or '—' }}</td><td>{{ h.action }}</td><td>{{ h.detail }}</td></tr>{% else %}<tr><td class="muted">لا أحداث.</td></tr>{% endfor %}</table></section>
<section class="card"><h3>سجل تعديلات المواصفات</h3><table><tr><th>التاريخ</th><th>العملية</th><th>المواصفة</th><th>السابق</th><th>الجديد</th><th>المنفذ</th></tr>
{% for r in revisions %}<tr><td class="ltr" style="font-size:12px">{{ r.modified_at }}</td><td><b>{{ r.operation }}</b></td><td>{{ r.attr }}</td><td class="ltr">{{ r.old_value }}</td><td class="ltr">{{ r.new_value }}</td><td>{{ r.modified_by }}</td></tr>{% else %}<tr><td colspan="6" class="muted">لا تعديلات.</td></tr>{% endfor %}</table></section>
{% endif %}
{% endblock %}"""

COMPARE = """{% extends "base" %}{% block head %}<style>
.cmp{overflow-x:auto}.cmp table{min-width:560px}.cmp th,.cmp td{vertical-align:top}
.cmp td:first-child,.cmp th:first-child{position:sticky;inset-inline-start:0;background:#fff;font-weight:600;min-width:150px;z-index:1}
.cmp tr.d td{background:#fff8e6}.cmp tr.d td:first-child{background:#fff4d6}
.cmp .hd{text-align:center;min-width:170px}.cmp .hd img{height:90px;max-width:150px;object-fit:contain;display:block;margin:0 auto 6px}
.cmp.only tr:not(.d):not(.h){display:none}
</style>{% endblock %}{% block body %}
<div class="card"><h2 style="margin:0 0 8px">مقارنة الطرازات</h2>
<form method="get" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center"><input type="hidden" name="ids" value="{{ ids|join(',') }}">
{% if ids|length < maxn %}<select onchange="if(this.value){location.href='/compare?ids={{ ids|join(',') }}{{ ',' if ids else '' }}'+this.value}"><option value="">+ أضف طرازًا للمقارنة...</option>{% for m in pool %}<option value="{{ m.id }}">{{ m.code }}{% if m.pname %} — {{ m.pname }}{% endif %}</option>{% endfor %}</select>{% else %}<span class="muted">بلغت الحد الأقصى ({{ maxn }}).</span>{% endif %}
{% if rows %}<label><input type="checkbox" onchange="document.getElementById('cm').classList.toggle('only',this.checked)"> الفروق فقط ({{ diff_n }})</label>{% endif %}</form></div>
{% if models|length < 2 %}<div class="card"><p class="muted" style="margin:0">اختر طرازين على الأقل. يمكنك تحديد طرازات من <a href="/search">البحث</a> أو <a href="/models">قائمة الطرازات</a> أو تبويب الطرازات في صفحة المنتج ثم الضغط على «قارن المحدد».</p></div>{% endif %}
{% if models %}<div class="card cmp" id="cm"><table><tr class="h"><th></th>{% for m in models %}<th class="hd">{% if m.thumb %}<img src="/img/{{ m.thumb }}" alt="">{% endif %}<a class="ltr" href="/model/{{ m.id }}"><b>{{ m.code }}</b></a><div class="muted" style="font-weight:400;font-size:12px">{{ m.pname or '' }}</div>
<a href="/compare?ids={{ ids|reject('equalto', m.id)|join(',') }}" style="font-size:12px;font-weight:400">إزالة</a></th>{% endfor %}</tr>
{% for r in rows %}<tr class="{{ 'd' if r.differs }}"><td>{{ r.attr }}</td>{% for v in r.vals %}<td class="ltr">{{ v or '—' }}</td>{% endfor %}</tr>{% else %}<tr><td colspan="9" class="muted">لا مواصفات لهذه الطرازات.</td></tr>{% endfor %}</table></div>
{% if models|length >= 2 %}<div class="card" style="display:flex;gap:10px;flex-wrap:wrap;align-items:center"><form method="post" action="/pro/compare.pdf" target="_blank" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
{% for i in ids %}<input type="hidden" name="model_ids" value="{{ i }}">{% endfor %}<select name="profile_id">{% for pr in profs %}<option value="{{ pr.id }}">{{ pr.name }}</option>{% endfor %}</select><button>تصدير المقارنة PDF</button></form>
<a class="btn sec" href="/export-center/?model_ids={{ ids|join(',') }}">كتالوج بهذه الطرازات</a></div>{% endif %}{% endif %}{% endblock %}"""

VIDEOS = """{% extends "base" %}{% block head %}<style>.vg{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}.vg .c{background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden}
.vg .p{aspect-ratio:16/9;background:#111;display:grid;place-items:center}.vg .p img{width:100%;height:100%;object-fit:cover}.vg .b{padding:10px 12px}</style>{% endblock %}
{% block body %}<div class="card"><h2 style="margin:0 0 6px">مكتبة الفيديوهات ({{ n_all }})</h2>
<p class="muted" style="margin:0 0 10px">كل فيديوهات المعدات للتسويق. الرفع من تبويب «الفيديو» في صفحة المنتج.</p>
<form method="get" style="display:flex;gap:8px;flex-wrap:wrap"><input name="q" value="{{ q }}" placeholder="بحث بالعنوان أو المنتج أو الطراز"><select name="status"><option value="">كل الحالات</option>{% for k, n in vstatus %}<option value="{{ k }}" {{ 'selected' if st==k }}>{{ n }}</option>{% endfor %}</select><button>تصفية</button></form></div>
<div class="vg">{% for v in rows %}<a class="c" href="/product/{{ v.product_id }}?tab=video#v{{ v.id }}" style="text-decoration:none;color:inherit"><div class="p">{% if v.kind=='file' %}<img src="/video/{{ v.id }}/thumb" alt="" onerror="this.remove()">{% else %}<span style="color:#fff">رابط</span>{% endif %}</div>
<div class="b"><b>{{ v.title }}</b><div class="muted" style="font-size:13px">{{ v.pname }}{% if v.mcode %} · <span class="ltr">{{ v.mcode }}</span>{% endif %}</div>
<div style="margin-top:6px"><span class="tag {{ 'g' if v.status=='published' else ('y' if v.status=='ready' else '') }}">{{ 'نُشر' if v.status=='published' else ('جاهز للنشر' if v.status=='ready' else 'مسودة') }}</span>
{% for k in (v.platforms or '').split(',') if k %}<span class="tag">{{ platforms.get(k, k) }}</span> {% endfor %}</div></div></a>
{% else %}<div class="card" style="grid-column:1/-1"><p class="muted" style="margin:0">لا فيديوهات بعد. افتح منتجًا ثم تبويب «الفيديو».</p></div>{% endfor %}</div>{% endblock %}"""
