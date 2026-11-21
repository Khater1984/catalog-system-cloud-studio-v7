"""Search + export (data sheet / catalog) and the export settings panel (visual identity profiles)."""
import os, re, unicodedata, json, uuid
from flask import Blueprint, request, redirect, render_template, flash, send_file, abort, Response
import io
from PIL import Image as PILImage
import db, profiles, pdfgen, pdf_engine, export_center, dictionary, assets, auth, storage

bp = Blueprint("exportui", __name__)


def q(sql, *a):
    c = db.conn(); r = c.execute(sql, a).fetchall(); c.close(); return r


def norm(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    for a, b in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ى", "ي"), ("ة", "ه"), ("ؤ", "و"), ("ئ", "ي")):
        s = s.replace(a, b)
    return s


def num(v):
    m = re.search(r"-?\d+(?:\.\d+)?", v or "")
    return float(m.group(0)) if m else None


def model_data(mid):
    c = db.conn()
    try:
        m = c.execute("SELECT m.*, c.supplier FROM models m LEFT JOIN catalogs c ON c.id=m.catalog_id WHERE m.id=?", (mid,)).fetchone()
        if not m: return None
        amap = dictionary.load_map(c)
        specs = dictionary.apply(c.execute("SELECT * FROM specs WHERE model_id=? ORDER BY sort,id", (mid,)).fetchall(), amap)
        links = c.execute("""SELECT l.*, i.file, i.w, i.h, i.disabled FROM image_links l JOIN images i ON i.id=l.image_id
                             WHERE l.model_id=? ORDER BY l.is_primary DESC, l.id""", (mid,)).fetchall()
        prod = None
        product_images = []
        if m["product_id"]:
            r = c.execute("SELECT * FROM products WHERE id=?", (m["product_id"],)).fetchone()
            prod = dict(r) if r else None
            if prod:
                product_images = c.execute("""SELECT pl.image_role, pl.is_primary, pl.ok_sheet, pl.ok_catalog,
                                                    i.file, i.disabled
                                             FROM product_image_links pl JOIN images i ON i.id=pl.image_id
                                             WHERE pl.product_id=? ORDER BY pl.is_primary DESC, pl.sort_order, pl.id""",
                                           (prod["id"],)).fetchall()
        fam = ""
        family_id = m["family_id"] or (prod or {}).get("family_id")
        if family_id:
            names, cur, guard = [], family_id, 0
            while cur and guard < 10:
                r = c.execute("SELECT name, parent_id FROM families WHERE id=?", (cur,)).fetchone()
                if not r: break
                names.append(r["name"]); cur = r["parent_id"]; guard += 1
            fam = " / ".join(reversed(names))
        return dict(m=m, specs=specs, links=links, product_images=product_images, product=prod, family=fam)
    finally:
        c.close()


def _profile(pid=None):
    c = db.conn(); p = profiles.load(c, pid or request.values.get("profile") or None); c.close(); return p


def _pdf(buf, name, what="", n=0, *, doc_type=None, profile=None, title="", options=None):
    pdf_data = buf.getvalue() if doc_type else None
    stored_name = None
    drive_id = None
    if doc_type:
        stem = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.splitext(os.path.basename(name))[0])[:120] or "export"
        stored_name = f"{stem}_{uuid.uuid4().hex[:10]}.pdf"
        page_count = pdf_engine.page_count(pdf_data)
        c = db.conn()
        try:
            drive_id = storage.save_export_pdf(stored_name, pdf_data)
            profile_id = (profile.get("id") if profile else None) or None
            c.execute("""INSERT INTO export_versions(doc_type,template_name,profile_id,title,file_name,options_json,page_count,drive_id)
                         VALUES(?,?,?,?,?,?,?,?)""",
                      (doc_type, "Corporate", profile_id, title or name, stored_name,
                       json.dumps(options or {}, ensure_ascii=False), page_count, drive_id))
            if what:
                db.log(c, "export", 0, what, f"{name}: {n} models")
            c.commit()
        except Exception:
            c.rollback()
            if drive_id:
                storage.delete(drive_id)
            elif not storage.IS_DRIVE:
                try:
                    os.remove(storage.export_path(stored_name))
                except OSError:
                    pass
            raise
        finally:
            c.close()
    elif what:
        c = db.conn()
        try:
            db.log(c, "export", 0, what, f"{name}: {n} models"); c.commit()
        finally:
            c.close()
    return send_file(io.BytesIO(pdf_data) if pdf_data is not None else buf,
                     mimetype="application/pdf", download_name=name)


def _engine_items(items, doc_type="product_catalog"):
    """Adapt the legacy search result shape to the shared PDF engine's input."""
    out = []
    approval_field = "ok_sheet" if doc_type == "tds" else "ok_catalog"
    for item in items:
        product = item.get("product") or {}
        images = []
        # Rows from the database are not dicts (no .get): convert first, then filter.
        def _usable(rows):
            dicts = [dict(r) for r in (rows or [])]
            return [l for l in dicts if not l.get("disabled") and l.get("file")
                    and int(l.get(approval_field) or 0) == 1]
        selected_links = _usable(item.get("links") or item.get("images")) or _usable(item.get("product_images"))
        for link in selected_links:
            images.append({
                "file": link["file"],
                "image_role": link.get("image_role") or "model_image",
                "is_primary": link.get("is_primary", 0),
                "ok_sheet": link.get("ok_sheet", 0),
                "ok_catalog": link.get("ok_catalog", 0),
            })
        out.append({
            "m": dict(item["m"]),
            "product": dict(product),
            "specs": [dict(s) for s in item.get("specs", [])],
            "images": images,
            "family_name": item.get("family_name") or item.get("family") or "General",
        })
    return out


def build_catalog(ids, title):
    items, skipped = [], []
    for i in ids:
        d = model_data(i)
        if not d: continue
        m = d["m"]
        why = "غير معتمد" if m["status"] != "approved" else "معطّل" if not m["active"] else "مخفي من الكتالوج" if not m["visible"] else ""
        (skipped.append((m["code"], why)) if why else items.append(d))
    items.sort(key=lambda d: ((d["product"] or {}).get("name") or "~", d["m"]["code"]))
    return items, skipped


@bp.route("/datasheet/<int:mid>.pdf")
def datasheet(mid):
    d = model_data(mid)
    if not d: abort(404)
    items = export_center.collect_items("tds", model_id=mid)
    if not items: abort(404)
    profile = _profile()
    options = {"title": d["m"]["code"], "model_id": str(mid), "cover": "0", "toc": "0"}
    buf = pdf_engine.build_document(items, profile, doc_type="tds", options=options)
    return _pdf(buf, f"{d['m']['code']}_datasheet.pdf", "datasheet", 1,
                doc_type="tds", profile=profile, title=options["title"], options=options)


@bp.route("/export/catalog", methods=["POST"])
def export_catalog():
    mode = request.form.get("mode", "selected")
    raw = request.form.get("all_ids", "") if mode == "all" else ",".join(request.form.getlist("ids"))
    ids = [int(x) for x in raw.split(",") if x.strip().isdigit()]
    if not ids:
        flash("يرجى اختيار الطرازات أولًا."); return redirect("/search")
    items, skipped = build_catalog(ids, "")
    if not items:
        flash("لا توجد طرازات صالحة للكتالوج؛ يجب أن تكون معتمدة ونشطة وظاهرة."); return redirect("/search")
    title = request.form.get("title", "").strip()
    profile = _profile()
    options = {"title": title, "model_ids": ",".join(str(x) for x in ids)}
    buf = pdf_engine.build_document(_engine_items(items, "product_catalog"), profile, doc_type="product_catalog", options=options)
    return _pdf(buf, "catalog.pdf", "catalog", len(items),
                doc_type="product_catalog", profile=profile, title=title, options=options)


@bp.route("/catalog.pdf")
def catalog_pdf():
    fid, pid = request.args.get("family"), request.args.get("product")
    if pid and str(pid).isdigit():
        ids = [r["id"] for r in q("SELECT id FROM models WHERE product_id=?", pid)]; row = q("SELECT name FROM products WHERE id=?", pid)
    elif fid and str(fid).isdigit():
        ids = [r["id"] for r in q("SELECT id FROM models WHERE family_id=?", fid)]; row = q("SELECT name FROM families WHERE id=?", fid)
    else:
        row = []
    if not row:
        abort(404)
    name = row[0]["name"]
    items, _ = build_catalog(ids, name)
    if not items:
        flash("لا توجد طرازات معتمدة وظاهرة ونشطة في هذا النطاق."); return redirect("/tree")
    profile = _profile()
    options = {"title": name, "product_id": str(pid or ""), "family_id": str(fid or "")}
    buf = pdf_engine.build_document(_engine_items(items, "product_catalog"), profile, doc_type="product_catalog", options=options)
    return _pdf(buf, "catalog.pdf", "catalog", len(items),
                doc_type="product_catalog", profile=profile, title=name, options=options)


@bp.route("/master.pdf")
def master_pdf():
    items, _ = build_catalog([r["id"] for r in q("SELECT id FROM models")], "")
    if not items:
        flash("لا توجد طرازات معتمدة وظاهرة."); return redirect("/tree")
    items.sort(key=lambda d: (d.get("family") or "~", (d["product"] or {}).get("name") or "~", d["m"]["code"]))
    profile = _profile()
    options = {"title": "Master Catalog"}
    buf = pdf_engine.build_document(_engine_items(items, "master_catalog"), profile, doc_type="master_catalog", options=options)
    return _pdf(buf, "master_catalog.pdf", "master_catalog", len(items),
                doc_type="master_catalog", profile=profile, title=options["title"], options=options)


# ------------------------------------------------------------------ search
@bp.route("/search")
def search():
    a = request.args
    text, fam, prod, sup = a.get("q", "").strip(), a.get("family", ""), a.get("product", ""), a.get("supplier", "")
    status, sort = a.get("status", "approved"), a.get("sort", "code")
    sql = """SELECT m.*, p.name pname, p.ar_name par, f.name fam, c.supplier FROM models m
             LEFT JOIN products p ON p.id=m.product_id LEFT JOIN families f ON f.id=m.family_id
             LEFT JOIN catalogs c ON c.id=m.catalog_id WHERE m.active=1"""
    args = []
    if status != "all": sql += " AND m.status=?"; args.append(status)
    if fam: sql += " AND m.family_id=?"; args.append(fam)
    if prod: sql += " AND m.product_id=?"; args.append(prod)
    if sup: sql += " AND c.supplier=?"; args.append(sup)
    c = db.conn()
    rows = c.execute(sql, args).fetchall()
    specs = {}
    amap = dictionary.load_map(c)
    for s in c.execute("SELECT * FROM specs WHERE hidden=0 ORDER BY sort,id"):
        mp = amap.get(dictionary.key(s["attr"]))
        specs.setdefault(s["model_id"], []).append(((mp or {}).get("canonical") or s["attr"], s["company_value"] or s["supplier_value"]))
    thumbs = {}
    for l in c.execute("""SELECT l.model_id, i.file, l.ok_sheet FROM image_links l JOIN images i ON i.id=l.image_id
                          ORDER BY l.is_primary DESC, l.ok_sheet DESC, l.id"""):
        thumbs.setdefault(l["model_id"], l["file"])
    attrs = []
    for r in c.execute("SELECT attr FROM specs GROUP BY attr ORDER BY COUNT(*) DESC LIMIT 120"):
        nm = (amap.get(dictionary.key(r["attr"])) or {}).get("canonical") or r["attr"]
        if nm not in attrs: attrs.append(nm)
    attrs = attrs[:60]
    fams = c.execute("SELECT * FROM families ORDER BY name").fetchall()
    prods = c.execute("SELECT id,name FROM products WHERE active=1 ORDER BY name").fetchall()
    sups = [r["supplier"] for r in c.execute("SELECT DISTINCT supplier FROM catalogs WHERE supplier<>''")]
    profs = profiles.load_all(c); active = profiles.load(c)["id"]
    c.close()
    toks = [norm(t) for t in text.split() if t]
    filt = []
    for k in (1, 2, 3):
        at, op, v = a.get(f"a{k}", "").strip(), a.get(f"o{k}", "contains"), a.get(f"v{k}", "").strip()
        if at and v: filt.append((norm(at), op, v))
    out = []
    for m in rows:
        sp = specs.get(m["id"], [])
        if toks:
            hay = norm(" ".join([m["code"] or "", m["series"] or "", m["ar_name"] or "", m["pname"] or "", m["par"] or "", m["fam"] or "", m["supplier"] or ""]
                                + [f"{x} {y}" for x, y in sp]))
            if not all(t in hay for t in toks): continue
        ok = True
        for at, op, v in filt:
            vals = [y for x, y in sp if norm(x) == at]
            if not vals: ok = False; break
            if op == "contains": ok = any(norm(v) in norm(y) for y in vals)
            else:
                n, t = num(vals[0]), num(v)
                ok = n is not None and t is not None and (n >= t if op == "gte" else n <= t if op == "lte" else n == t)
            if not ok: break
        if ok: out.append(dict(m=m, specs=sp[:5], thumb=thumbs.get(m["id"])))
    key = {"code": lambda r: (r["m"]["code"] or ""), "product": lambda r: ((r["m"]["pname"] or "~"), r["m"]["code"] or ""),
           "new": lambda r: -r["m"]["id"]}.get(sort, lambda r: r["m"]["code"] or "")
    out.sort(key=key)
    return render_template("search", res=out[:300], total=len(out), a=a, fams=fams, prods=prods, sups=sups, attrs=attrs,
                           profs=profs, active=active, all_ids=",".join(str(r["m"]["id"]) for r in out))


# ------------------------------------------------------------------ export settings panel
BOOLS = ("logo_plate", "show_supplier", "show_date", "show_page", "show_desc", "cover", "toc", "dividers")


@bp.route("/export-settings")
def export_settings():
    c = db.conn()
    pid = request.args.get("pid") or db.get_setting(c, "active_profile")
    p = profiles.load(c, pid); active = db.get_setting(c, "active_profile")
    allp = profiles.load_all(c)
    versions = c.execute("SELECT id,version_num,name,created FROM profile_versions WHERE profile_id=? ORDER BY version_num DESC LIMIT 20", (p["id"],)).fetchall()
    c.close()
    fl = profiles.fonts()
    return render_template("exsettings", p=p, allp=allp, active=int(active or 0), fonts_en=sorted(fl), fonts_ar=sorted(k for k, v in fl.items() if v["arabic"]),
                           papers=profiles.PAPERS, paper_colors=profiles.PAPER_COLORS, textures=profiles.TEXTURES, presets=profiles.PRESETS,
                           versions=versions, arabic_ok=pdf_engine.ARABIC_OK, ts=int(__import__("time").time()))


@bp.route("/export-settings/save", methods=["POST"])
def export_settings_save():
    f = request.form; pid = int(f["pid"]); c = db.conn()
    cur = profiles.load(c, pid, resolve=False); new = dict(cur)
    for k in profiles.DEFAULTS:
        if k in BOOLS: new[k] = "1" if f.get(k) == "on" else "0"
        elif k in f and k not in ("logo", "wm_image"): new[k] = f.get(k, "").strip()
    new["name"] = f.get("name", "").strip() or cur["name"]
    try:
        new = profiles.validate_settings(new)
    except ValueError as e:
        c.close()
        flash(str(e))
        return redirect(f"/export-settings?pid={pid}")
    for fld, key in (("logo", "logo"), ("wm_image_file", "wm_image"), ("bg_image_file", "bg_image")):
        up = request.files.get(fld)
        if up and up.filename:
            data = up.read()
            try:
                if len(data) > 5 * 1024 * 1024: raise ValueError("too big")
                PILImage.open(io.BytesIO(data)).verify()
            except Exception:
                flash(f"الملف «{up.filename}» ليس صورة صالحة أو يتجاوز حجمه 5 ميجابايت."); continue
            akey = f"profile-{pid}-{key}"
            assets.put(akey, up.filename, data); new[key] = "asset:" + akey
    if f.get("remove_logo"): new["logo"] = ""
    if f.get("remove_wm"): new["wm_image"] = ""
    if f.get("remove_bg"): new["bg_image"] = ""
    db.log(c, "profile", pid, "edit", f"export settings of '{new['name']}'")
    profiles.save(c, pid, new); c.commit(); c.close()
    flash("حُفظت إعدادات التصدير بنجاح.")
    return redirect(f"/export-settings?pid={pid}")


@bp.route("/export-settings/font", methods=["POST"])
def export_settings_font():
    reg = request.files.get("regular")
    if reg and reg.filename and reg.filename.lower().endswith(".ttf"):
        profiles.save_font(request.form.get("fname", "") or os.path.splitext(reg.filename)[0], reg, request.files.get("bold"))
        flash("أُضيف الخط. اختره من القائمة.")
    else:
        flash("يرجى رفع ملف خط بصيغة TTF.")
    return redirect(auth.back("/export-settings"))


@bp.route("/export-settings/profile", methods=["POST"])
def export_settings_profile():
    f = request.form; c = db.conn(); act = f.get("do"); pid = int(f.get("pid", 0) or 0)
    if act == "new":
        base = profiles.load(c, pid, resolve=False); pid = profiles.create(c, f.get("name", "").strip() or "هوية جديدة", base)
        copied = False
        for k in ("logo", "wm_image", "bg_image"):
            ref = base.get(k) or ""
            if ref.startswith("asset:"):
                old_key = ref[6:]; new_key = f"profile-{pid}-{k}"
                cp = c.execute("""INSERT INTO assets(key,name,mime,sha,size,data)
                                  SELECT ?,name,mime,sha,size,data FROM assets WHERE key=?
                                  ON CONFLICT(key) DO NOTHING""", (new_key, old_key))
                if cp.rowcount:
                    base[k] = "asset:" + new_key; copied = True
                else:
                    base[k] = ""
        if copied:
            profiles.save(c, pid, base)
    elif act == "activate": db.set_setting(c, "active_profile", str(pid)); flash("أصبحت هذه الهوية الافتراضية للتصدير.")
    elif act == "delete":
        if len(profiles.load_all(c)) > 1:
            c.execute("DELETE FROM profiles WHERE id=?", (pid,))
            if db.get_setting(c, "active_profile") == str(pid): db.set_setting(c, "active_profile", str(profiles.load_all(c)[0]["id"]))
            pid = int(db.get_setting(c, "active_profile"))
        else: flash("يجب الإبقاء على هوية واحدة على الأقل.")
    c.commit(); c.close()
    return redirect(f"/export-settings?pid={pid}")


@bp.route("/export-settings/restore-version", methods=["POST"])
def export_settings_restore_version():
    try:
        vid = int(request.form.get("vid", ""))
        pid = int(request.form.get("pid", ""))
    except (TypeError, ValueError):
        abort(400)
    c = db.conn()
    try:
        version = c.execute("SELECT * FROM profile_versions WHERE id=? AND profile_id=?", (vid, pid)).fetchone()
        if not version:
            abort(404)
        try:
            data = json.loads(version["data"] or "{}")
        except (TypeError, json.JSONDecodeError):
            flash("تعذر قراءة إعدادات هذا الإصدار.")
            return redirect(f"/export-settings?pid={pid}")
        data["name"] = version["name"]
        try:
            data = profiles.validate_settings(data)
        except ValueError as e:
            flash(str(e))
            return redirect(f"/export-settings?pid={pid}")
        profiles.save(c, pid, data)
        db.log(c, "profile", pid, "restore_version", f"restored profile version {version['version_num']}")
        c.commit()
        flash(f"تمت استعادة إصدار الهوية v{version['version_num']} وحُفظ كإصدار جديد.")
    finally:
        c.close()
    return redirect(f"/export-settings?pid={pid}")


def _sample():
    r = q("""SELECT m.id FROM models m WHERE m.active=1 ORDER BY (m.status='approved') DESC,
             (SELECT COUNT(*) FROM image_links l WHERE l.model_id=m.id AND l.ok_sheet=1) DESC, m.id LIMIT 1""")
    if r:
        d = model_data(r[0]["id"])
        if d:
            return d
    m = dict(code="SAMPLE-100", series="Standard Series", status="approved", ar_name="نموذج تجريبي", supplier="Supplier", product_id=1, active=1, visible=1)
    sp = [dict(attr=a, supplier_value=v, company_value="", hidden=0) for a, v in
          [("Motor power", "3 kW"), ("Power supply", "380 V AC, 60 Hz, 3-phase"), ("Cutting rate", "32 cuts/min"), ("N.W", "350 kg"), ("Dimensions (L × W × H)", "1320 × 500 × 650 mm")]]
    return dict(m=m, specs=sp, links=[], product=dict(id=1, name="Rebar Cutting Machine", ar_name="ماكينة قص حديد التسليح", description="Sample product description."))


@bp.route("/preview.png")
def preview():
    p = _profile(); kind = request.args.get("kind", "datasheet"); d = _sample()
    doc_type = "product_catalog" if kind == "cover" else "tds"
    opts = {"title": "Product Catalog 2026", "cover": "1" if kind == "cover" else "0", "toc": "0"}
    buf = pdf_engine.build_document(_engine_items([d], doc_type), p, doc_type=doc_type, options=opts)
    return Response(pdfgen.render_png(buf, 0, 1.0), mimetype="image/png")
