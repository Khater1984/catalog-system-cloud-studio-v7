"""Navigation model + Home dashboard.

Five places; the pages of a place are its tabs. A page is matched to its place by URL prefix (longest prefix wins),
so detail pages (/model/12, /catalog/3, /product/5 ...) keep the right place and tab highlighted without extra work.
To add a page: add one tab line below. No template change needed."""
from flask import Blueprint, request, render_template
import db, catalogs, product_studio
from shell import ICONS

bp = Blueprint("ui", __name__)

# (label, url, prefixes that belong to this tab, flags)
GROUPS = [
    dict(key="home", label="الرئيسية", icon="home", url="/", tabs=[]),
    dict(key="catalogs", label="الكتالوجات", icon="catalogs", url="/catalogs", tabs=[
        ("الكتالوجات", "/catalogs", ["/catalogs", "/catalog/", "/upload", "/merge"], {}),
        ("المراجعة", "/review", ["/review"], {"badge": "unresolved"}),
        ("نواقص البيانات", "/gaps", ["/gaps"], {}),
    ]),
    dict(key="products", label="المنتجات", icon="products", url="/search", tabs=[
        ("بحث وعرض", "/search", ["/search", "/model/"], {}),
        ("المنتجات", "/products", ["/products", "/product/", "/studio/product", "/studio/variant", "/studio/spec"], {}),
        ("الطرازات", "/models", ["/models"], {}),
        ("مقارنة", "/compare", ["/compare"], {}),
        ("الفيديوهات", "/videos", ["/videos", "/video/"], {}),
        ("ورشة الصور", "/studio/unlinked-images", ["/studio"], {"badge": "images"}),
        ("شجرة المنتجات", "/tree", ["/tree"], {}),
    ]),
    dict(key="export", label="التصدير", icon="export", url="/export-center/", tabs=[
        ("مركز التصدير", "/export-center/", ["/export-center", "/export/", "/datasheet", "/catalog.pdf", "/master.pdf", "/preview"], {}),
        ("الهوية والقوالب", "/export-settings", ["/export-settings"], {}),
        ("أدوات متقدمة", "/pro/", ["/pro"], {}),
        ("جوجل شيت", "/sync", ["/sync"], {}),
        ("تنزيل Excel", "/export.xlsx", [], {"download": True}),
    ]),
    dict(key="admin", label="الإدارة", icon="admin", url="/dictionary", tabs=[
        ("قاموس المواصفات", "/dictionary", ["/dictionary"], {}),
        ("القواعد", "/rules", ["/rules"], {}),
        ("سجل النشاط", "/activity", ["/activity"], {}),
        ("المحذوفات", "/trash", ["/trash"], {}),
        ("المستخدمون", "/users", ["/users"], {"owner": True}),
        ("نسخ احتياطي", "/backup", ["/backup"], {"owner": True}),
        ("حسابي", "/account", ["/account"], {}),
    ]),
]


def _match(path, prefixes):
    """Length of the longest matching prefix (0 = no match). '/x' matches '/x' and '/x/...', never '/xy'."""
    best = 0
    for p in prefixes:
        base = p.rstrip("/")
        if path == base or path == p or path.startswith(base + "/"):
            best = max(best, len(base))
    return best


def locate(path):
    """(group, tab) for a URL path; (home, None) for '/'; (None, None) when it belongs nowhere."""
    if path == "/":
        return GROUPS[0], None
    best, g_best, t_best = 0, None, None
    for g in GROUPS[1:]:
        for t in g["tabs"]:
            m = _match(path, t[2])
            if m > best:
                best, g_best, t_best = m, g, t
    return g_best, t_best


def build_nav(path, counts, owner):
    g_cur, t_cur = locate(path)
    groups = []
    for g in GROUPS:
        tabs = []
        for label, url, _pref, fl in g["tabs"]:
            if fl.get("owner") and not owner:
                continue
            tabs.append(dict(label=label, url=url, active=(t_cur is not None and url == t_cur[1]),
                             download=bool(fl.get("download")), badge=counts.get(fl.get("badge"), 0) if fl.get("badge") else 0))
        groups.append(dict(key=g["key"], label=g["label"], icon=g["icon"], url=g["url"], tabs=tabs,
                           active=(g_cur is not None and g_cur["key"] == g["key"]),
                           badge=counts.get("unresolved", 0) if g["key"] == "catalogs" else 0))
    cur = next((g for g in groups if g["active"]), None)
    tab = next((t for t in (cur["tabs"] if cur else []) if t["active"]), None)
    return dict(groups=groups, group=cur, tab=tab)


def init_app(app):
    @app.context_processor
    def _nav():
        from flask import g
        user = getattr(g, "user", None)
        try:
            owner = bool(user and user["is_owner"])
        except Exception:
            owner = False
        c = db.conn()
        try:
            unresolved = c.execute("SELECT COUNT(*) n FROM review WHERE resolved=0").fetchone()["n"]
            images = product_studio.review_image_count(c)
        finally:
            c.close()
        local = (user is None) or (user["username"] == "local")
        return dict(nav=build_nav(request.path, dict(unresolved=unresolved, images=images), owner or local), icons=ICONS, gq=request.args.get("q", "") if request.path in ("/search",) else "")
    app.register_blueprint(bp)


def _count(c, sql, *a):
    return c.execute(sql, a).fetchone()[0]


@bp.route("/")
def home():
    c = db.conn()
    try:
        rows = c.execute("""SELECT c.*, (SELECT COUNT(*) FROM models m WHERE m.catalog_id=c.id) models,
                (SELECT COUNT(*) FROM models m WHERE m.catalog_id=c.id AND m.status='approved') approved
                FROM catalogs c WHERE c.file_sha<>'manual' ORDER BY c.id DESC LIMIT 6""").fetchall()
        cats = catalogs.annotate(rows)
        n_models = _count(c, "SELECT COUNT(*) FROM models WHERE active=1")
        n_approved = _count(c, "SELECT COUNT(*) FROM models WHERE active=1 AND status='approved'")
        n_staging = n_models - n_approved
        n_review = _count(c, "SELECT COUNT(*) FROM review WHERE resolved=0")
        n_nospec = _count(c, "SELECT COUNT(*) FROM models m WHERE m.active=1 AND NOT EXISTS (SELECT 1 FROM specs s WHERE s.model_id=m.id)")
        n_cats = _count(c, "SELECT COUNT(*) FROM catalogs WHERE file_sha<>'manual'")
        n_img = product_studio.review_image_count(c)
    finally:
        c.close()
    decide = sum(1 for x in cats if x["state"] == "needs_decision")
    busy = sum(1 for x in cats if x["state"] in ("processing", "checking"))
    work = [dict(n=n, text=t, url=u, tone=tone) for n, t, u, tone in (
        (decide, "كتالوج ينتظر قرارك (هل هو مكرر؟)", "/catalogs", "bad"),
        (busy, "كتالوج قيد الاستخراج الآن", "/catalogs", "warn"),
        (n_review, "بند مراجعة غير محلول", "/review", "warn"),
        (n_img, "صورة تنتظر ربطها بطراز", "/studio/unlinked-images", "warn"),
        (n_staging, "طراز لم يُعتمد بعد", "/models?status=staging", "muted"),
        (n_nospec, "طراز بلا مواصفات", "/gaps", "muted")) if n]
    return render_template("home", cats=cats, work=work, n_models=n_models, n_approved=n_approved, n_cats=n_cats, busy=bool(busy))
