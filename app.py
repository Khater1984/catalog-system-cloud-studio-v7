import os, io, re, threading, datetime, sys
from flask import Flask, request, redirect, url_for, send_file, render_template, flash, abort
from werkzeug.middleware.proxy_fix import ProxyFix
from jinja2 import DictLoader
from openpyxl import Workbook
import db, extractor, storage, auth, catalogs, admin, gauth
from templates import TEMPLATES
import templates_cloud
import pdfgen, gsync
import products as prod
import exportui, profiles, dictionary, product_studio, export_center, enterprise_pro, ui, product_hub

TEMPLATES.update(templates_cloud.TEMPLATES)
app = Flask(__name__)
_key = os.environ.get("SECRET_KEY", "")
if not _key:
    if db.IS_PG or auth.PROD:
        raise RuntimeError("SECRET_KEY must be set in the environment (Cloud Run: from Secret Manager)")
    _key = "local-dev-only"
app.secret_key = _key
app.jinja_loader = DictLoader(TEMPLATES)
app.jinja_env.autoescape = True                     # template names have no .html suffix, so Flask would otherwise NOT escape anything
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)     # Cloud Run terminates HTTPS in front of the app
db.init()
auth.init_app(app)


@app.errorhandler(413)
def too_large(_e):
    flash(f"يتجاوز حجم الملف الحد المسموح ({app.config['MAX_CONTENT_LENGTH'] // (1024 * 1024)} ميجابايت). يبلغ الحد العملي للرفع المباشر على Cloud Run مقدار 32 ميجابايت؛ يُرجى ضغط ملف PDF أو تقسيمه. راجع README_CLOUD_AR.md.")
    return redirect("/")
auth.bootstrap()
app.register_blueprint(catalogs.bp)
app.register_blueprint(admin.bp)
app.register_blueprint(prod.bp)
app.register_blueprint(exportui.bp)
app.register_blueprint(dictionary.bp)
app.register_blueprint(product_studio.bp)
app.register_blueprint(export_center.bp)
app.register_blueprint(enterprise_pro.bp)
app.register_blueprint(product_hub.bp)
ui.init_app(app)
_c = db.conn(); profiles.ensure_default(_c); _c.commit(); _c.close()

FREQ_ATTRS = {"power supply", "frequency", "voltage", "التردد", "مصدر الطاقة"}
FREQ_CANON = {"power supply", "frequency"}
SPEED_HINT = re.compile(r"speed|rate|rpm|سرعة", re.I)


def q(sql, *a):
    c = db.conn(); rows = c.execute(sql, a).fetchall(); c.close(); return rows


def ex(sql, *a):
    c = db.conn(); cur = c.execute(sql, a); c.commit(); lid = cur.lastrowid; c.close(); return lid


@app.context_processor
def ctx():
    return dict(unresolved=q("SELECT COUNT(*) n FROM review WHERE resolved=0")[0]["n"])


@app.route("/catalogs")
def index():
    cats = catalogs.annotate(q("""SELECT c.*, (SELECT COUNT(*) FROM models m WHERE m.catalog_id=c.id) models,
                (SELECT COUNT(*) FROM models m WHERE m.catalog_id=c.id AND m.status='approved') approved,
                (SELECT COUNT(*) FROM review r WHERE r.catalog_id=c.id AND r.resolved=0) reviews FROM catalogs c WHERE c.file_sha<>'manual' ORDER BY c.id DESC"""))
    return render_template("index", cats=cats, busy=any(c["state"] in ("processing", "checking") for c in cats))


@app.route("/img/<name>")
def img(name):
    if not re.fullmatch(r"[0-9a-f]{20}\.(jpg|png)", name):
        abort(404)
    try:
        p = storage.image_path(name)
    except Exception:
        abort(404)
    if not os.path.exists(p):
        abort(404)
    r = send_file(p)
    r.headers["Cache-Control"] = "private, max-age=604800, immutable"      # file name = content hash
    return r


# ---------------- models list / bulk ----------------
@app.route("/models")
def models():
    fam, st, cat, text = request.args.get("family", ""), request.args.get("status", ""), request.args.get("catalog", ""), request.args.get("q", "")
    sql = """SELECT m.*, c.supplier, f.name fam, p.name pname,
             (SELECT COUNT(*) FROM specs s WHERE s.model_id=m.id) nspec,
             (SELECT COUNT(*) FROM image_links l WHERE l.model_id=m.id) nimg
             FROM models m LEFT JOIN catalogs c ON c.id=m.catalog_id LEFT JOIN families f ON f.id=m.family_id
             LEFT JOIN products p ON p.id=m.product_id WHERE 1=1"""
    act, pr = request.args.get("active", ""), request.args.get("product", "")
    sql += "" if act == "all" else (" AND m.active=0" if act == "off" else " AND m.active=1")
    a = []
    if pr == "none": sql += " AND m.product_id IS NULL"
    elif pr: sql += " AND m.product_id=?"; a.append(pr)
    if fam == "none": sql += " AND m.family_id IS NULL"
    elif fam: sql += " AND m.family_id=?"; a.append(fam)
    if st: sql += " AND m.status=?"; a.append(st)
    if cat: sql += " AND m.catalog_id=?"; a.append(cat)
    if text: sql += " AND (m.code LIKE ? OR m.series LIKE ?)"; a += [f"%{text}%", f"%{text}%"]
    rows = q(sql + " ORDER BY m.catalog_id, m.page, m.id", *a)
    return render_template("models", rows=rows, fams=q("SELECT * FROM families ORDER BY name"),
                           prods=q("SELECT id,name FROM products WHERE active=1 ORDER BY name"),
                           cats=q("SELECT * FROM catalogs"), args=request.args)


def approve_models(c, ids, with_images):
    db.touch(c, "models", ids)
    for mid in ids:
        c.execute("UPDATE models SET status='approved' WHERE id=?", (mid,))
        db.log(c, "model", int(mid), "approve", "with images (high confidence, or the model's only medium-confidence image)" if with_images else "")
        if with_images:
            c.execute("""UPDATE image_links SET ok_catalog=1, ok_sheet=1 WHERE model_id=? AND (confidence='high' OR (confidence='medium' AND
                         (SELECT COUNT(*) FROM image_links x WHERE x.model_id=image_links.model_id AND x.confidence IN ('high','medium'))=1))""", (mid,))
            has = c.execute("SELECT 1 FROM image_links WHERE model_id=? AND is_primary=1", (mid,)).fetchone()
            if not has:
                r = c.execute("SELECT id FROM image_links WHERE model_id=? AND ok_sheet=1 ORDER BY id LIMIT 1", (mid,)).fetchone()
                if r: c.execute("UPDATE image_links SET is_primary=1 WHERE id=?", (r["id"],))


@app.route("/catalog/<int:cid>/approve-all", methods=["POST"])
def catalog_approve_all(cid):
    """One click for the common case: approve every not-yet-approved model of this catalog that has specifications (with its images)."""
    c = db.conn()
    ids = [r["id"] for r in c.execute("""SELECT m.id FROM models m WHERE m.catalog_id=? AND m.active=1 AND m.status<>'approved'
                                         AND EXISTS (SELECT 1 FROM specs s WHERE s.model_id=m.id)""", (cid,)).fetchall()]
    if ids:
        approve_models(c, ids, True); c.commit()
    c.close()
    flash(f"اعتُمد {len(ids)} طراز مع صوره. الطرازات بلا مواصفات تبقى للمراجعة." if ids else "لا طرازات جاهزة للاعتماد في هذا الكتالوج.")
    return redirect(f"/catalog/{cid}")


@app.route("/models/bulk", methods=["POST"])
def bulk():
    ids = request.form.getlist("ids"); act = request.form.get("action")
    if not ids:
        flash("يرجى اختيار الطرازات أولًا."); return redirect(auth.back("/models"))
    c = db.conn()
    db.touch(c, "models", [int(x) for x in ids if str(x).isdigit()])
    if act == "family":
        fid = request.form.get("family_id") or None
        names = {str(r["id"]): r["name"] for r in c.execute("SELECT id,name FROM families").fetchall()}
        skipped = 0
        for i in ids:
            old = c.execute("SELECT family_id, product_id FROM models WHERE id=?", (i,)).fetchone()
            if old and old["product_id"]:
                skipped += 1; continue                      # its family comes from its product (keeps product/family consistent)
            c.execute("UPDATE models SET family_id=? WHERE id=?", (fid, i))
            db.log(c, "model", int(i), "family", f"{names.get(str(old['family_id']), '-')} -> {names.get(str(fid), '-')}")
        if skipped:
            flash(f"تعذّر تغيير تصنيف {skipped} طرازًا تابعًا لمنتج؛ يُدار تصنيف هذه الطرازات من صفحة المنتج.")
    elif act == "approve": approve_models(c, ids, bool(request.form.get("with_images")))
    elif act == "unapprove":
        for i in ids:
            c.execute("UPDATE models SET status='staging', public_enabled=0 WHERE id=?", (i,)); db.log(c, "model", int(i), "unapprove", "bulk")
    elif act in ("hide", "show"):
        for i in ids:
            c.execute("UPDATE models SET visible=?, public_enabled=CASE WHEN ?='hide' THEN 0 ELSE public_enabled END WHERE id=?",
                      (0 if act == "hide" else 1, act, i)); db.log(c, "model", int(i), act, "bulk")
    elif act == "product": prod.assign(c, [int(i) for i in ids], request.form.get("product_id") or None)
    elif act in ("disable", "enable"):
        for i in ids:
            c.execute("UPDATE models SET active=?, public_enabled=CASE WHEN ?='disable' THEN 0 ELSE public_enabled END WHERE id=?",
                      (0 if act == "disable" else 1, act, i)); db.log(c, "model", int(i), act, "bulk")
    c.commit(); c.close()
    return redirect(auth.back("/models"))


# ---------------- families / tree ----------------
def family_tree():
    fams = q("SELECT * FROM families ORDER BY name")
    prods = q("SELECT * FROM products WHERE active=1 ORDER BY name")
    ms = q("SELECT id,code,series,status,visible,family_id,product_id,active FROM models ORDER BY code")
    by_prod, loose = {}, {}
    for m in ms:
        if m["product_id"]: by_prod.setdefault(m["product_id"], []).append(m)
        else: loose.setdefault(m["family_id"], []).append(m)
    def build(pid):
        out = []
        for f in fams:
            if f["parent_id"] == pid:
                out.append(dict(f=f, models=loose.get(f["id"], []), kids=build(f["id"]),
                                prods=[dict(p=p, models=by_prod.get(p["id"], [])) for p in prods if p["family_id"] == f["id"]]))
        return out
    free_prods = [dict(p=p, models=by_prod.get(p["id"], [])) for p in prods if not p["family_id"]]
    return build(None), loose.get(None, []), free_prods


@app.route("/tree")
def tree():
    t, free, fp = family_tree()
    return render_template("tree", tree=t, free=free, free_prods=fp, fams=q("SELECT * FROM families ORDER BY name"))


@app.route("/family/add", methods=["POST"])
def family_add():
    name = request.form.get("name", "").strip()
    if name:
        c = db.conn()
        fid = c.execute("INSERT INTO families(name,parent_id) VALUES(?,?)", (name, request.form.get("parent_id") or None)).lastrowid
        db.log(c, "family", fid, "create", name); c.commit(); c.close()
    return redirect("/tree")


@app.route("/family/<int:fid>/rename", methods=["POST"])
def family_rename(fid):
    new = request.form.get("name", "").strip()
    c = db.conn()
    old = c.execute("SELECT name FROM families WHERE id=?", (fid,)).fetchone()
    if old and new:
        c.execute("UPDATE families SET name=? WHERE id=?", (new, fid)); db.log(c, "family", fid, "rename", f"'{old['name']}' -> '{new}'"); c.commit()
    c.close()
    return redirect("/tree")


@app.route("/family/<int:fid>/delete", methods=["POST"])
def family_delete(fid):
    if q("SELECT 1 FROM models WHERE family_id=?", fid) or q("SELECT 1 FROM families WHERE parent_id=?", fid):
        flash("تضم المجموعة طرازات أو مجموعات فرعية؛ يُرجى نقلها أولًا.")
    else:
        c = db.conn()
        old = c.execute("SELECT name FROM families WHERE id=?", (fid,)).fetchone()
        c.execute("DELETE FROM families WHERE id=?", (fid,)); db.log(c, "family", fid, "delete", old["name"] if old else ""); c.commit(); c.close()
    return redirect("/tree")


@app.route("/families/suggest", methods=["POST"])
def families_suggest():
    c = db.conn()
    for r in c.execute("SELECT DISTINCT suggested_group g FROM models WHERE family_id IS NULL AND suggested_group<>''").fetchall():
        f = c.execute("SELECT id FROM families WHERE name=?", (r["g"],)).fetchone()
        fid = f["id"] if f else c.execute("INSERT INTO families(name,parent_id) VALUES(?,NULL)", (r["g"],)).lastrowid
        n = c.execute("UPDATE models SET family_id=? WHERE family_id IS NULL AND suggested_group=?", (fid, r["g"])).rowcount
        db.log(c, "family", fid, "suggest_assign", f"{n} models by code prefix '{r['g']}'")
    c.commit(); c.close()
    flash("أُنشئت تصنيفات أولية استنادًا إلى بادئات رموز الطرازات. يمكنك تعديل الأسماء من صفحة الشجرة.")
    return redirect("/tree")


# ---------------- single model ----------------
def model_ctx(mid):
    m = q("""SELECT m.*, c.supplier, c.name cname FROM models m LEFT JOIN catalogs c ON c.id=m.catalog_id WHERE m.id=?""", mid)
    if not m: abort(404)
    links = q("""SELECT l.*, i.file, i.w, i.h, i.page, i.dpi FROM image_links l JOIN images i ON i.id=l.image_id
                 WHERE l.model_id=? ORDER BY l.is_primary DESC, l.id""", mid)
    free = q("""SELECT i.* FROM images i WHERE i.page=? AND i.catalog_id=? AND i.id NOT IN
                (SELECT image_id FROM image_links WHERE model_id=?)""", m[0]["page"], m[0]["catalog_id"], mid)
    sib = q("SELECT id, code FROM models WHERE catalog_id=? AND active=1 ORDER BY page, id", m[0]["catalog_id"])
    pos = next((i for i, r in enumerate(sib) if r["id"] == mid), None)
    prev_m = sib[pos - 1] if pos else None
    next_m = sib[pos + 1] if pos is not None and pos + 1 < len(sib) else None
    return dict(m=m[0], prev_m=prev_m, next_m=next_m, specs=q("SELECT * FROM specs WHERE model_id=? ORDER BY sort,id", mid),
                links=links, free=free, fams=q("SELECT * FROM families ORDER BY name"),
                prods=q("SELECT id,name FROM products WHERE active=1 ORDER BY name"),
                hist=q("SELECT * FROM audit WHERE entity='model' AND entity_id=? ORDER BY id DESC LIMIT 15", mid),
                spec_hist=q("SELECT * FROM spec_changes WHERE model_id=? ORDER BY id DESC LIMIT 40", mid),
                sheet_w_cm=pdfgen.SHEET_IMG_W_CM)


@app.route("/model/<int:mid>")
def model(mid):
    return render_template("model", **model_ctx(mid))


@app.route("/model/<int:mid>/save", methods=["POST"])
def model_save(mid):
    f = request.form; c = db.conn()
    try:
        old = c.execute("SELECT * FROM models WHERE id=?", (mid,)).fetchone()
        sent = f.get("version")
        if sent is not None and str(old["version"] or 1) != sent:          # someone else saved this model after the form was opened
            last = c.execute("SELECT username, ts FROM audit WHERE entity='model' AND entity_id=? ORDER BY id DESC LIMIT 1", (mid,)).fetchone()
            flash(f"أجرى {last['username'] if last else 'مستخدم آخر'} تعديلًا على هذا الطراز ({last['ts'] if last else ''} UTC) بعد فتح الصفحة. "
                  "لم يُحفظ تعديلك لتجنّب استبدال التغييرات الأخرى. راجع أحدث التعديلات ثم أعد تنفيذ تعديلك.")
            return redirect(f"/model/{mid}")
        c.execute("UPDATE models SET ar_name=?, visible=? WHERE id=?", (f.get("ar_name", "").strip(), 1 if f.get("visible") else 0, mid))
        newp = f.get("product_id") or None
        if str(newp or "") != str(old["product_id"] or ""):
            prod.assign(c, [mid], newp)
            c.execute("UPDATE models SET public_enabled=0 WHERE id=?", (mid,))
        if not newp:
            fam = f.get("family_id") or None
            if str(fam or "") != str(old["family_id"] or ""):
                c.execute("UPDATE models SET family_id=? WHERE id=?", (fam, mid)); db.log(c, "model", mid, "family", str(fam))
        if f.get("ar_name", "").strip() != (old["ar_name"] or ""): db.log(c, "model", mid, "edit", f"arabic name: '{old['ar_name'] or ''}' -> '{f.get('ar_name', '').strip()}'")
        if bool(f.get("visible")) != bool(old["visible"]): db.log(c, "model", mid, "show" if f.get("visible") else "hide", "")
        if not f.get("visible"):
            c.execute("UPDATE models SET public_enabled=0 WHERE id=?", (mid,))
        for s_ in c.execute("SELECT * FROM specs WHERE model_id=?", (mid,)).fetchall():
            new = f.get(f"cv_{s_['id']}", "").strip()
            if new != (s_["company_value"] or ""):
                c.execute("UPDATE specs SET company_value=?, rule=? WHERE id=?", (new, "MANUAL" if new else "", s_["id"]))
                db.spec_change(c, "UPDATE" if new else "RESET", s_, new, model=old)
                db.log(c, "model", mid, "spec", f"{s_['attr']}: '{s_['company_value'] or s_['supplier_value']}' -> '{new or s_['supplier_value']}'")
            hid = 1 if f.get(f"hid_{s_['id']}") else 0
            if hid != s_["hidden"]:
                c.execute("UPDATE specs SET hidden=? WHERE id=?", (hid, s_["id"]))
                db.spec_change(c, "DELETE" if hid else "RESTORE", s_, "", model=old)
                db.log(c, "model", mid, "spec_hidden" if hid else "spec_shown", s_["attr"])
        for k in range(5):
            na, nv = f.get(f"new_attr_{k}", "").strip(), f.get(f"new_val_{k}", "").strip()
            if na and nv:
                n = c.execute("SELECT COALESCE(MAX(sort),0)+1 n FROM specs WHERE model_id=?", (mid,)).fetchone()["n"]
                sid = c.execute("INSERT INTO specs(model_id,attr,supplier_value,company_value,rule,sort) VALUES(?,?,?,?,?,?)", (mid, na, "", nv, "MANUAL", n)).lastrowid
                db.spec_change(c, "ADD", dict(id=sid, model_id=mid, attr=na, supplier_value="", company_value=""), nv, old_value="", model=old)
                db.log(c, "model", mid, "spec_added", f"{na} = {nv}")
        prim = f.get("primary")
        for l in c.execute("SELECT * FROM image_links WHERE model_id=?", (mid,)).fetchall():
            c.execute("UPDATE image_links SET ok_catalog=?, ok_sheet=?, is_primary=? WHERE id=?",
                      (1 if f.get(f"okc_{l['id']}") else 0, 1 if f.get(f"oks_{l['id']}") else 0, 1 if prim == str(l["id"]) else 0, l["id"]))
        db.touch(c, "models", [mid])
        act = f.get("action")
        if act == "approve": approve_models(c, [mid], bool(f.get("with_images")))
        if act == "unapprove":
            c.execute("UPDATE models SET status='staging', public_enabled=0 WHERE id=?", (mid,)); db.log(c, "model", mid, "unapprove", "")
        if act in ("disable", "enable"):
            c.execute("UPDATE models SET active=?, public_enabled=CASE WHEN ?='disable' THEN 0 ELSE public_enabled END WHERE id=?",
                      (0 if act == "disable" else 1, act, mid))
            db.log(c, "model", mid, act, f.get("reason", "").strip())
        c.commit()
    finally:
        c.close()
    return redirect(f"/model/{mid}")


@app.route("/model/<int:mid>/link", methods=["POST"])
def model_link(mid):
    try:
        image_id = int(request.form.get("image_id", ""))
    except (TypeError, ValueError):
        flash("تعذر ربط الصورة: معرّف الصورة غير صالح.")
        return redirect(f"/model/{mid}")

    c = db.conn()
    try:
        model = c.execute("SELECT id FROM models WHERE id=?", (mid,)).fetchone()
        image = c.execute("SELECT id FROM images WHERE id=? AND disabled=0", (image_id,)).fetchone()
        if not model or not image:
            flash("تعذّر ربط الصورة: الطراز أو الصورة غير موجود، أو أن الصورة معطّلة.")
            return redirect(f"/model/{mid}")
        try:
            c.execute("INSERT INTO image_links(image_id,model_id,confidence) VALUES(?,?,'manual') ON CONFLICT DO NOTHING", (image_id, mid))
            db.log(c, "model", mid, "image_link", f"image {image_id}")
            c.commit()
        except Exception as exc:
            c.rollback()
            is_fk = (getattr(exc, "pgcode", None) == "23503" or
                     getattr(exc, "sqlite_errorname", "") == "SQLITE_CONSTRAINT_FOREIGNKEY" or
                     "FOREIGN KEY constraint failed" in str(exc))
            if not is_fk:
                raise
            flash("تعذّر ربط الصورة لأنها حُذفت أثناء تنفيذ العملية. حدّث الصفحة ثم أعد المحاولة.")
    finally:
        c.close()
    return redirect(f"/model/{mid}")


@app.route("/model/<int:mid>/unlink/<int:lid>", methods=["POST"])
def model_unlink(mid, lid):
    c = db.conn()
    r = c.execute("SELECT image_id FROM image_links WHERE id=? AND model_id=?", (lid, mid)).fetchone()
    c.execute("DELETE FROM image_links WHERE id=? AND model_id=?", (lid, mid))
    if r: db.log(c, "model", mid, "image_unlink", f"image {r['image_id']}")
    c.commit(); c.close()
    return redirect(f"/model/{mid}")


# ---------------- review ----------------
@app.route("/review")
def review():
    show, cat = request.args.get("all"), request.args.get("catalog", "")
    where, a = [], []
    if not show: where.append("r.resolved=0")
    if cat.isdigit(): where.append("r.catalog_id=?"); a.append(int(cat))
    rows = q("SELECT r.*, c.name cname FROM review r LEFT JOIN catalogs c ON c.id=r.catalog_id " +
             ("WHERE " + " AND ".join(where) + " " if where else "") + "ORDER BY r.kind, r.id", *a)
    cat_row = q("SELECT id,name FROM catalogs WHERE id=?", int(cat)) if cat.isdigit() else []
    return render_template("review", rows=rows, show=show, cat=cat_row[0] if cat_row else None)


@app.route("/review/<int:rid>/resolve", methods=["POST"])
def review_resolve(rid):
    c = db.conn()
    r = c.execute("SELECT kind, ref FROM review WHERE id=?", (rid,)).fetchone()
    c.execute("UPDATE review SET resolved=1 WHERE id=?", (rid,))
    if r: db.log(c, "catalog" if (r["ref"] or "").startswith("catalog") else "review", rid, "review_resolved", f"{r['kind']} {r['ref']}")
    c.commit(); c.close()
    return redirect(auth.back("/review"))


# ---------------- company rules (frequency) ----------------
def _freq_attr(s, amap, builtin_raw):
    k = dictionary.key(s["attr"])
    canon = ((amap.get(k) or {}).get("canonical") or "").strip().lower()
    return k in FREQ_ATTRS or k in builtin_raw or canon in FREQ_CANON


def apply_freq(hz):
    c = db.conn(); n = 0; models_ = set()
    amap = dictionary.load_map(c)
    builtin_raw = {r for canon, (_ar, _o, raws) in dictionary.BUILTIN.items() if canon in ("Power supply", "Frequency") for r in raws}
    for s in c.execute("SELECT * FROM specs WHERE rule IN ('','FREQ')").fetchall():
        if not _freq_attr(s, amap, builtin_raw): continue
        v = s["supplier_value"] or ""; nv = v
        nv = re.sub(r"50\s*/\s*60\s*Hz(\s*optional)?", f"{hz} Hz", nv, flags=re.I)
        nv = re.sub(r"\b50\s*Hz\b", f"{hz} Hz", nv, flags=re.I)
        if nv != v:
            db.spec_change(c, "UPDATE", s, nv, old_value=s["company_value"] or "")
            c.execute("UPDATE specs SET company_value=?, rule='FREQ' WHERE id=?", (nv, s["id"])); n += 1; models_.add(s["model_id"])
    if n and not c.execute("SELECT 1 FROM review WHERE kind='RULE_SIDE_EFFECT' AND resolved=0").fetchone():
        c.execute("INSERT INTO review(catalog_id,kind,ref,note) VALUES(NULL,'RULE_SIDE_EFFECT','قاعدة التردد',?)",
                  ("يغيّر هذا الإجراء التردد في الوصف فقط. تظل سرعة المحرك ومعدلات القطع والثني على قيم المورد عند 50 هرتز، وتتغير فعليًا بتغير التردد. "
                   "يرجى تأكيد توافر إصدار 60 هرتز وقيم مواصفاته الفعلية من المورد قبل إعداد أي مستند للعميل.",))
    if models_: db.touch(c, "models", models_)
    db.set_setting(c, "freq_hz", str(hz))
    db.log(c, "rule", 0, "freq_apply", f"{hz} Hz: {n} specs in {len(models_)} models")
    c.commit(); c.close(); return n, len(models_)


@app.route("/rules", methods=["GET", "POST"])
def rules():
    if request.method == "POST":
        if request.form.get("do") == "clear":
            c = db.conn()
            for s_ in c.execute("SELECT * FROM specs WHERE rule='FREQ'").fetchall():
                db.spec_change(c, "RESET", s_, "", old_value=s_["company_value"] or "")
            n = c.execute("UPDATE specs SET company_value='', rule='' WHERE rule='FREQ'").rowcount
            db.log(c, "rule", 0, "freq_clear", f"{n} specs back to supplier values"); c.commit(); c.close()
            flash("أُزيلت قاعدة التردد واستُعيدت قيم المورد.")
        else:
            try:
                hz = int(request.form.get("hz", "60"))
                if not 40 <= hz <= 70: raise ValueError
            except ValueError:
                flash("يجب أن يكون التردد رقمًا بين 40 و70."); return redirect("/rules")
            n, m = apply_freq(hz)
            flash(f"طُبّقت القاعدة على {n} مواصفة في {m} طرازًا. لم تتغير قيم المورد.")
        return redirect("/rules")
    c = db.conn()
    aff = c.execute("SELECT COUNT(*) n, COUNT(DISTINCT model_id) m FROM specs WHERE rule='FREQ'").fetchone()
    speed = c.execute("SELECT COUNT(DISTINCT model_id) n FROM specs WHERE attr LIKE '%speed%' OR attr LIKE '%rate%'").fetchone()
    hz = db.get_setting(c, "freq_hz", "60"); c.close()
    return render_template("rules", aff=aff, hz=hz, speed=speed)


# ---------------- exports ----------------
@app.route("/export.xlsx")
def export_xlsx():
    wb = Workbook(); ws = wb.active; ws.title = "Models"
    ws.append(["ID", "Model", "Series", "Product", "Family", "Supplier", "Arabic name", "Status", "Visible", "Active", "Page"])
    for m in q("""SELECT m.*, c.supplier, f.name fam, p.name pname FROM models m LEFT JOIN catalogs c ON c.id=m.catalog_id
                  LEFT JOIN families f ON f.id=m.family_id LEFT JOIN products p ON p.id=m.product_id ORDER BY m.id"""):
        ws.append([m["id"], m["code"], m["series"], m["pname"], m["fam"], m["supplier"], m["ar_name"], m["status"], m["visible"], m["active"], m["page"]])
    w2 = wb.create_sheet("Specifications")
    w2.append(["Model ID", "Model", "Attribute", "Attribute (as written by supplier)", "Supplier value", "Company value", "Final value", "Rule", "Hidden", "Source page", "Found on source page"])
    c = db.conn(); amap = dictionary.load_map(c)
    for s_ in c.execute("SELECT s.*, m.code FROM specs s JOIN models m ON m.id=s.model_id ORDER BY s.model_id, s.sort").fetchall():
        k = dictionary.key(s_["attr"]); canon = (amap.get(k) or {}).get("canonical") or s_["attr"]
        w2.append([s_["model_id"], s_["code"], canon, s_["attr"], s_["supplier_value"], s_["company_value"], s_["company_value"] or s_["supplier_value"],
                   s_["rule"], s_["hidden"], s_["source_page"], "" if s_["verified"] is None else ("yes" if s_["verified"] else "NO")])
    c.close()
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return send_file(buf, download_name="catalog_export.xlsx", as_attachment=True)


@app.route("/sync", methods=["GET", "POST"])
def sync_page():
    res = None
    if request.method == "POST":
        try:
            res = gsync.sync(); flash("اكتملت المزامنة بنجاح.")
        except Exception as e:
            flash(f"فشلت المزامنة: {e}")
    c = db.conn()
    info = dict(sheet=db.get_setting(c, "sheet_id"), folder=db.get_setting(c, "drive_folder"), last=db.get_setting(c, "last_sync"),
                has_cred=gauth.configured(), drive_mode=storage.IS_DRIVE, n=c.execute("SELECT COUNT(*) n FROM models WHERE status='approved'").fetchone()["n"])
    c.close()
    return render_template("sync", info=info, res=res)


if __name__ == "__main__":      # local development only; production runs under gunicorn (see Dockerfile)
    port = int(os.environ.get("PORT", "5050"))
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=port, debug=False)
