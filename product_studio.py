"""Product Studio: Central Management for Products, Variants, Immutable Specs & Images."""
import os, hashlib
from flask import Blueprint, request, redirect, render_template_string, flash, abort
from werkzeug.utils import secure_filename
from PIL import Image
import db, storage, products as prod_mod

bp = Blueprint("studio", __name__, url_prefix="/studio")


# An image needs a person when it has no trustworthy link yet: no model link of confidence high/medium/confirmed/manual and no product link.
NEEDS_PERSON = """i.disabled=0
    AND NOT EXISTS (SELECT 1 FROM image_links l WHERE l.image_id=i.id AND l.confidence IN ('high','medium','confirmed','manual'))
    AND NOT EXISTS (SELECT 1 FROM product_image_links pl WHERE pl.image_id=i.id)"""


def review_image_count(c=None, catalog=None):
    own = c is None
    c = c or db.conn()
    try:
        sql, a = "SELECT COUNT(*) FROM images i WHERE " + NEEDS_PERSON, []
        if catalog:
            sql += " AND i.catalog_id=?"; a.append(catalog)
        return c.execute(sql, a).fetchone()[0]
    finally:
        if own:
            c.close()


def _next(default):
    """Where to go after a save: the page the form came from (a local path), else the default."""
    n = request.form.get("next", "")
    return n if n.startswith("/") and not n.startswith("//") else default


def _q(sql, *a):
    c = db.conn(); r = c.execute(sql, a).fetchall(); c.close(); return r


@bp.route("/")
def studio_home():
    return redirect("/products")                  # the product list lives at /products now


def index():
    q_txt = request.args.get("q", "").strip()
    fam = request.args.get("family", "")
    st = request.args.get("status", "")
    show_inactive = request.args.get("inactive", "") == "1"

    sql = """
        SELECT p.*, f.name fam_name,
               (SELECT COUNT(*) FROM models m WHERE m.product_id=p.id AND m.active=1) active_models,
               (SELECT COUNT(*) FROM models m WHERE m.product_id=p.id) total_models,
               (SELECT COUNT(*) FROM product_videos v WHERE v.product_id=p.id AND v.active=1) videos,
               (SELECT i.file FROM product_image_links pl JOIN images i ON i.id=pl.image_id
                WHERE pl.product_id=p.id AND i.disabled=0 ORDER BY pl.is_primary DESC, pl.sort_order ASC LIMIT 1) thumb
        FROM products p
        LEFT JOIN families f ON f.id=p.family_id
        WHERE 1=1
    """
    args = []
    if not show_inactive:
        sql += " AND p.active=1"
    if fam:
        sql += " AND p.family_id=?"; args.append(fam)
    if st:
        sql += " AND p.status=?"; args.append(st)
    if q_txt:
        sql += " AND (p.name LIKE ? OR p.ar_name LIKE ? OR p.supplier LIKE ? OR p.manufacturer LIKE ?)"
        args += [f"%{q_txt}%"] * 4

    prods = _q(sql + " ORDER BY p.sort_order ASC, p.id DESC", *args)
    fams = _q("SELECT * FROM families ORDER BY sort_order, name")
    unlinked_imgs = review_image_count()
    loose = _q("SELECT COUNT(*) n FROM models WHERE product_id IS NULL AND active=1")[0]["n"]
    return render_template_string(STUDIO_LIST_HTML, loose=loose, prods=prods, fams=fams, q_txt=q_txt,
                                  fam=fam, st=st, show_inactive=show_inactive, unlinked_imgs=unlinked_imgs)


@bp.route("/product/create", methods=["POST"])
def product_create():
    f = request.form
    name = f.get("name", "").strip()
    if not name:
        flash("اسم المنتج مطلوب"); return redirect("/products")
    c = db.conn()
    pid = c.execute("""
        INSERT INTO products(name, ar_name, description, supplier, manufacturer, family_id, status)
        VALUES(?,?,?,?,?,?,?)
    """, (name, f.get("ar_name", "").strip(), f.get("description", "").strip(),
          f.get("supplier", "").strip(), f.get("manufacturer", "").strip(),
          f.get("family_id") or None, f.get("status", "staging"))).lastrowid
    db.log(c, "product", pid, "create", name)
    c.commit(); c.close()
    return redirect(_next(f"/product/{pid}"))


@bp.route("/product/<int:pid>")
def product_detail(pid):
    return redirect(f"/product/{pid}")            # one product page now: the workspace (product_hub.py)


def _legacy_product_detail(pid):
    c = db.conn()
    p = c.execute("SELECT p.*, f.name fam_name FROM products p LEFT JOIN families f ON f.id=p.family_id WHERE p.id=?", (pid,)).fetchone()
    if not p:
        c.close(); abort(404)

    variants = c.execute("""
        SELECT m.*,
               (SELECT COUNT(*) FROM specs s WHERE s.model_id=m.id AND s.deleted=0) spec_cnt,
               (SELECT i.file FROM image_links l JOIN images i ON i.id=l.image_id
                WHERE l.model_id=m.id AND i.disabled=0 ORDER BY l.is_primary DESC, l.sort_order ASC LIMIT 1) thumb
        FROM models m WHERE m.product_id=? ORDER BY m.active DESC, m.sort_order ASC, m.code ASC
    """, (pid,)).fetchall()

    prod_specs = []
    for s in c.execute("SELECT * FROM specs WHERE product_id=? AND model_id IS NULL ORDER BY deleted ASC, sort ASC, id ASC", (pid,)):
        fv, fu, ffull = db.compute_final_spec(s)
        prod_specs.append(dict(s, final_val=fv, final_unit=fu, final_full=ffull))

    prod_imgs = c.execute("""
        SELECT pl.*, i.file, i.w, i.h, i.dpi, i.page, i.image_type, i.link_status, i.disabled, i.sha, c2.name cat_name
        FROM product_image_links pl
        JOIN images i ON i.id=pl.image_id
        LEFT JOIN catalogs c2 ON c2.id=i.catalog_id
        WHERE pl.product_id=? ORDER BY i.disabled ASC, pl.is_primary DESC, pl.sort_order ASC
    """, (pid,)).fetchall()

    revisions = c.execute("""
        SELECT r.*, s.attr FROM spec_revisions r
        LEFT JOIN specs s ON s.id=r.specification_id
        WHERE r.product_id=? ORDER BY r.id DESC LIMIT 40
    """, (pid,)).fetchall()

    all_prods = c.execute("SELECT id, name FROM products WHERE active=1 AND id<>? ORDER BY name", (pid,)).fetchall()
    fams = c.execute("SELECT * FROM families ORDER BY name").fetchall()
    c.close()

    return render_template_string(PRODUCT_DETAIL_HTML, p=p, variants=variants, prod_specs=prod_specs,
                                  prod_imgs=prod_imgs, revisions=revisions, all_prods=all_prods, fams=fams)


@bp.route("/product/<int:pid>/update", methods=["POST"])
def product_update(pid):
    f = request.form
    c = db.conn()
    act = f.get("act", "save")
    if act == "disable":
        c.execute("UPDATE products SET active=0 WHERE id=?", (pid,))
        c.execute("UPDATE models SET public_enabled=0 WHERE product_id=?", (pid,))
        db.log(c, "product", pid, "disable", "تعطيل المنتج دون حذف البيانات")
    elif act == "restore":
        c.execute("UPDATE products SET active=1 WHERE id=?", (pid,))
        db.log(c, "product", pid, "restore", "استعادة المنتج")
    else:
        c.execute("""
            UPDATE products SET name=?, ar_name=?, description=?, supplier=?, manufacturer=?, family_id=?, status=?
            WHERE id=?
        """, (f.get("name", "").strip(), f.get("ar_name", "").strip(), f.get("description", "").strip(),
              f.get("supplier", "").strip(), f.get("manufacturer", "").strip(),
              f.get("family_id") or None, f.get("status", "staging"), pid))
        c.execute("UPDATE models SET family_id=? WHERE product_id=?", (f.get("family_id") or None, pid))
        db.log(c, "product", pid, "update", f.get("name", "").strip())
    c.commit(); c.close()
    return redirect(_next(f"/product/{pid}"))


@bp.route("/product/<int:pid>/variant/add", methods=["POST"])
def variant_add(pid):
    f = request.form
    code = f.get("code", "").strip()
    if not code:
        flash("رقم/كود الطراز مطلوب"); return redirect(_next(f"/product/{pid}"))
    c = db.conn()
    p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    mid = c.execute("""
        INSERT INTO models(catalog_id, code, name, series, page, family_id, ar_name, product_id, status)
        VALUES(?, ?, ?, ?, 0, ?, ?, ?, 'staging')
    """, (prod_mod.manual_catalog(c), code, f.get("vname", "").strip(), f.get("series", "").strip(),
          p["family_id"], f.get("ar_name", "").strip(), pid)).lastrowid
    db.log(c, "model", mid, "create_variant", f"{code} under Product #{pid}")
    c.commit(); c.close()
    return redirect(f"/studio/variant/{mid}")


@bp.route("/variant/<int:mid>")
def variant_detail(mid):
    c = db.conn()
    m = c.execute("""
        SELECT m.*, p.name pname, p.id pid FROM models m
        LEFT JOIN products p ON p.id=m.product_id WHERE m.id=?
    """, (mid,)).fetchone()
    if not m:
        c.close(); abort(404)

    specs = []
    for s in c.execute("SELECT * FROM specs WHERE model_id=? ORDER BY deleted ASC, sort ASC, id ASC", (mid,)):
        fv, fu, ffull = db.compute_final_spec(s)
        specs.append(dict(s, final_val=fv, final_unit=fu, final_full=ffull))

    imgs = c.execute("""
        SELECT l.*, i.file, i.w, i.h, i.dpi, i.page, i.image_type, i.link_status, i.disabled, i.sha, c2.name cat_name
        FROM image_links l
        JOIN images i ON i.id=l.image_id
        LEFT JOIN catalogs c2 ON c2.id=i.catalog_id
        WHERE l.model_id=? ORDER BY i.disabled ASC, l.is_primary DESC, l.sort_order ASC
    """, (mid,)).fetchall()

    revs = c.execute("""
        SELECT r.*, s.attr FROM spec_revisions r
        LEFT JOIN specs s ON s.id=r.specification_id
        WHERE r.variant_id=? ORDER BY r.id DESC LIMIT 40
    """, (mid,)).fetchall()

    all_prods = c.execute("SELECT id, name FROM products WHERE active=1 ORDER BY name").fetchall()
    c.close()
    return render_template_string(VARIANT_DETAIL_HTML, m=m, specs=specs, imgs=imgs, revs=revs, all_prods=all_prods)


@bp.route("/variant/<int:mid>/update", methods=["POST"])
def variant_update(mid):
    f = request.form
    c = db.conn()
    old = c.execute("SELECT * FROM models WHERE id=?", (mid,)).fetchone()
    act = f.get("act", "save")

    if act == "disable":
        c.execute("UPDATE models SET active=0, public_enabled=0 WHERE id=?", (mid,))
        db.log(c, "model", mid, "disable", "تعطيل الطراز")
    elif act == "restore":
        c.execute("UPDATE models SET active=1 WHERE id=?", (mid,))
        db.log(c, "model", mid, "restore", "استعادة الطراز")
    elif act == "move_product":
        new_pid = int(f["target_product_id"])
        np = c.execute("SELECT * FROM products WHERE id=?", (new_pid,)).fetchone()
        c.execute("UPDATE models SET product_id=?, family_id=? WHERE id=?", (new_pid, np["family_id"], mid))
        c.execute("UPDATE models SET public_enabled=0 WHERE id=?", (mid,))
        c.execute("UPDATE specs SET product_id=? WHERE model_id=?", (new_pid, mid))
        c.execute("UPDATE spec_revisions SET product_id=? WHERE variant_id=?", (new_pid, mid))
        db.log(c, "model", mid, "move_product", f"Product #{old['product_id']} -> Product #{new_pid}")
    else:
        new_code = f.get("code", "").strip() or old["code"]
        new_status = f.get("status", "staging")
        new_visible = 1 if f.get("visible") else 0
        c.execute("""
            UPDATE models SET code=?, name=?, series=?, ar_name=?, status=?, visible=?
            WHERE id=?
        """, (new_code, f.get("vname", "").strip(),
              f.get("series", "").strip(), f.get("ar_name", "").strip(),
              new_status, new_visible, mid))
        if new_status != "approved" or not new_visible or new_code != old["code"]:
            c.execute("UPDATE models SET public_enabled=0 WHERE id=?", (mid,))
        db.log(c, "model", mid, "update", f.get("code", "").strip())

    c.commit(); c.close()
    return redirect(f"/studio/variant/{mid}")


@bp.route("/spec/mutate", methods=["POST"])
def spec_mutate():
    f = request.form
    op = f["op"]
    pid = int(f["product_id"]) if f.get("product_id") else None
    vid = int(f["variant_id"]) if f.get("variant_id") else None
    c = db.conn()

    if op == "ADD":
        attr = f.get("attr", "").strip()
        val = f.get("val", "").strip()
        unit = f.get("unit", "").strip()
        if attr and val:
            nxt = c.execute("SELECT COALESCE(MAX(sort),0)+1 n FROM specs WHERE COALESCE(model_id,0)=COALESCE(?,0) AND COALESCE(product_id,0)=COALESCE(?,0)",
                            (vid, pid)).fetchone()["n"]
            sid = c.execute("""
                INSERT INTO specs(model_id, product_id, attr, supplier_value, supplier_unit, company_value, company_unit, rule, sort, source_tag)
                VALUES(?, ?, ?, '', '', ?, ?, 'MANUAL', ?, 'manual')
            """, (vid, pid, attr, val, unit, nxt)).lastrowid
            db.log_spec_rev(c, pid, vid, sid, "ADD", "", f"{attr}: {val} {unit}".strip())

    elif op == "UPDATE":
        sid = int(f["spec_id"])
        row = c.execute("SELECT * FROM specs WHERE id=?", (sid,)).fetchone()
        _, _, old_full = db.compute_final_spec(row)
        new_attr = f.get("attr", "").strip() or row["attr"]
        new_val = f.get("company_value", "").strip()
        new_unit = f.get("company_unit", "").strip()
        hid = 1 if f.get("hidden") else 0
        srt = int(f.get("sort", row["sort"]))
        c.execute("""
            UPDATE specs SET attr=?, company_value=?, company_unit=?, hidden=?, sort=?, rule='MANUAL'
            WHERE id=?
        """, (new_attr, new_val, new_unit, hid, srt, sid))
        updated = c.execute("SELECT * FROM specs WHERE id=?", (sid,)).fetchone()
        _, _, new_full = db.compute_final_spec(updated)
        if old_full != new_full or new_attr != row["attr"]:
            db.log_spec_rev(c, row["product_id"] or pid, row["model_id"] or vid, sid, "UPDATE",
                            f"{row['attr']}={old_full}", f"{new_attr}={new_full}")

    elif op == "DELETE":
        sid = int(f["spec_id"])
        row = c.execute("SELECT * FROM specs WHERE id=?", (sid,)).fetchone()
        _, _, old_full = db.compute_final_spec(row)
        c.execute("UPDATE specs SET deleted=1 WHERE id=?", (sid,))
        db.log_spec_rev(c, row["product_id"] or pid, row["model_id"] or vid, sid, "DELETE", f"{row['attr']}={old_full}", "DELETED")

    elif op == "RESTORE":
        sid = int(f["spec_id"])
        row = c.execute("SELECT * FROM specs WHERE id=?", (sid,)).fetchone()
        c.execute("UPDATE specs SET deleted=0 WHERE id=?", (sid,))
        _, _, cur_full = db.compute_final_spec(row)
        db.log_spec_rev(c, row["product_id"] or pid, row["model_id"] or vid, sid, "RESTORE", "DELETED", f"{row['attr']}={cur_full}")

    elif op == "MOVE_SCOPE":
        sid = int(f["spec_id"])
        target_vid = int(f["target_variant_id"]) if f.get("target_variant_id") else None
        row = c.execute("SELECT * FROM specs WHERE id=?", (sid,)).fetchone()
        c.execute("UPDATE specs SET model_id=?, product_id=? WHERE id=?", (target_vid, pid, sid))
        db.log_spec_rev(c, pid, target_vid, sid, "MOVE", f"variant={row['model_id']}", f"variant={target_vid}")

    c.commit(); c.close()
    return redirect(request.referrer or "/products")


@bp.route("/image/upload", methods=["POST"])
def image_upload():
    f = request.form
    up = request.files.get("file")
    try:
        pid = int(f["product_id"]) if f.get("product_id") else None
        vid = int(f["variant_id"]) if f.get("variant_id") else None
    except (TypeError, ValueError):
        flash("معرّف المنتج أو الطراز غير صالح.")
        return redirect(request.referrer or "/products")
    if not pid and not vid:
        flash("اختر منتجًا أو طرازًا لربط الصورة.")
        return redirect(request.referrer or "/products")
    role = f.get("image_role", "main_product" if pid and not vid else "model_image")
    is_prim = 1 if f.get("is_primary") else 0

    if not up or not up.filename:
        flash("اختر ملف صورة"); return redirect(request.referrer or "/products")

    data = up.read()
    if not data:
        flash("ملف الصورة فارغ.")
        return redirect(request.referrer or "/products")
    sha = hashlib.sha256(data).hexdigest()
    c = db.conn()
    did = None
    fname = None
    created_file = False
    try:
        if pid:
            product = c.execute("SELECT id FROM products WHERE id=? AND active=1", (pid,)).fetchone()
            if not product:
                flash("المنتج المحدد غير موجود أو غير نشط.")
                return redirect(request.referrer or "/products")
        if vid:
            variant = c.execute("SELECT id, product_id FROM models WHERE id=? AND active=1", (vid,)).fetchone()
            if not variant or (pid and variant["product_id"] not in (None, pid)):
                flash("الطراز المحدد غير موجود أو لا يتبع المنتج المختار.")
                return redirect(request.referrer or "/products")

        existing = c.execute("SELECT * FROM images WHERE sha=?", (sha,)).fetchone()
        if existing:
            img_id = existing["id"]
        else:
            ext = os.path.splitext(secure_filename(up.filename))[1].lower() or ".png"
            fname = f"{sha[:20]}{ext}"
            did = storage.save_image(fname, data)
            created_file = True
            fpath = storage.image_path(fname)
            with Image.open(fpath) as pil:
                w, h = pil.size
                has_alpha = 1 if pil.mode in ("RGBA", "LA") else 0
                original_format = pil.format or ext.lstrip(".").upper()
            img_id = c.execute("""
                INSERT INTO images(catalog_id, page, file, sha, w, h, dpi, source, original_format,
                                   extraction_method, image_type, link_status, quality_flag, has_alpha, drive_id)
                VALUES(?, 0, ?, ?, ?, ?, 300, 'manual', ?, 'manual_upload', ?, 'confirmed', ?, ?, ?)
            """, (prod_mod.manual_catalog(c), fname, sha, w, h, original_format, role,
                  "ok" if w >= 300 else "low_res", has_alpha, did)).lastrowid

        if pid and not vid:
            if is_prim:
                c.execute("UPDATE product_image_links SET is_primary=0 WHERE product_id=?", (pid,))
            c.execute("""
                INSERT INTO product_image_links(image_id, product_id, image_role, confidence, is_primary)
                VALUES(?, ?, ?, 'confirmed', ?)
                ON CONFLICT(image_id, product_id) DO UPDATE SET is_primary=excluded.is_primary, image_role=excluded.image_role
            """, (img_id, pid, role, is_prim))
        if vid:
            if is_prim:
                c.execute("UPDATE image_links SET is_primary=0 WHERE model_id=?", (vid,))
            c.execute("""
                INSERT INTO image_links(image_id, model_id, confidence, image_role, is_primary, ok_catalog, ok_sheet)
                VALUES(?, ?, 'confirmed', ?, ?, 1, 1)
                ON CONFLICT(image_id, model_id) DO UPDATE SET is_primary=excluded.is_primary, image_role=excluded.image_role
            """, (img_id, vid, role, is_prim))
        c.commit()
    except Exception as exc:
        c.rollback()
        if created_file and fname:
            if did:
                storage.delete(did)
                try:
                    os.remove(storage._img_cache(fname))
                except OSError:
                    pass
            else:
                try:
                    os.remove(os.path.join(db.IMG_DIR, fname))
                except OSError:
                    pass
        is_fk = (getattr(exc, "pgcode", None) == "23503" or
                 getattr(exc, "sqlite_errorname", "") == "SQLITE_CONSTRAINT_FOREIGNKEY" or
                 "FOREIGN KEY constraint failed" in str(exc))
        if not is_fk:
            raise
        flash("لم يكتمل الربط لأن المنتج أو الطراز أو الصورة حُذف أثناء العملية. حدّث الصفحة ثم أعد المحاولة.")
    finally:
        c.close()
    return redirect(request.referrer or "/products")


@bp.route("/image/manage", methods=["POST"])
def image_manage():
    f = request.form
    act = f["act"]
    img_id = int(f["image_id"])
    pid = int(f["product_id"]) if f.get("product_id") else None
    vid = int(f["variant_id"]) if f.get("variant_id") else None
    c = db.conn()

    if act == "set_primary":
        if pid and not vid:
            c.execute("UPDATE product_image_links SET is_primary=0 WHERE product_id=?", (pid,))
            c.execute("UPDATE product_image_links SET is_primary=1 WHERE product_id=? AND image_id=?", (pid, img_id))
        if vid:
            c.execute("UPDATE image_links SET is_primary=0 WHERE model_id=?", (vid,))
            c.execute("UPDATE image_links SET is_primary=1 WHERE model_id=? AND image_id=?", (vid, img_id))
    elif act == "disable":
        c.execute("UPDATE images SET disabled=1 WHERE id=?", (img_id,))
    elif act == "restore":
        c.execute("UPDATE images SET disabled=0 WHERE id=?", (img_id,))
    elif act == "reorder":
        srt = int(f.get("sort_order", 0))
        if pid:
            c.execute("UPDATE product_image_links SET sort_order=? WHERE product_id=? AND image_id=?", (srt, pid, img_id))
        if vid:
            c.execute("UPDATE image_links SET sort_order=? WHERE model_id=? AND image_id=?", (srt, vid, img_id))
    elif act == "relink":
        try:
            target_pid = int(f.get("target_product_id") or f.get("product_id") or 0) or None
            target_vid = int(f.get("target_variant_id") or f.get("variant_id") or 0) or None
        except ValueError:
            c.close(); abort(400)
        if not target_pid and not target_vid:
            c.close(); flash("اختر منتجًا أو طرازًا لربط الصورة."); return redirect(request.referrer or "/studio/unlinked-images")
        image = c.execute("SELECT image_type FROM images WHERE id=?", (img_id,)).fetchone()
        if not image:
            c.close(); abort(404)
        role = f.get("image_role") or image["image_type"] or "model_image"
        if target_pid:
            if not c.execute("SELECT 1 FROM products WHERE id=? AND active=1", (target_pid,)).fetchone():
                c.close(); flash("المنتج المحدد غير متاح."); return redirect(request.referrer or "/studio/unlinked-images")
            c.execute("""INSERT INTO product_image_links(image_id,product_id,image_role,confidence,is_primary,ok_catalog,ok_sheet)
                         VALUES(?,?,?,'confirmed',0,1,1) ON CONFLICT(image_id,product_id) DO UPDATE SET image_role=excluded.image_role""",
                      (img_id, target_pid, role))
        if target_vid:
            if not c.execute("SELECT 1 FROM models WHERE id=? AND active=1", (target_vid,)).fetchone():
                c.close(); flash("الطراز المحدد غير متاح."); return redirect(request.referrer or "/studio/unlinked-images")
            c.execute("""
                INSERT INTO image_links(image_id, model_id, confidence, image_role, is_primary, ok_catalog, ok_sheet)
                VALUES(?, ?, 'confirmed', ?, 0, 1, 1)
                ON CONFLICT(image_id, model_id) DO UPDATE SET image_role=excluded.image_role
            """, (img_id, target_vid, role))
        c.execute("UPDATE images SET link_status='confirmed' WHERE id=?", (img_id,))

    c.commit(); c.close()
    return redirect(request.referrer or "/products")


@bp.route("/unlinked-images")
def unlinked_images():
    """Image workbench: every image that needs a person, shown with the models of its own catalog page as one-click choices."""
    cat = request.args.get("catalog", "")
    c = db.conn()
    try:
        sql, a = """SELECT i.*, c2.name cat_name, (SELECT COUNT(*) FROM image_links l WHERE l.image_id=i.id) n_links
                    FROM images i LEFT JOIN catalogs c2 ON c2.id=i.catalog_id WHERE """ + NEEDS_PERSON, []
        if cat.isdigit():
            sql += " AND i.catalog_id=?"; a.append(int(cat))
        imgs = []
        for im in c.execute(sql + " ORDER BY n_links ASC, i.catalog_id, i.page, i.id LIMIT 200", a).fetchall():
            d = dict(im)
            d["cands"] = [dict(m) for m in c.execute(
                """SELECT m.id, m.code, m.series, l.confidence FROM models m
                   LEFT JOIN image_links l ON l.model_id=m.id AND l.image_id=?
                   WHERE m.catalog_id=? AND m.page=? AND m.active=1 ORDER BY m.id""", (im["id"], im["catalog_id"], im["page"])).fetchall()] if im["catalog_id"] else []
            imgs.append(d)
        prods = c.execute("SELECT id,name FROM products WHERE active=1 ORDER BY name").fetchall()
        models = c.execute("""SELECT m.id,m.code,m.name,m.product_id,p.name pname
                             FROM models m LEFT JOIN products p ON p.id=m.product_id
                             WHERE m.active=1 ORDER BY m.code""").fetchall()
        total = review_image_count(c, int(cat) if cat.isdigit() else None)
    finally:
        c.close()
    return render_template_string(UNLINKED_HTML, imgs=imgs, prods=prods, models=models, total=total, cat=cat)


@bp.route("/image/link-many", methods=["POST"])
def image_link_many():
    """Confirm an image for one or several models of its page. Confirmed links are approved for data sheets and catalogs at once."""
    f = request.form
    try:
        img_id = int(f["image_id"])
        mids = sorted({int(x) for x in f.getlist("model_id") if x.strip().isdigit()})
    except (KeyError, ValueError):
        abort(400)
    c = db.conn()
    try:
        if not mids or not c.execute("SELECT 1 FROM images WHERE id=?", (img_id,)).fetchone():
            flash("اختر طرازًا واحدًا على الأقل."); return redirect("/studio/unlinked-images")
        role = (c.execute("SELECT image_type FROM images WHERE id=?", (img_id,)).fetchone()["image_type"]) or "model_image"
        done = []
        for mid in mids:
            m = c.execute("SELECT code FROM models WHERE id=? AND active=1", (mid,)).fetchone()
            if not m:
                continue
            c.execute("""INSERT INTO image_links(image_id, model_id, confidence, image_role, is_primary, ok_catalog, ok_sheet)
                         VALUES(?,?,'confirmed',?,0,1,1)
                         ON CONFLICT(image_id, model_id) DO UPDATE SET confidence='confirmed', ok_catalog=1, ok_sheet=1""", (img_id, mid, role))
            # the first confirmed image of a model becomes its main picture
            c.execute("""UPDATE image_links SET is_primary=1 WHERE image_id=? AND model_id=? AND NOT EXISTS
                         (SELECT 1 FROM image_links x WHERE x.model_id=? AND x.is_primary=1 AND x.image_id<>?)""", (img_id, mid, mid, img_id))
            done.append(m["code"])
        c.execute("UPDATE images SET link_status='confirmed' WHERE id=?", (img_id,))
        db.log(c, "image", img_id, "link", "confirmed for: " + ", ".join(done))
        c.commit()
    finally:
        c.close()
    flash("رُبطت الصورة واعتُمدت: " + "، ".join(done))
    return redirect(request.form.get("next") or "/studio/unlinked-images")


UNLINKED_HTML = """{% extends "base" %}{% block head %}<style>
.wb{display:grid;grid-template-columns:minmax(220px,300px) 1fr;gap:18px;align-items:start}
.wb img{width:100%;max-height:260px;object-fit:contain;background:#fff;border:1px solid var(--line);border-radius:10px}
.wb .acts{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}.wb .acts form{margin:0}
.wb .acts button{padding:9px 14px}
.wb h4{margin:0 0 6px}
@media(max-width:860px){.wb{grid-template-columns:1fr}}
</style>{% endblock %}{% block body %}
<div class="card"><h2 style="margin:0 0 6px">الصور التي تنتظر قرارك ({{ total }})</h2>
<p class="muted" style="margin:0">لكل صورة الطرازات الموجودة في صفحتها الأصلية. الربط بنقرة واحدة، وتُعتمد الصورة للتصدير فورًا.
{% if cat %}<a href="/studio/unlinked-images">كل الكتالوجات</a>{% endif %}</p></div>
{% for im in imgs %}<section class="card wb" id="img{{ im.id }}">
<div><img src="/img/{{ im.file }}" alt="" loading="lazy">
<div class="muted" style="font-size:13px;margin-top:6px">{{ im.w }}×{{ im.h }}{% if im.catalog_id %} · <a href="/catalog/{{ im.catalog_id }}/page/{{ im.page }}.png" target="_blank">الصفحة {{ im.page }} في الكتالوج</a>{% endif %}</div></div>
<div>
<h4>{% if im.n_links %}<span class="tag y">ربط ضعيف</span>{% else %}<span class="tag r">بلا ربط</span>{% endif %} {{ im.cat_name or 'إدخال يدوي' }}</h4>
{% if im.cands %}<p class="muted" style="margin:0 0 6px">طرازات هذه الصفحة:</p>
<div class="acts">{% for m in im.cands %}<form method="post" action="/studio/image/link-many"><input type="hidden" name="image_id" value="{{ im.id }}"><input type="hidden" name="model_id" value="{{ m.id }}"><input type="hidden" name="next" value="/studio/unlinked-images{{ '?catalog=' ~ cat if cat }}">
<button class="sec"><span class="ltr">{{ m.code }}</span>{% if m.confidence=='review' %} (مقترح){% endif %}</button></form>{% endfor %}
{% if im.cands|length>1 %}<form method="post" action="/studio/image/link-many"><input type="hidden" name="image_id" value="{{ im.id }}">{% for m in im.cands %}<input type="hidden" name="model_id" value="{{ m.id }}">{% endfor %}<input type="hidden" name="next" value="/studio/unlinked-images{{ '?catalog=' ~ cat if cat }}"><button>اربط بالكل ({{ im.cands|length }})</button></form>{% endif %}</div>
{% else %}<p class="muted">لا طرازات معروفة لهذه الصورة؛ اختر يدويًا:</p>{% endif %}
<details><summary class="muted">ربط يدوي بمنتج أو طراز آخر</summary>
<form method="post" action="/studio/image/manage" style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px"><input type="hidden" name="image_id" value="{{ im.id }}">
<select name="target_product_id"><option value="">منتج...</option>{% for p in prods %}<option value="{{ p.id }}">{{ p.name }}</option>{% endfor %}</select>
<select name="target_variant_id"><option value="">طراز...</option>{% for m in models %}<option value="{{ m.id }}">{{ m.code }}</option>{% endfor %}</select>
<button name="act" value="relink">ربط</button></form></details>
<form method="post" action="/studio/image/manage" style="margin-top:8px"><input type="hidden" name="image_id" value="{{ im.id }}"><button class="red" name="act" value="disable" title="شارة أو شعار أو تفصيل صغير ليس صورة منتج">تعطيل الصورة</button></form>
</div></section>
{% else %}<div class="card"><p class="ok" style="margin:0">لا صور تنتظر قرارك. كل الصور المستخرجة مرتبطة.</p></div>{% endfor %}
{% endblock %}"""


STUDIO_LIST_HTML = """{% extends "base" %}{% block body %}
<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px">
    <h2 style="margin:0">المنتجات</h2>
    <a class="btn sec" href="/studio/unlinked-images">ورشة الصور ({{ unlinked_imgs }})</a>
  </div>
  <form method="get" style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">
    <input name="q" value="{{ q_txt }}" placeholder="بحث بالاسم أو المورد أو المصنع...">
    <select name="family"><option value="">كل التصنيفات</option>{% for f in fams %}<option value="{{ f.id }}" {{ 'selected' if fam==f.id|string }}>{{ f.name }}</option>{% endfor %}</select>
    <select name="status"><option value="">كل الحالات</option><option value="approved" {{ 'selected' if st=='approved' }}>معتمد</option><option value="staging" {{ 'selected' if st=='staging' }}>مسودة</option></select>
    <label><input type="checkbox" name="inactive" value="1" {{ 'checked' if show_inactive }}> عرض المعطلة</label>
    <button>تصفية</button>
  </form>
</div>

<div class="card">
  <h4>+ إنشاء منتج رئيسي جديد</h4>
  <form method="post" action="/studio/product/create" style="display:flex;gap:8px;flex-wrap:wrap">
    <input name="name" placeholder="Product Name (EN)" required>
    <input name="ar_name" placeholder="اسم المنتج بالعربي">
    <input name="supplier" placeholder="المورد">
    <input name="manufacturer" placeholder="الشركة المصنعة">
    <select name="family_id"><option value="">-- التصنيف --</option>{% for f in fams %}<option value="{{ f.id }}">{{ f.name }}</option>{% endfor %}</select>
    <button>إنشاء المنتج</button>
  </form>
</div>

<div class="card" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
  <form method="post" action="/products/suggest" style="margin:0"><button class="sec">اقتراح منتجات من الطرازات غير المرتبطة{% if loose %} ({{ loose }}){% endif %}</button></form>
  <a class="btn sec" href="/products/merge">مقترحات دمج منتجات الموردين</a><a class="btn sec" href="/products/archived">المؤرشفة</a>
  {% if loose %}<span class="warn">{{ loose }} طرازًا بلا منتج.</span>{% endif %}
</div>

<div class="card">
  <table>
    <tr><th>الصورة</th><th>المنتج</th><th>التصنيف</th><th>المورد / المصنع</th><th>الطرازات</th><th>فيديو</th><th>الحالة</th><th></th></tr>
    {% for p in prods %}
    <tr style="{{ 'opacity:0.55' if not p.active }}">
      <td>{% if p.thumb %}<img src="/img/{{ p.thumb }}" style="height:48px;width:64px;object-fit:contain">{% else %}—{% endif %}</td>
      <td><a href="/product/{{ p.id }}"><b>{{ p.name }}</b></a>{% if p.suggested %} <span class="tag y">مقترح</span>{% endif %}<div class="muted">{{ p.ar_name }}</div></td>
      <td>{{ p.fam_name or '—' }}</td>
      <td>{{ p.supplier }} / {{ p.manufacturer }}</td>
      <td>{{ p.active_models }} نشط ({{ p.total_models }} إجمالي)</td>
      <td>{{ p.videos or '—' }}</td>
      <td><span class="tag {{ 'g' if p.status=='approved' else 'y' }}">{{ 'معتمد' if p.status=='approved' else 'مسودة' }}</span></td>
      <td style="white-space:nowrap"><a class="btn" href="/product/{{ p.id }}">فتح</a> <a class="btn sec" href="/compare?product={{ p.id }}">قارن</a> <a class="btn sec" href="/export-center/?doc_type=tds&product_id={{ p.id }}">ورقة بيانات</a></td>
    </tr>
    {% else %}<tr><td colspan="8" class="muted">لا توجد منتجات. أنشئ منتجك الأول أعلاه، أو اضغط «اقتراح منتجات».</td></tr>{% endfor %}
  </table>
</div>
{% endblock %}"""

PRODUCT_DETAIL_HTML = """{% extends "base" %}{% block body %}
<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap">
    <div><a href="/studio/">← العودة لقائمة المنتجات</a><h2 style="margin:4px 0">{{ p.name }} <small class="muted">(#{{ p.id }})</small></h2></div>
    <div style="display:flex;gap:8px">
      <a class="btn" href="/export-center?doc_type=tds&product_id={{ p.id }}">📄 تصدير ورقة البيانات الفنية للمنتج</a>
      <a class="btn sec" href="/export-center?doc_type=product_catalog&product_id={{ p.id }}">📚 تصدير كتالوج المنتج</a>
    </div>
  </div>
  <form method="post" action="/studio/product/{{ p.id }}/update" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:8px;margin-top:10px">
    <label>الاسم الإنجليزي <input name="name" value="{{ p.name }}" style="width:100%"></label>
    <label>الاسم العربي <input name="ar_name" value="{{ p.ar_name }}" style="width:100%"></label>
    <label>المورد <input name="supplier" value="{{ p.supplier }}" style="width:100%"></label>
    <label>المصنع <input name="manufacturer" value="{{ p.manufacturer }}" style="width:100%"></label>
    <label>التصنيف <select name="family_id" style="width:100%"><option value="">—</option>{% for f in fams %}<option value="{{ f.id }}" {{ 'selected' if p.family_id==f.id }}>{{ f.name }}</option>{% endfor %}</select></label>
    <label>الحالة <select name="status" style="width:100%"><option value="staging" {{ 'selected' if p.status=='staging' }}>مسودة</option><option value="approved" {{ 'selected' if p.status=='approved' }}>معتمد</option></select></label>
    <label style="grid-column:1/-1">الوصف العام <textarea name="description" rows="2" style="width:100%">{{ p.description }}</textarea></label>
    <div style="grid-column:1/-1;display:flex;gap:8px">
      <button name="act" value="save">💾 حفظ بيانات المنتج</button>
      {% if p.active %}<button class="red" name="act" value="disable">تعطيل المنتج دون حذف</button>
      {% else %}<button name="act" value="restore">استعادة المنتج</button>{% endif %}
    </div>
  </form>
</div>

<div class="card">
  <h3>الطرازات التابعة للمنتج (الطرازات)</h3>
  <form method="post" action="/studio/product/{{ p.id }}/variant/add" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
    <input name="code" placeholder="رقم الطراز (مثال: A100)" required>
    <input name="vname" placeholder="اسم الطراز">
    <input name="ar_name" placeholder="الاسم العربي">
    <input name="series" placeholder="السلسلة">
    <button>+ إضافة طراز</button>
  </form>
  <table>
    <tr><th>الصورة</th><th>الكود</th><th>الاسم</th><th>المواصفات</th><th>الحالة</th><th>إدارة</th></tr>
    {% for v in variants %}
    <tr style="{{ 'opacity:0.55' if not v.active }}">
      <td>{% if v.thumb %}<img src="/img/{{ v.thumb }}" style="height:42px">{% else %}—{% endif %}</td>
      <td class="ltr"><b>{{ v.code }}</b></td>
      <td>{{ v.name }} {{ v.ar_name }}</td>
      <td>{{ v.spec_cnt }}</td>
      <td>{{ 'نشط' if v.active else 'معطل' }} / {{ v.status }}</td>
      <td>
        <a class="btn" href="/studio/variant/{{ v.id }}">إدارة الطراز</a>
        <a class="btn sec" href="/export-center?doc_type=tds&model_id={{ v.id }}">TDS</a>
      </td>
    </tr>
    {% endfor %}
  </table>
</div>

<div class="card">
  <h3>المواصفات المشتركة على مستوى المنتج (المصدر الأصلي محفوظ)</h3>
  <form method="post" action="/studio/spec/mutate" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">
    <input type="hidden" name="op" value="ADD"><input type="hidden" name="product_id" value="{{ p.id }}">
    <input name="attr" placeholder="اسم المواصفة" required>
    <input name="val" placeholder="القيمة" required>
    <input name="unit" placeholder="الوحدة (مثال: Hz)">
    <button>+ إضافة مواصفة</button>
  </form>
  <table>
    <tr><th>المواصفة</th><th>المصدر الأصلي</th><th>تعديل القيمة</th><th>الوحدة</th><th>القيمة النهائية المعتمدة</th><th>إخفاء</th><th>ترتيب</th><th>إجراءات</th></tr>
    {% for s in prod_specs %}
    <tr style="{{ 'opacity:0.45;text-decoration:line-through' if s.deleted }}">
      <form method="post" action="/studio/spec/mutate">
        <input type="hidden" name="op" value="UPDATE"><input type="hidden" name="spec_id" value="{{ s.id }}"><input type="hidden" name="product_id" value="{{ p.id }}">
        <td><input name="attr" value="{{ s.attr }}" style="width:140px"></td>
        <td class="muted ltr">{{ s.supplier_value }} {{ s.supplier_unit }}</td>
        <td><input name="company_value" value="{{ s.company_value }}" style="width:110px"></td>
        <td><input name="company_unit" value="{{ s.company_unit }}" style="width:65px"></td>
        <td><b class="ok ltr">{{ s.final_full }}</b></td>
        <td><input type="checkbox" name="hidden" value="1" {{ 'checked' if s.hidden }}></td>
        <td><input type="number" name="sort" value="{{ s.sort }}" style="width:50px"></td>
        <td><button>حفظ</button></td>
      </form>
      <td>
        {% if not s.deleted %}
        <form method="post" action="/studio/spec/mutate" style="display:inline"><input type="hidden" name="op" value="DELETE"><input type="hidden" name="spec_id" value="{{ s.id }}"><input type="hidden" name="product_id" value="{{ p.id }}"><button class="red">حذف</button></form>
        {% else %}
        <form method="post" action="/studio/spec/mutate" style="display:inline"><input type="hidden" name="op" value="RESTORE"><input type="hidden" name="spec_id" value="{{ s.id }}"><input type="hidden" name="product_id" value="{{ p.id }}"><button>استعادة</button></form>
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </table>
</div>

<div class="card">
  <h3>معرض صور المنتج</h3>
  <form method="post" action="/studio/image/upload" enctype="multipart/form-data" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
    <input type="hidden" name="product_id" value="{{ p.id }}">
    <input type="file" name="file" accept="image/*" required>
    <select name="image_role">
      <option value="main_product">Main Product Image</option>
      <option value="additional">Additional Image</option>
      <option value="detail">Detail Image</option>
      <option value="technical_drawing">Technical Drawing</option>
      <option value="diagram">Diagram</option>
    </select>
    <label><input type="checkbox" name="is_primary" value="1" checked> صورة رئيسية</label>
    <button>+ رفع صورة</button>
  </form>
  <div class="imgs">
    {% for im in prod_imgs %}
    <div class="im" style="{{ 'opacity:0.5' if im.disabled }}">
      <img src="/img/{{ im.file }}">
      <div><span class="tag">{{ im.image_role }}</span> {% if im.is_primary %}<span class="tag g">رئيسية ★</span>{% endif %}</div>
      <div class="muted" style="font-size:11px">{{ im.w }}×{{ im.h }} | {{ im.cat_name or 'يدوي' }} ص{{ im.page }}</div>
      <form method="post" action="/studio/image/manage" style="margin-top:4px;display:flex;gap:4px;flex-wrap:wrap">
        <input type="hidden" name="image_id" value="{{ im.image_id }}"><input type="hidden" name="product_id" value="{{ p.id }}">
        <button name="act" value="set_primary">تعيين رئيسية</button>
        <button class="sec" name="act" value="{{ 'restore' if im.disabled else 'disable' }}">{{ 'استعادة' if im.disabled else 'تعطيل' }}</button>
      </form>
    </div>
    {% endfor %}
  </div>
</div>

<div class="card">
  <h3>سجل تعديلات المواصفات (سجل المراجعة)</h3>
  <table>
    <tr><th>التاريخ</th><th>العملية</th><th>المواصفة</th><th>القيمة السابقة</th><th>القيمة الجديدة</th><th>المنفذ</th></tr>
    {% for r in revisions %}
    <tr><td>{{ r.modified_at }}</td><td><b>{{ r.operation }}</b></td><td>{{ r.attr }}</td><td class="ltr">{{ r.old_value }}</td><td class="ltr">{{ r.new_value }}</td><td>{{ r.modified_by }}</td></tr>
    {% endfor %}
  </table>
</div>
{% endblock %}"""

VARIANT_DETAIL_HTML = """{% extends "base" %}{% block body %}
<div class="card">
  <a href="/studio/product/{{ m.pid }}">← العودة للمنتج: {{ m.pname }}</a>
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap">
    <h2>طراز: <span class="ltr">{{ m.code }}</span> <small class="muted">(Variant #{{ m.id }})</small></h2>
    <a class="btn" href="/export-center?doc_type=tds&model_id={{ m.id }}">📄 تصدير ورقة البيانات الفنية للطراز</a>
  </div>
  <form method="post" action="/studio/variant/{{ m.id }}/update" style="display:flex;gap:8px;flex-wrap:wrap">
    <input name="code" value="{{ m.code }}" placeholder="كود الطراز">
    <input name="vname" value="{{ m.name }}" placeholder="اسم الطراز">
    <input name="ar_name" value="{{ m.ar_name }}" placeholder="الاسم العربي">
    <input name="series" value="{{ m.series }}" placeholder="السلسلة">
    <select name="status"><option value="staging" {{ 'selected' if m.status=='staging' }}>مسودة</option><option value="approved" {{ 'selected' if m.status=='approved' }}>معتمد</option></select>
    <label><input type="checkbox" name="visible" value="1" {{ 'checked' if m.visible }}> ظاهر في الكتالوج</label>
    <button name="act" value="save">💾 حفظ</button>
    <button class="red" name="act" value="disable">تعطيل</button>
  </form>
  <form method="post" action="/studio/variant/{{ m.id }}/update" style="display:flex;gap:8px;margin-top:8px">
    <input type="hidden" name="act" value="move_product">
    <select name="target_product_id">{% for p in all_prods %}<option value="{{ p.id }}" {{ 'selected' if p.id==m.pid }}>{{ p.name }}</option>{% endfor %}</select>
    <button class="sec">نقل الطراز إلى منتج آخر (مع الحفاظ على مواصفاته وصوره)</button>
  </form>
</div>

<div class="card">
  <h3>مواصفات الطراز (المصدر الأصلي منفصل عن التعديلات)</h3>
  <form method="post" action="/studio/spec/mutate" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">
    <input type="hidden" name="op" value="ADD"><input type="hidden" name="product_id" value="{{ m.pid }}"><input type="hidden" name="variant_id" value="{{ m.id }}">
    <input name="attr" placeholder="اسم المواصفة" required>
    <input name="val" placeholder="القيمة" required>
    <input name="unit" placeholder="الوحدة">
    <button>+ إضافة مواصفة</button>
  </form>
  <table>
    <tr><th>المواصفة</th><th>المصدر الأصلي</th><th>قيمة الشركة المعدلة</th><th>الوحدة المعدلة</th><th>القيمة النهائية المعتمدة</th><th>إخفاء</th><th>ترتيب</th><th>إجراءات</th></tr>
    {% for s in specs %}
    <tr style="{{ 'opacity:0.45;text-decoration:line-through' if s.deleted }}">
      <form method="post" action="/studio/spec/mutate">
        <input type="hidden" name="op" value="UPDATE"><input type="hidden" name="spec_id" value="{{ s.id }}"><input type="hidden" name="product_id" value="{{ m.pid }}"><input type="hidden" name="variant_id" value="{{ m.id }}">
        <td><input name="attr" value="{{ s.attr }}" style="width:140px"></td>
        <td class="muted ltr">{{ s.supplier_value }} {{ s.supplier_unit }}</td>
        <td><input name="company_value" value="{{ s.company_value }}" style="width:110px"></td>
        <td><input name="company_unit" value="{{ s.company_unit }}" style="width:65px"></td>
        <td><b class="ok ltr">{{ s.final_full }}</b></td>
        <td><input type="checkbox" name="hidden" value="1" {{ 'checked' if s.hidden }}></td>
        <td><input type="number" name="sort" value="{{ s.sort }}" style="width:50px"></td>
        <td><button>حفظ</button></td>
      </form>
      <td>
        {% if not s.deleted %}
        <form method="post" action="/studio/spec/mutate" style="display:inline"><input type="hidden" name="op" value="DELETE"><input type="hidden" name="spec_id" value="{{ s.id }}"><input type="hidden" name="product_id" value="{{ m.pid }}"><input type="hidden" name="variant_id" value="{{ m.id }}"><button class="red">حذف</button></form>
        {% else %}
        <form method="post" action="/studio/spec/mutate" style="display:inline"><input type="hidden" name="op" value="RESTORE"><input type="hidden" name="spec_id" value="{{ s.id }}"><input type="hidden" name="product_id" value="{{ m.pid }}"><input type="hidden" name="variant_id" value="{{ m.id }}"><button>استعادة</button></form>
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </table>
</div>

<div class="card">
  <h3>صور الطراز</h3>
  <form method="post" action="/studio/image/upload" enctype="multipart/form-data" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
    <input type="hidden" name="product_id" value="{{ m.pid }}"><input type="hidden" name="variant_id" value="{{ m.id }}">
    <input type="file" name="file" accept="image/*" required>
    <select name="image_role">
      <option value="model_image">Model Image</option>
      <option value="additional">Additional Image</option>
      <option value="detail">Detail Image</option>
      <option value="technical_drawing">Technical Drawing</option>
      <option value="diagram">Diagram</option>
    </select>
    <label><input type="checkbox" name="is_primary" value="1" checked> صورة رئيسية للطراز</label>
    <button>+ رفع صورة</button>
  </form>
  <div class="imgs">
    {% for im in imgs %}
    <div class="im" style="{{ 'opacity:0.5' if im.disabled }}">
      <img src="/img/{{ im.file }}">
      <div><span class="tag">{{ im.image_role }}</span> {% if im.is_primary %}<span class="tag g">رئيسية ★</span>{% endif %}</div>
      <form method="post" action="/studio/image/manage" style="margin-top:4px;display:flex;gap:4px">
        <input type="hidden" name="image_id" value="{{ im.image_id }}"><input type="hidden" name="variant_id" value="{{ m.id }}">
        <button name="act" value="set_primary">رئيسية</button>
        <button class="sec" name="act" value="{{ 'restore' if im.disabled else 'disable' }}">{{ 'استعادة' if im.disabled else 'تعطيل' }}</button>
      </form>
    </div>
    {% endfor %}
  </div>
</div>
{% endblock %}"""
