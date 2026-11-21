"""Owner tools (backup, integrity) and the activity log."""
import datetime
from flask import Blueprint, request, redirect, render_template, flash, Response
import db, dataio, integrity, extractor
from auth import owner_required

bp = Blueprint("admin", __name__)


@bp.route("/activity")
def activity():
    who, ent = request.args.get("user", ""), request.args.get("entity", "")
    sql, a = "SELECT * FROM audit WHERE 1=1", []
    if who: sql += " AND username=?"; a.append(who)
    if ent: sql += " AND entity=?"; a.append(ent)
    c = db.conn()
    try:
        rows = c.execute(sql + " ORDER BY id DESC LIMIT 300", a).fetchall()
        users = [r["username"] for r in c.execute("SELECT DISTINCT username FROM audit WHERE username<>'' ORDER BY username").fetchall()]
    finally:
        c.close()
    return render_template("activity", rows=rows, users=users, who=who, ent=ent)


@bp.route("/backup")
@owner_required
def backup_page():
    c = db.conn()
    try:
        issues = integrity.run(c)
        counts = {t: c.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"] for t in ("catalogs", "models", "specs", "images", "users")}
    finally:
        c.close()
    return render_template("backup", issues=issues, counts=counts, pg=db.IS_PG)


@bp.route("/backup/download")
@owner_required
def backup_download():
    c = db.conn()
    try:
        data = dataio.dump(c)
        db.log(c, "system", 0, "backup_download", f"{len(data)} bytes"); c.commit()
    finally:
        c.close()
    name = "catalog_backup_" + datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S") + ".zip"
    return Response(data, mimetype="application/zip", headers={"Content-Disposition": f"attachment; filename={name}"})


@bp.route("/backup/fix-family", methods=["POST"])
@owner_required
def fix_family():
    c = db.conn()
    try:
        n = integrity.fix_family(c); c.commit()
    finally:
        c.close()
    flash(f"تم تحديث تصنيف {n} طرازًا.")
    return redirect("/backup")


@bp.route("/trash")
def trash():
    c = db.conn()
    try:
        rows = c.execute("SELECT id, ts, username, entity, entity_id, label, restored FROM deleted_records ORDER BY id DESC LIMIT 100").fetchall()
    finally:
        c.close()
    return render_template("trash", rows=rows)


@bp.route("/trash/<int:rid>/restore", methods=["POST"])
def trash_restore(rid):
    c = db.conn()
    try:
        msg = extractor.restore_catalog(c, rid)
    finally:
        c.close()
    flash(msg or "تمت استعادة الكتالوج بجميع طرازاته ومواصفاته.")
    return redirect("/trash")
