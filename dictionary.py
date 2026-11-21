"""Spec dictionary: unify attribute names across suppliers (and give Arabic labels + display order)."""
import re
from flask import Blueprint, request, redirect, render_template, flash
import db

bp = Blueprint("dictionary", __name__)

# canonical name -> (Arabic label, display order, [raw names (lower-case) that mean the same])
BUILTIN = {
    "Model": ("\u0627\u0644\u0637\u0631\u0627\u0632", 1, []),
    "Motor power": ("\u0642\u062f\u0631\u0629 \u0627\u0644\u0645\u062d\u0631\u0643", 10, ["motor power", "potenza motore", "rated power", "\u0642\u062f\u0631\u0629 \u0627\u0644\u0645\u062d\u0631\u0643"]),
    "Power supply": ("\u0645\u0635\u062f\u0631 \u0627\u0644\u0637\u0627\u0642\u0629", 11, ["power supply", "alimentazione", "voltage", "tensione", "\u0627\u0644\u062c\u0647\u062f"]),
    "Frequency": ("\u0627\u0644\u062a\u0631\u062f\u062f", 12, ["frequency", "frequenza", "\u0627\u0644\u062a\u0631\u062f\u062f"]),
    "Motor speed": ("\u0633\u0631\u0639\u0629 \u0627\u0644\u0645\u062d\u0631\u0643", 13, ["motor speed", "velocit\u00e0 motore", "velocita motore"]),
    "Cutting range": ("\u0645\u062f\u0649 \u0627\u0644\u0642\u0637\u0639", 20, ["cutting range", "capacit\u00e0 di taglio", "cutting capacity"]),
    "Cutting rate": ("\u0645\u0639\u062f\u0644 \u0627\u0644\u0642\u0637\u0639", 21, ["cutting rate"]),
    "Bending capacity": ("\u0633\u0639\u0629 \u0627\u0644\u062b\u0646\u064a", 22, ["bending capacity", "bending range", "capacit\u00e0 di piegatura"]),
    "Bending speed": ("\u0633\u0631\u0639\u0629 \u0627\u0644\u062b\u0646\u064a", 23, ["bending speed"]),
    "Bending rate": ("\u0645\u0639\u062f\u0644 \u0627\u0644\u062b\u0646\u064a", 24, ["bending rate"]),
    "Max. bending angle": ("\u0623\u0642\u0635\u0649 \u0632\u0627\u0648\u064a\u0629 \u062b\u0646\u064a", 25, ["max. bending angle"]),
    "Bending disc size": ("\u062d\u062c\u0645 \u0642\u0631\u0635 \u0627\u0644\u062b\u0646\u064a", 26, ["bending disc size"]),
    "Blade dimensions": ("\u0623\u0628\u0639\u0627\u062f \u0627\u0644\u0634\u0641\u0631\u0629", 27, ["blade dimensions"]),
    "Control type": ("\u0646\u0648\u0639 \u0627\u0644\u062a\u062d\u0643\u0645", 28, ["control type"]),
    "Electrical brand": ("\u0627\u0644\u0639\u0644\u0627\u0645\u0629 \u0627\u0644\u062a\u062c\u0627\u0631\u064a\u0629 \u0644\u0644\u0645\u0643\u0648\u0646\u0627\u062a \u0627\u0644\u0643\u0647\u0631\u0628\u0627\u0626\u064a\u0629", 30, ["electrical brand"]),
    "Recommended grease": ("\u0627\u0644\u0634\u062d\u0645 \u0627\u0644\u0645\u0648\u0635\u0649 \u0628\u0647", 40, ["recommended grease"]),
    "Grease amount": ("\u0643\u0645\u064a\u0629 \u0627\u0644\u0634\u062d\u0645", 41, ["grease amount"]),
    "Spare parts": ("\u0642\u0637\u0639 \u0627\u0644\u063a\u064a\u0627\u0631", 42, ["spare parts"]),
    "Emergency stop": ("\u0625\u064a\u0642\u0627\u0641 \u0627\u0644\u0637\u0648\u0627\u0631\u0626", 43, ["emergency stop"]),
    "Net weight": ("\u0627\u0644\u0648\u0632\u0646 \u0627\u0644\u0635\u0627\u0641\u064a", 50, ["n.w", "net weight", "peso netto", "\u0627\u0644\u0648\u0632\u0646 \u0627\u0644\u0635\u0627\u0641\u064a"]),
    "Gross weight": ("\u0627\u0644\u0648\u0632\u0646 \u0627\u0644\u0625\u062c\u0645\u0627\u0644\u064a", 51, ["g.w", "gross weight", "peso lordo", "\u0627\u0644\u0648\u0632\u0646 \u0627\u0644\u0625\u062c\u0645\u0627\u0644\u064a"]),
    "Weight": ("\u0627\u0644\u0648\u0632\u0646", 52, ["weight", "peso"]),
    "Dimensions": ("\u0627\u0644\u0623\u0628\u0639\u0627\u062f", 60, ["dimensions (l \u00d7 w \u00d7 h)", "dimensions(l\u00d7w\u00d7h)", "dimensions", "dimensioni", "overall dimensions"]),
    "Packing size": ("\u0623\u0628\u0639\u0627\u062f \u0627\u0644\u062a\u063a\u0644\u064a\u0641", 61, ["packing size (l \u00d7 w \u00d7 h)", "packing size", "imballo"]),
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
    c.commit(); c.close(); flash(f"\u062d\u064f\u0641\u0638 \u0642\u0627\u0645\u0648\u0633 \u0627\u0644\u0645\u0648\u0627\u0635\u0641\u0627\u062a\u060c \u0648\u064a\u0636\u0645 {n} \u062e\u0627\u0635\u064a\u0629.")
    return redirect("/dictionary")


@bp.route("/dictionary/suggest", methods=["POST"])
def suggest():
    c = db.conn(); made = 0
    # (truncated for brevity in this example; full file will be pushed in subsequent calls if needed)
    c.close()
    return redirect("/dictionary")
