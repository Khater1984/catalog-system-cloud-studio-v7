"""Product management layer: products (parents of models), suggestions, move/disable, gaps report."""
import re
from flask import Blueprint, request, redirect, render_template, flash, abort
import db, pdfgen, dedupe

bp = Blueprint("products", __name__)


def q(sql, *a):
    c = db.conn(); r = c.execute(sql, a).fetchall(); c.close(); return r


def manual_catalog(c):
    r = c.execute("SELECT id FROM catalogs WHERE file_sha='manual'").fetchone()
    if r: return r["id"]
    return c.execute("INSERT INTO catalogs(name,supplier,file_sha,status,pages) VALUES('إدخال يدوي','', 'manual','done',0)").lastrowid


def assign(c, model_ids, product_id):
    db.touch(c, "models", model_ids)
    p = c.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone() if product_id else None
    for mid in model_ids:
        old = c.execute("SELECT m.product_id, p.name FROM models m LEFT JOIN products p ON p.id=m.product_id WHERE m.id=?", (mid,)).fetchone()
        c.execute("UPDATE models SET product_id=?, family_id=COALESCE(?, family_id), public_enabled=0 WHERE id=?",
                  (product_id or None, p["family_id"] if p else None, mid))
        db.log(c, "model", mid, "move_product", f"{old['name'] or '—'} -> {p['name'] if p else '—'}")


KW = [("Signature", r"S\s*ignature"), ("Heavy", r"Heavy"), ("Standard", r"Standard"), ("CNC", r"CNC"), ("Manual", r"Manual"), ("Diesel", r"Diesel")]


def sup_of_product(c, pid):
    return {dedupe.norm_supplier(r["supplier"]) for r in c.execute(
        "SELECT DISTINCT k.supplier FROM models m JOIN catalogs k ON k.id=m.catalog_id WHERE m.product_id=? AND k.file_sha<>'manual'", (pid,)).fetchall()}


def _kw(name):
    return next((k for k, pat in KW if re.search(pat, name or "", re.I)), "")


def merge_candidates(c, threshold=0.6):
    """Products of DIFFERENT suppliers that look like the same machine: same series keyword and mostly the same spec attributes.
    Only a suggestion; a person confirms each merge."""
    import dictionary
    amap = dictionary.load_map(c)
    prods = c.execute("SELECT id,name,family_id FROM products WHERE active=1").fetchall()
    info = {}
    for p in prods:
        attrs = set()
        for r in c.execute("SELECT DISTINCT s.attr FROM specs s JOIN models m ON m.id=s.model_id WHERE m.product_id=?", (p["id"],)).fetchall():
            k = dictionary.key(r["attr"]); attrs.add(((amap.get(k) or {}).get("canonical") or k).lower())
        sups = sup_of_product(c, p["id"])
        if attrs and sups:
            info[p["id"]] = (p, attrs, sups, _kw(p["name"]))
    out, ids = [], sorted(info)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            pa, aa, sa, ka = info[a]; pb, ab, sb, kb = info[b]
            if sa & sb or (ka and kb and ka != kb):
                continue
            j = len(aa & ab) / len(aa | ab)
            if j >= threshold:
                out.append(dict(a=pa, b=pb, score=j, same_family=(pa["family_id"] == pb["family_id"]), sup_a=", ".join(sorted(sa)), sup_b=", ".join(sorted(sb))))
    return sorted(out, key=lambda x: -x["score"])[:40]


@bp.route("/products/merge")
def merge_page():
    c = db.conn()
    try:
        rows = merge_candidates(c)
    finally:
        c.close()
    return render_template("merge", rows=rows)


@bp.route("/products/merge-do", methods=["POST"])
def merge_do():
    keep, drop = int(request.form["keep"]), int(request.form["drop"])
    c = db.conn()
    try:
        a = c.execute("SELECT * FROM products WHERE id=? AND active=1", (keep,)).fetchone()
        b = c.execute("SELECT * FROM products WHERE id=? AND active=1", (drop,)).fetchone()
        if not a or not b or keep == drop:
            flash("المنتجات المحددة غير متاحة للدمج."); return redirect("/products/merge")
        ids = [r["id"] for r in c.execute("SELECT id FROM models WHERE product_id=?", (drop,)).fetchall()]
        assign(c, ids, keep)
        keep_primary = c.execute("SELECT COUNT(*) n FROM product_image_links WHERE product_id=? AND is_primary=1", (keep,)).fetchone()["n"] > 0
        for link in c.execute("SELECT * FROM product_image_links WHERE product_id=? ORDER BY sort_order,id", (drop,)).fetchall():
            primary = 1 if link["is_primary"] and not keep_primary else 0
            cur = c.execute("""INSERT INTO product_image_links(image_id,product_id,image_role,confidence,is_primary,ok_catalog,ok_sheet,sort_order)
                               VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(image_id,product_id) DO NOTHING""",
                            (link["image_id"], keep, link["image_role"], link["confidence"], primary,
                             link["ok_catalog"], link["ok_sheet"], link["sort_order"]))
            if cur.rowcount and primary:
                keep_primary = True
        c.execute("DELETE FROM product_image_links WHERE product_id=?", (drop,))
        c.execute("UPDATE products SET active=0 WHERE id=?", (drop,))
        db.log(c, "product", keep, "merge_in", f"{b['name']} (#{drop}): {len(ids)} models")
        db.log(c, "product", drop, "merged_into", f"{a['name']} (#{keep})")
        c.commit()
    finally:
        c.close()
    flash(f"دُمج المنتج «{b['name']}» في «{a['name']}»، ونُقل {len(ids)} طرازًا.")
    return redirect("/products/merge")


@bp.route("/products")
def products():
    rows = q("""SELECT p.*, f.name fam,
        (SELECT COUNT(*) FROM models m WHERE m.product_id=p.id AND m.active=1) n,
        (SELECT COUNT(*) FROM models m WHERE m.product_id=p.id AND m.active=1 AND m.status='approved') appr
        FROM products p LEFT JOIN families f ON f.id=p.family_id WHERE p.active=1 ORDER BY f.name, p.name""")
    import product_studio                       # the product list lives in the workspace module family
    return product_studio.index()


@bp.route("/product/add", methods=["POST"])
def product_add():
    name = request.form.get("name", "").strip()
    if not name:
        return redirect("/products")
    c = db.conn()
    pid = c.execute("INSERT INTO products(name,ar_name,family_id) VALUES(?,?,?)",
                    (name, request.form.get("ar_name", "").strip(), request.form.get("family_id") or None)).lastrowid
    db.log(c, "product", pid, "create", name); c.commit(); c.close()
    return redirect(f"/product/{pid}")


@bp.route("/products/suggest", methods=["POST"])
def products_suggest():
    """Propose products by grouping unassigned models of the same code prefix + series. Marked as suggestions."""
    c = db.conn(); made = 0
    rows = c.execute("SELECT * FROM models WHERE product_id IS NULL AND active=1 ORDER BY catalog_id, page, id").fetchall()
    groups = {}
    for m in rows:
        series = next((k for k, pat in KW if re.search(pat, m["series"] or "", re.I)), "")
        key = (m["catalog_id"], m["suggested_group"] or "", series)
        groups.setdefault(key, []).append(m)
    for (cat, grp, series), ms in groups.items():
        sup = (c.execute("SELECT supplier FROM catalogs WHERE id=?", (cat,)).fetchone() or {"supplier": ""})["supplier"] or ""
        base = (f"{grp} {series}").strip() or (ms[0]["code"])
        pid = None
        for ex in c.execute("SELECT id FROM products WHERE name=? AND active=1", (base,)).fetchall():
            if sup_of_product(c, ex["id"]) <= {dedupe.norm_supplier(sup)}:       # same supplier (or empty product): safe to reuse
                pid = ex["id"]; break
        name = base
        if pid is None:
            if c.execute("SELECT 1 FROM products WHERE name=? AND active=1", (base,)).fetchone() and sup:
                name = f"{base} - {sup}"                                          # same name, different supplier: keep apart, merge later by evidence
            ex = c.execute("SELECT id FROM products WHERE name=? AND active=1", (name,)).fetchone()
            if ex:
                pid = ex["id"]
            else:
                fam = next((m["family_id"] for m in ms if m["family_id"]), None)
                pid = c.execute("INSERT INTO products(name,family_id,suggested) VALUES(?,?,1)", (name, fam)).lastrowid
                db.log(c, "product", pid, "suggest", f"{name} (prefix '{grp}', series '{series}', catalog {cat}, supplier '{sup}')"); made += 1
        assign(c, [m["id"] for m in ms], pid)
    c.commit(); c.close()
    flash(f"أُنشئ {made} منتجًا مقترحًا. راجع المنتجات، وحدّد أسماءها الفعلية، ثم اعتمد كل منتج.")
    return redirect("/products")


@bp.route("/product/<int:pid>/save", methods=["POST"])
def product_save(pid):
    f = request.form; c = db.conn()
    old = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    sent = f.get("version")
    if sent is not None and str(old["version"] or 1) != sent:               # another employee saved this product after the form was opened
        last = c.execute("SELECT username, ts FROM audit WHERE entity='product' AND entity_id=? ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
        c.close()
        flash(f"أجرى {last['username'] if last else 'مستخدم آخر'} تعديلًا على هذا المنتج ({last['ts'] if last else ''} UTC) بعد فتح الصفحة. لم يُحفظ تعديلك؛ راجع التغييرات ثم أعد تنفيذ التعديل.")
        return redirect(f"/product/{pid}")
    fam = f.get("family_id") or None
    c.execute("UPDATE products SET name=?, ar_name=?, description=?, family_id=?, suggested=0 WHERE id=?",
              (f.get("name", "").strip() or old["name"], f.get("ar_name", "").strip(), f.get("description", "").strip(), fam, pid))
    if "supplier" in f:                                                    # fields of the product workspace
        c.execute("UPDATE products SET supplier=?, manufacturer=?, status=? WHERE id=?",
                  (f.get("supplier", "").strip(), f.get("manufacturer", "").strip(), f.get("status") if f.get("status") in ("staging", "approved") else old["status"], pid))
    c.execute("UPDATE models SET family_id=? WHERE product_id=?", (fam, pid))
    ch = [f"{k}: '{old[k] or ''}' -> '{f.get(k, '').strip()}'" for k in ("name", "ar_name", "description") if (f.get(k, "").strip() != (old[k] or ""))]
    if str(fam or "") != str(old["family_id"] or ""):
        names = {r["id"]: r["name"] for r in c.execute("SELECT id,name FROM families").fetchall()}
        ch.append(f"family: '{names.get(old['family_id'], '-')}' -> '{names.get(int(fam), '-') if fam else '-'}'")
    db.touch(c, "products", [pid]); db.touch(c, "models", [r["id"] for r in c.execute("SELECT id FROM models WHERE product_id=?", (pid,)).fetchall()])
    db.log(c, "product", pid, "edit", "; ".join(ch) or "confirmed")
    c.commit(); c.close()
    return redirect((f.get("next") if (f.get("next") or "").startswith("/") and not f.get("next").startswith("//") else f"/product/{pid}"))


@bp.route("/product/<int:pid>/addmodel", methods=["POST"])
def product_addmodel(pid):
    code = request.form.get("code", "").strip()
    if not code:
        return redirect(f"/product/{pid}")
    c = db.conn()
    p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    mid = c.execute("INSERT INTO models(catalog_id,code,series,page,family_id,product_id) VALUES(?,?,?,?,?,?)",
                    (manual_catalog(c), code, request.form.get("series", "").strip(), 0, p["family_id"], pid)).lastrowid
    db.log(c, "model", mid, "create_manual", f"{code} in {p['name']}")
    c.commit(); c.close()
    return redirect(f"/model/{mid}")


@bp.route("/product/<int:pid>/move", methods=["POST"])
def product_move_all(pid):
    target = request.form.get("target") or None
    c = db.conn()
    ids = [r["id"] for r in c.execute("SELECT id FROM models WHERE product_id=?", (pid,))]
    assign(c, ids, target)
    c.commit(); c.close()
    flash(f"نُقل {len(ids)} طرازًا.")
    return redirect(f"/product/{pid}")


@bp.route("/product/<int:pid>/archive", methods=["POST"])
def product_archive(pid):
    if q("SELECT 1 FROM models WHERE product_id=?", pid):
        flash("يحتوي المنتج على طرازات؛ انقلها إلى منتج آخر أولًا. سيظل سجلها محفوظًا.")
        return redirect(f"/product/{pid}")
    c = db.conn(); c.execute("UPDATE products SET active=0 WHERE id=?", (pid,)); db.log(c, "product", pid, "archive", "no models left")
    c.commit(); c.close()
    return redirect("/products")


@bp.route("/gaps")
def gaps():
    c = db.conn(); out = []
    for m in c.execute("""SELECT m.*, p.name pname FROM models m LEFT JOIN products p ON p.id=m.product_id
                          WHERE m.active=1 ORDER BY m.code""").fetchall():
        iss = []
        if not m["product_id"]: iss.append("غير مرتبط بمنتج")
        if not m["family_id"]: iss.append("غير مصنف")
        if not m["ar_name"]: iss.append("لا يوجد اسم عربي")
        if not c.execute("SELECT 1 FROM specs WHERE model_id=? AND hidden=0 LIMIT 1", (m["id"],)).fetchone(): iss.append("لا توجد مواصفات")
        imgs = c.execute("""SELECT i.w, l.is_primary, l.ok_sheet FROM image_links l JOIN images i ON i.id=l.image_id
                            WHERE l.model_id=? AND (l.ok_sheet=1 OR l.ok_catalog=1)""", (m["id"],)).fetchall()
        if not imgs: iss.append("لا توجد صورة معتمدة")
        else:
            if not any(i["is_primary"] for i in imgs): iss.append("لا توجد صورة رئيسية")
            if all(i["w"] / (pdfgen.SHEET_IMG_W_CM / 2.54) < 150 for i in imgs): iss.append("الصورة منخفضة الدقة")
        if m["status"] != "approved": iss.append("غير معتمد")
        if iss: out.append((m, iss))
    total = c.execute("SELECT COUNT(*) n FROM models WHERE active=1").fetchone()["n"]
    c.close()
    kinds = {}
    for m, iss in out:
        for i in iss: kinds[i] = kinds.get(i, 0) + 1
    return render_template("gaps", rows=out, kinds=kinds, total=total)


@bp.route("/products/archived")
def archived_page():
    c = db.conn()
    try:
        rows = c.execute("SELECT id,name,family_id FROM products WHERE active=0 ORDER BY name").fetchall()
    finally:
        c.close()
    return render_template("archived", rows=rows)


@bp.route("/product/<int:pid>/unarchive", methods=["POST"])
def product_unarchive(pid):
    c = db.conn()
    try:
        r = c.execute("SELECT name FROM products WHERE id=? AND active=0", (pid,)).fetchone()
        if r:
            c.execute("UPDATE products SET active=1 WHERE id=?", (pid,)); db.touch(c, "products", [pid]); db.log(c, "product", pid, "unarchive", r["name"]); c.commit()
    finally:
        c.close()
    return redirect("/products/archived")
