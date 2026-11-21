"""Spec dictionary: unify attribute names across suppliers (and give Arabic labels + display order)."""
import re
from flask import Blueprint, request, redirect, render_template, flash
import db

bp = Blueprint("dictionary", __name__)

# canonical name -> (Arabic label, display order, [raw names (lower-case) that mean the same])
BUILTIN = {
    "Model": ("الطراز", 1, []),
    "Motor power": ("قدرة المحرك", 10, ["motor power", "potenza motore", "rated power", "قدرة المحرك"]),
    "Power supply": ("مصدر الطاقة", 11, ["power supply", "alimentazione", "voltage", "tensione", "الجهد"]),
    "Frequency": ("التردد", 12, ["frequency", "frequenza", "التردد"]),
    "Motor speed": ("سرعة المحرك", 13, ["motor speed", "velocità motore", "velocita motore"]),
    "Cutting range": ("مدى القطع", 20, ["cutting range", "capacità di taglio", "cutting capacity"]),
    "Cutting rate": ("معدل القطع", 21, ["cutting rate"]),
    "Bending capacity": ("سعة الثني", 22, ["bending capacity", "bending range", "capacità di piegatura"]),
    "Bending speed": ("سرعة الثني", 23, ["bending speed"]),
    "Bending rate": ("معدل الثني", 24, ["bending rate"]),
    "Max. bending angle": ("أقصى زاوية ثني", 25, ["max. bending angle"]),
    "Bending disc size": ("حجم قرص الثني", 26, ["bending disc size"]),
    "Blade dimensions": ("أبعاد الشفرة", 27, ["blade dimensions"]),
    "Control type": ("نوع التحكم", 28, ["control type"]),
    "Electrical brand": ("العلامة التجارية للمكونات الكهربائية", 30, ["electrical brand"]),
    "Recommended grease": ("الشحم الموصى به", 40, ["recommended grease"]),
    "Grease amount": ("كمية الشحم", 41, ["grease amount"]),
    "Spare parts": ("قطع الغيار", 42, ["spare parts"]),
    "Emergency stop": ("إيقاف الطوارئ", 43, ["emergency stop"]),
    "Net weight": ("الوزن الصافي", 50, ["n.w", "net weight", "peso netto", "الوزن الصافي"]),
    "Gross weight": ("الوزن الإجمالي", 51, ["g.w", "gross weight", "peso lordo", "الوزن الإجمالي"]),
    "Weight": ("الوزن", 52, ["weight", "peso"]),
    "Dimensions": ("الأبعاد", 60, ["dimensions (l × w × h)", "dimensions(l×w×h)", "dimensions", "dimensioni", "overall dimensions"]),
    "Packing size": ("أبعاد التغليف", 61, ["packing size (l × w × h)", "packing size", "imballo"]),
}


def key(raw):
    return re.sub(r"\s+", " ", (raw or "").strip().lower())


def load_map(c):
    return {r["raw"]: dict(canonical=r["canonical"], ar=r["ar"], sort=r["sort"]) for r in c.execute("SELECT * FROM attr_map")}


def apply(specs, amap):
    """specs: list of dict rows -> same list with attr (canonical), attr_raw, attr_ar, ordered by dictionary order."""
    out = []
    for i, s in enumerate(specs):
        d = dict(s); m = amap.get(key(d["attr"]))
        d["attr_raw"] = d["attr"]
        d["attr_ar"] = (m or {}).get("ar", "")
        if m and m["canonical"]: d["attr"] = m["canonical"]
        d["_o"] = ((m or {}).get("sort", 999), i)
        out.append(d)
    out.sort(key=lambda d: d["_o"])
    return out


@bp.route("/dictionary")
def page():
    c = db.conn(); amap = load_map(c)
    rows = c.execute("""SELECT lower(trim(attr)) k, MIN(attr) shown, COUNT(*) n, COUNT(DISTINCT model_id) models,
                        MIN(COALESCE(NULLIF(company_value,''), supplier_value)) sample FROM specs GROUP BY lower(trim(attr))
                        ORDER BY n DESC""").fetchall()
    canon = sorted({v["canonical"] for v in amap.values() if v["canonical"]} | set(BUILTIN))
    data = [dict(k=key(r["k"]), shown=r["shown"], n=r["n"], models=r["models"], sample=r["sample"], m=amap.get(key(r["k"]), {})) for r in rows]
    c.close()
    return render_template("dictionary", rows=data, canon=canon, unmapped=sum(1 for r in data if not r["m"]))


@bp.route("/dictionary/save", methods=["POST"])
def save():
    f = request.form; c = db.conn(); n = 0
    for i, k in enumerate(f.getlist("k")):
        cn = f.getlist("canonical")[i].strip(); ar = f.getlist("ar")[i].strip()
        srt = f.getlist("sort")[i].strip()
        if cn:
            c.execute("INSERT INTO attr_map(raw,canonical,ar,sort,suggested) VALUES(?,?,?,?,0) ON CONFLICT(raw) DO UPDATE SET canonical=excluded.canonical, ar=excluded.ar, sort=excluded.sort, suggested=0",
                      (k, cn, ar, int(srt) if srt.isdigit() else 999)); n += 1
        else:
            c.execute("DELETE FROM attr_map WHERE raw=?", (k,))
    c.commit(); c.close(); flash(f"حُفظ قاموس المواصفات، ويضم {n} خاصية.")
    return redirect("/dictionary")


@bp.route("/dictionary/suggest", methods=["POST"])
def suggest():
    c = db.conn(); made = 0
    existing = {r["raw"] for r in c.execute("SELECT raw FROM attr_map")}
    present = {key(r["attr"]) for r in c.execute("SELECT DISTINCT attr FROM specs")}
    for canon, (ar, srt, raws) in BUILTIN.items():
        for raw in raws:
            k = key(raw)
            if k in present and k not in existing:
                c.execute("INSERT INTO attr_map(raw,canonical,ar,sort,suggested) VALUES(?,?,?,?,1)", (k, canon, ar, srt)); made += 1
    c.commit(); c.close()
    flash(f"أُنشئ {made} اقتراحًا استنادًا إلى القاموس الجاهز. راجع الأسماء وعدّل ما يلزم، ثم احفظ التغييرات.")
    return redirect("/dictionary")
