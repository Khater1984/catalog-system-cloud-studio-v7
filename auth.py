"""Simple multi-user access: one account per employee, everybody has the same rights; the user name is recorded on every operation.
Only the owner (is_owner=1) can manage users and download backups.
AUTH_DISABLED=1 keeps the old single-PC behaviour (no login) - never use it on the internet."""
import os, datetime, re
from functools import wraps
from urllib.parse import urlparse, quote
from flask import Blueprint, request, redirect, session, render_template, flash, g, abort
from werkzeug.security import generate_password_hash, check_password_hash
import db

bp = Blueprint("auth", __name__)
AUTH_DISABLED = os.environ.get("AUTH_DISABLED") == "1"
PROD = os.environ.get("APP_ENV", "").lower() == "production"
MAX_FAILS, LOCK_MINUTES, MIN_PASSWORD = 5, 10, 8
PUBLIC = {
    "auth.login", "healthz", "static",
    # Public customer portal; each route performs its own published-model checks.
    "enterprise_pro.public_model_portal", "enterprise_pro.public_image", "enterprise_pro.qr_badge",
}
LOCAL_USER = {"id": 0, "username": "local", "display_name": "محلي", "is_owner": 1, "active": 1}


def _now():
    return datetime.datetime.utcnow()


def _fmt(d):
    return d.strftime("%Y-%m-%d %H:%M:%S")


def init_app(app):
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=PROD,
                      PERMANENT_SESSION_LIFETIME=datetime.timedelta(hours=12), MAX_CONTENT_LENGTH=int(os.environ.get("MAX_UPLOAD_MB", "32" if PROD else "200")) * 1024 * 1024)
    app.before_request(guard)
    app.after_request(security_headers)
    app.teardown_request(lambda e: db.set_actor("system"))
    app.register_blueprint(bp)

    @app.route("/healthz")
    def healthz():
        c = db.conn()
        try:
            c.execute("SELECT 1").fetchone()
        finally:
            c.close()
        return "ok"

    @app.context_processor
    def user_ctx():
        return dict(cur_user=getattr(g, "user", None))


def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if PROD:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return resp


def _same_origin():
    for h in ("Origin", "Referer"):
        v = request.headers.get(h)
        if v:
            return urlparse(v).netloc == request.host
    return True


def guard():
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and not _same_origin():
        abort(403)
    if AUTH_DISABLED:
        g.user = LOCAL_USER; db.set_actor("local"); return
    if (request.endpoint or "") in PUBLIC:
        return
    user = None
    uid = session.get("uid")
    if uid:
        c = db.conn()
        try:
            user = c.execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone()
        finally:
            c.close()
        if not user:
            session.clear()
    if not user:
        return redirect("/login?next=" + quote(request.full_path if request.method == "GET" else "/"))
    g.user = user
    db.set_actor(user["username"])


def owner_required(f):
    @wraps(f)
    def w(*a, **k):
        if not (getattr(g, "user", None) and g.user["is_owner"]):
            abort(403)
        return f(*a, **k)
    return w


def valid_username(u):
    return bool(re.fullmatch(r"[a-z0-9._-]{2,40}", u or ""))


def create_user(c, username, password, display_name="", is_owner=False):
    username = (username or "").strip().lower()
    if not valid_username(username):
        raise ValueError("يجب أن يتكوّن اسم المستخدم من 2 إلى 40 حرفًا، وأن يقتصر على الأحرف الإنجليزية الصغيرة والأرقام والرموز . _ -.")
    if len(password or "") < MIN_PASSWORD:
        raise ValueError(f"يجب ألا يقل طول كلمة المرور عن {MIN_PASSWORD} أحرف.")
    if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        raise ValueError("اسم المستخدم مستخدم بالفعل.")
    uid = c.execute("INSERT INTO users(username,display_name,password_hash,is_owner) VALUES(?,?,?,?)",
                    (username, display_name.strip(), generate_password_hash(password), 1 if is_owner else 0)).lastrowid
    db.log(c, "user", uid, "create", f"{username}{' (owner)' if is_owner else ''}")
    return uid


def bootstrap():
    """Create the first owner from OWNER_USERNAME / OWNER_PASSWORD when there are no users yet."""
    u, p = os.environ.get("OWNER_USERNAME", ""), os.environ.get("OWNER_PASSWORD", "")
    if not (u and p):
        return
    c = db.conn()
    try:
        if not c.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            create_user(c, u, p, os.environ.get("OWNER_DISPLAY_NAME", u), True); c.commit()
    finally:
        c.close()


def _safe_next(n):
    return n if n and n.startswith("/") and not n.startswith("//") else "/"


@bp.route("/login", methods=["GET", "POST"])
def login():
    if AUTH_DISABLED:
        return redirect("/")
    if request.method == "POST":
        username, pw = request.form.get("username", "").strip().lower(), request.form.get("password", "")
        c = db.conn()
        try:
            u = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if u and u["locked_until"] and u["locked_until"] > _fmt(_now()):
                flash("أُغلق الحساب مؤقتًا بسبب تكرار محاولات تسجيل الدخول غير الصحيحة. يُرجى المحاولة لاحقًا.")
            elif u and u["active"] and check_password_hash(u["password_hash"], pw):
                c.execute("UPDATE users SET failed_count=0, locked_until='', last_login=? WHERE id=?", (_fmt(_now()), u["id"]))
                db.set_actor(u["username"]); db.log(c, "user", u["id"], "login", ""); c.commit()
                session.clear(); session.permanent = True; session["uid"] = u["id"]
                return redirect(_safe_next(request.args.get("next")))
            else:
                if u:
                    n = (u["failed_count"] or 0) + 1
                    lock = _fmt(_now() + datetime.timedelta(minutes=LOCK_MINUTES)) if n >= MAX_FAILS else ""
                    c.execute("UPDATE users SET failed_count=?, locked_until=? WHERE id=?", (0 if lock else n, lock, u["id"])); c.commit()
                flash("اسم المستخدم أو كلمة المرور غير صحيح.")
        finally:
            c.close()
    c = db.conn()
    try:
        empty = not c.execute("SELECT 1 FROM users LIMIT 1").fetchone()
    finally:
        c.close()
    return render_template("login", empty=empty)


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect("/login")


@bp.route("/account", methods=["GET", "POST"])
def account():
    if AUTH_DISABLED:
        return redirect("/")
    if request.method == "POST":
        f = request.form
        if not check_password_hash(g.user["password_hash"], f.get("old", "")):
            flash("كلمة المرور الحالية غير صحيحة.")
        elif f.get("new", "") != f.get("new2", ""):
            flash("كلمتا المرور الجديدتان غير متطابقتين.")
        elif len(f.get("new", "")) < MIN_PASSWORD:
            flash(f"يجب ألا يقل طول كلمة المرور عن {MIN_PASSWORD} أحرف.")
        else:
            c = db.conn()
            try:
                c.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(f["new"]), g.user["id"]))
                db.log(c, "user", g.user["id"], "password_change", "self"); c.commit()
            finally:
                c.close()
            flash("تم تغيير كلمة المرور بنجاح.")
        return redirect("/account")
    return render_template("account")


@bp.route("/users", methods=["GET", "POST"])
@owner_required
def users():
    c = db.conn()
    try:
        if request.method == "POST":
            f = request.form
            try:
                create_user(c, f.get("username", ""), f.get("password", ""), f.get("display_name", ""), bool(f.get("is_owner")))
                c.commit(); flash("أُضيف المستخدم بنجاح.")
            except ValueError as e:
                c.rollback(); flash(str(e))
            return redirect("/users")
        rows = c.execute("SELECT * FROM users ORDER BY id").fetchall()
    finally:
        c.close()
    return render_template("users", rows=rows)


@bp.route("/users/<int:uid>/password", methods=["POST"])
@owner_required
def user_password(uid):
    pw = request.form.get("password", "")
    if len(pw) < MIN_PASSWORD:
        flash(f"يجب ألا يقل طول كلمة المرور عن {MIN_PASSWORD} أحرف.")
    else:
        c = db.conn()
        try:
            c.execute("UPDATE users SET password_hash=?, failed_count=0, locked_until='' WHERE id=?", (generate_password_hash(pw), uid))
            db.log(c, "user", uid, "password_reset", "by owner"); c.commit()
        finally:
            c.close()
        flash("تم تغيير كلمة المرور بنجاح.")
    return redirect("/users")


@bp.route("/users/<int:uid>/toggle", methods=["POST"])
@owner_required
def user_toggle(uid):
    c = db.conn()
    try:
        u = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not u:
            abort(404)
        if u["active"]:
            others = c.execute("SELECT COUNT(*) n FROM users WHERE active=1 AND is_owner=1 AND id<>?", (uid,)).fetchone()["n"]
            if uid == g.user["id"] or (u["is_owner"] and not others):
                flash("لا يمكن تعطيل حسابك أو حساب المالك الأخير."); return redirect("/users")
        c.execute("UPDATE users SET active=? WHERE id=?", (0 if u["active"] else 1, uid))
        db.log(c, "user", uid, "disable" if u["active"] else "enable", u["username"]); c.commit()
    finally:
        c.close()
    return redirect("/users")


def back(default="/"):
    """Redirect target: the referring page if it is on this site, otherwise `default` (no open redirects)."""
    ref = request.referrer
    if ref and urlparse(ref).netloc == request.host:
        return ref
    return default
