"""Export profiles (visual identity for PDF export): defaults, presets, fonts, paper textures, storage."""
import os, json, random, io, math
from PIL import Image, ImageDraw
import db, assets

FONT_DIR = os.path.join(db.DATA, "fonts")
PROFILE_DIR = os.path.join(db.DATA, "profiles")
os.makedirs(FONT_DIR, exist_ok=True)
os.makedirs(PROFILE_DIR, exist_ok=True)

DEFAULTS = dict(
    name="الافتراضي", lang="en", font_en="auto", font_ar="auto",
    c_primary="#1F3864", c_accent="#E8590C", c_text="#1D2433", c_zebra="#F2F4F8", c_th_text="#FFFFFF",
    paper="A4", orient="portrait", paper_color="#FFFFFF", texture="none", margin="normal",
    header="band", logo_pos="left", logo_size="m", logo="", logo_plate="1",
    company="", contact="", show_supplier="0", show_date="1", show_page="1", show_desc="1",
    wm_type="none", wm_text="", wm_image="", wm_opacity="0.15", wm_angle="45",
    cover="1", cover_title="", cover_sub="", cover_style="block", toc="1", dividers="1",
)

# Preserve the richer identity controls from the local Studio while keeping
# Cloud asset references and font persistence from this module.
DEFAULTS.update(dict(
    font_size_base="9.5", c_heading="#1F3864", bg_image="", bg_opacity="0.12",
    bg_repeat="repeat", bg_scope="all", border_style="none", decor_style="none",
    bleed_mm="0", social_links="", table_style="striped",
))

NUMERIC_LIMITS = {
    "font_size_base": (6.0, 24.0, "حجم الخط الأساسي"),
    "bg_opacity": (0.0, 1.0, "شفافية الخلفية"),
    "wm_opacity": (0.0, 1.0, "شفافية العلامة المائية"),
    "wm_angle": (-360.0, 360.0, "زاوية العلامة المائية"),
    "bleed_mm": (0.0, 20.0, "هوامش الطباعة"),
}


def validate_settings(data):
    """Validate numeric profile controls before they reach the PDF engine."""
    out = dict(data)
    for key, (low, high, label) in NUMERIC_LIMITS.items():
        raw = out.get(key, DEFAULTS[key])
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{label}: أدخل رقمًا بين {low:g} و{high:g}.")
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{label}: أدخل رقمًا بين {low:g} و{high:g}.")
        out[key] = format(value, "g")
    return out

PAPERS = ["A4", "A3", "A5", "Letter", "Legal"]
PAPER_COLORS = {"أبيض": "#FFFFFF", "عاجي": "#FBF7EC", "رمادي فاتح": "#F3F4F6", "أزرق باهت": "#EEF3FA", "كرافت": "#E9DCC3"}
PAPER_COLORS.update({"أبيض ناصع": "#FFFFFF", "عاجي دافئ": "#FBF7EC", "أزرق هندسي": "#EEF3FA"})
TEXTURES = {"none": "أملس دون نسيج", "grain": "حبيبات (ورق مطفي)", "linen": "نسيج كتاني", "grid_tech": "شبكة هندسية خفيفة"}
PRESETS = {
    "أزرق رسمي": dict(c_primary="#1F3864", c_accent="#E8590C", c_zebra="#F2F4F8"),
    "أخضر صناعي": dict(c_primary="#1B5E3B", c_accent="#F2A900", c_zebra="#EEF5F0"),
    "رمادي أنيق": dict(c_primary="#2D3748", c_accent="#C05621", c_zebra="#F1F2F4"),
    "برتقالي ديناميكي": dict(c_primary="#C2410C", c_accent="#1F3864", c_zebra="#FBF1EA"),
    "أسود وذهبي": dict(c_primary="#111111", c_accent="#B8860B", c_zebra="#F4F1E8"),
}
PRESETS.update({
    "أزرق مؤسسي": dict(c_primary="#1F3864", c_accent="#E8590C", c_heading="#1F3864", c_zebra="#F2F4F8"),
    "أخضر صناعي": dict(c_primary="#1B5E3B", c_accent="#D97706", c_heading="#1B5E3B", c_zebra="#EEF5F0"),
    "رمادي هندسي": dict(c_primary="#2D3748", c_accent="#C05621", c_heading="#1A202C", c_zebra="#F1F2F4"),
})

# ---------------------------------------------------------------- fonts
_FAMILIES = {
    "Arial": [("arial.ttf", "arialbd.ttf"), ("Arial.ttf", "Arial Bold.ttf")],
    "Tahoma": [("tahoma.ttf", "tahomabd.ttf")],
    "Segoe UI": [("segoeui.ttf", "segoeuib.ttf")],
    "Calibri": [("calibri.ttf", "calibrib.ttf")],
    "Times New Roman": [("times.ttf", "timesbd.ttf"), ("Times New Roman.ttf", "Times New Roman Bold.ttf")],
    "Verdana": [("verdana.ttf", "verdanab.ttf")],
    "Georgia": [("georgia.ttf", "georgiab.ttf")],
    "Trebuchet MS": [("trebuc.ttf", "trebucbd.ttf")],
    "DejaVu Sans": [("DejaVuSans.ttf", "DejaVuSans-Bold.ttf")],
    "DejaVu Serif": [("DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf")],
}
_FAMILIES.update({
    "Cairo": [("Cairo-Regular.ttf", "Cairo-Bold.ttf"), ("Cairo[slnt,wght].ttf", "Cairo[slnt,wght].ttf")],
    "Tajawal": [("Tajawal-Regular.ttf", "Tajawal-Bold.ttf")],
    "IBM Plex Sans Arabic": [("IBMPlexSansArabic-Regular.ttf", "IBMPlexSansArabic-Bold.ttf")],
    "Liberation Sans": [("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf")],
})
_DIRS = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts"), "C:/Windows/Fonts", "/Library/Fonts", "/System/Library/Fonts/Supplemental", "/usr/share/fonts/truetype/dejavu",
         "/usr/share/fonts/truetype/liberation", "/usr/share/fonts/truetype/cairo", "/usr/share/fonts/truetype/tajawal", "/usr/share/fonts/truetype/ibm-plex"]
_arabic_cache = {}


def _has_arabic(path):
    if path in _arabic_cache:
        return _arabic_cache[path]
    ok = False
    try:
        from reportlab.pdfbase.ttfonts import TTFontFile
        ok = 0x0645 in TTFontFile(path).charToGlyph
    except Exception:
        ok = False
    _arabic_cache[path] = ok
    return ok


def fonts():
    """label -> dict(regular, bold, arabic). System fonts that exist + uploaded fonts."""
    try:
        assets.materialize_fonts(FONT_DIR)          # fonts uploaded earlier live in the database
    except Exception:
        pass
    out = {}
    for label, pairs in _FAMILIES.items():
        for d in _DIRS:
            hit = next(((os.path.join(d, r), os.path.join(d, b)) for r, b in pairs if os.path.exists(os.path.join(d, r))), None)
            if hit:
                reg, bold = hit
                out[label] = dict(regular=reg, bold=bold if os.path.exists(bold) else reg, arabic=_has_arabic(reg))
                break
    for fn in sorted(os.listdir(FONT_DIR)):
        if fn.endswith("__r.ttf"):
            name = fn[:-7]; reg = os.path.join(FONT_DIR, fn); b = os.path.join(FONT_DIR, name + "__b.ttf")
            out[f"{name} (مرفوع)"] = dict(regular=reg, bold=b if os.path.exists(b) else reg, arabic=_has_arabic(reg))
    return out


def save_font(name, reg_file, bold_file):
    name = "".join(ch for ch in name if ch.isalnum() or ch in "-_ ").strip().replace(" ", "_") or "font"
    for suffix, up in (("__r.ttf", reg_file), ("__b.ttf", bold_file if (bold_file and bold_file.filename) else None)):
        if up is None:
            continue
        data = up.read()
        with open(os.path.join(FONT_DIR, name + suffix), "wb") as f:
            f.write(data)
        assets.put("font/" + name + suffix, name + suffix, data, "font/ttf")     # survives restarts (Cloud Run has no persistent disk)


# ---------------------------------------------------------------- textures
_tiles = {}


def texture_tile(kind):
    """Small transparent tile for a subtle paper texture (screen/PDF look, not for physical printing)."""
    if kind in _tiles:
        return _tiles[kind]
    rnd = random.Random(7); n = 192
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0)); px = im.load()
    if kind == "grain":
        for x in range(n):
            for y in range(n):
                v = rnd.random()
                if v < 0.35:
                    px[x, y] = (0, 0, 0, rnd.randint(4, 14))
                elif v < 0.5:
                    px[x, y] = (255, 255, 255, rnd.randint(30, 70))
    elif kind == "linen":
        for y in range(0, n, 3):
            a = rnd.randint(6, 16)
            for x in range(n):
                px[x, y] = (0, 0, 0, a)
        for x in range(0, n, 3):
            a = rnd.randint(6, 16)
            for y in range(n):
                px[x, y] = (0, 0, 0, max(px[x, y][3], a))
    elif kind == "grid_tech":
        dr = ImageDraw.Draw(im)
        for step in range(0, n, 24):
            dr.line([(step, 0), (step, n)], fill=(31, 56, 100, 14), width=1)
            dr.line([(0, step), (n, step)], fill=(31, 56, 100, 14), width=1)
    else:
        return None
    _tiles[kind] = im
    return im


# ---------------------------------------------------------------- storage
def load_all(c):
    return [dict(id=r["id"], name=r["name"]) for r in c.execute("SELECT id,name FROM profiles ORDER BY id")]


def load(c, pid=None, resolve=True):
    """resolve=True turns asset references (logo, watermark image) into local file paths for the PDF engine;
    resolve=False keeps the stored references (use it when the profile will be saved again)."""
    if not pid:
        pid = db.get_setting(c, "active_profile")
    r = c.execute("SELECT * FROM profiles WHERE id=?", (pid,)).fetchone() if pid else None
    if not r:
        r = c.execute("SELECT * FROM profiles ORDER BY id LIMIT 1").fetchone()
    if not r:
        return dict(DEFAULTS, id=0)
    d = dict(DEFAULTS); d.update(json.loads(r["data"] or "{}")); d["id"] = r["id"]; d["name"] = r["name"]
    if resolve:
        for k in ("logo", "wm_image", "bg_image"):
            if str(d.get(k) or "").startswith("asset:"):
                d[k] = assets.path(d[k][6:]) or ""
    return d


def _record_version(c, pid, name, data_json):
    row = c.execute("SELECT COALESCE(MAX(version_num),0)+1 n FROM profile_versions WHERE profile_id=?", (pid,)).fetchone()
    c.execute("INSERT INTO profile_versions(profile_id,version_num,name,data) VALUES(?,?,?,?)",
              (pid, row["n"], name, data_json))


def save(c, pid, data):
    d = {k: v for k, v in data.items() if k in DEFAULTS and k != "name"}
    d = validate_settings(d)
    js = json.dumps(d, ensure_ascii=False)
    nm = data.get("name") or "دون اسم"
    c.execute("UPDATE profiles SET name=?, data=? WHERE id=?", (nm, js, pid))
    _record_version(c, pid, nm, js)


def create(c, name, base=None):
    d = {k: v for k, v in (base or DEFAULTS).items() if k in DEFAULTS and k != "name"}
    d = validate_settings(d)
    js = json.dumps(d, ensure_ascii=False)
    pid = c.execute("INSERT INTO profiles(name,data) VALUES(?,?)", (name, js)).lastrowid
    _record_version(c, pid, name, js)
    return pid


def ensure_default(c):
    """First run (or upgrade from the old settings page): create a default profile from old settings."""
    if c.execute("SELECT 1 FROM profiles LIMIT 1").fetchone():
        return
    base = dict(DEFAULTS)
    old = {k: db.get_setting(c, k) for k in ("company", "contact", "color", "watermark", "logo", "show_supplier")}
    base["company"], base["contact"] = old["company"], old["contact"]
    if old["color"]: base["c_primary"] = old["color"]
    if old["watermark"]: base.update(wm_type="text", wm_text=old["watermark"])
    base["logo"] = old["logo"] or ""
    base["show_supplier"] = "1" if old["show_supplier"] == "1" else "0"
    pid = create(c, "الافتراضي", base)
    db.set_setting(c, "active_profile", str(pid))
    c.commit()
