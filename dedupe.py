"""Duplicate / newer-version detection for supplier catalogs (4 levels).

 1. SHA-256 of the file                      -> exact copy            : BLOCK                (done by the upload route, see exact_match)
 2. normalised text content hash             -> same content, new name : CONFIRM (warn)
 3. supplier + version + catalog date        -> same release / older   : CONFIRM (warn)
                                              newer release           : ALLOW, linked to the previous one after the overlap check
 4. products / models / specs similarity     -> after extraction: near-identical copy -> review item (user decides),
                                                updated catalog -> linked as newer version, never called a duplicate
An updated catalog is NOT a duplicate just because most of its models already exist: level 4 only calls a catalog a
near-duplicate when the model sets are (almost) the same AND the specs are (almost) identical.
"""
import re, json, hashlib, unicodedata
import db

TEXT_MIN_CHARS = 300          # below this the PDF is probably scanned: level 2 is skipped
NEAR_DUP_JACCARD = 0.90
NEAR_DUP_SPEC_SAME = 0.97
UPDATE_OVERLAP = 0.30


# ------------------------------------------------------------------ normalisation helpers
def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def norm_supplier(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", (s or "").lower())).strip()


def normalize_text(t):
    t = unicodedata.normalize("NFKC", t or "").lower()
    lines = [ln for ln in t.splitlines() if not re.fullmatch(r"[\W\d_]{0,12}", ln.strip())]   # page numbers, rules, bullets
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def parse_version(s):
    m = re.search(r"\d+(?:\.\d+){0,3}", s or "")
    return tuple(int(x) for x in m.group(0).split(".")) if m else None


def cmp_version(a, b):
    if a is None or b is None:
        return None
    n = max(len(a), len(b)); a2, b2 = a + (0,) * (n - len(a)), b + (0,) * (n - len(b))
    return (a2 > b2) - (a2 < b2)


MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def parse_date(s):
    """-> (y,m,d) with 0 for unknown parts, or None."""
    s = (s or "").strip().lower()
    if not s:
        return None
    m = re.search(r"\b(20\d\d|19\d\d)[-/.](\d{1,2})(?:[-/.](\d{1,2}))?\b", s)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
    m = re.search(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](20\d\d|19\d\d)\b", s)
    if m:
        return (int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+(20\d\d|19\d\d)\b", s)
    if m:
        return (int(m.group(2)), MONTHS[m.group(1)], 0)
    m = re.search(r"\b(20\d\d|19\d\d)\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", s)
    if m:
        return (int(m.group(1)), MONTHS[m.group(2)], 0)
    m = re.search(r"\b(20\d\d|19\d\d)\b", s)
    return (int(m.group(1)), 0, 0) if m else None


def _prec(d):
    return 1 if d[1] == 0 else 2 if d[2] == 0 else 3


def cmp_date(a, b):
    if a is None or b is None:
        return None
    p = min(_prec(a), _prec(b)); a, b = a[:p], b[:p]
    return (a > b) - (a < b)


def detect_metadata(text):
    """Best-effort version / date from the first pages. Only a suggestion: the caller marks it as 'auto'."""
    version = ""
    m = re.search(r"\b(?:ver(?:sion)?|rev(?:ision)?|edition|ed)\.?\s*[:#-]?\s*v?(\d+(?:\.\d+){0,3})\b", text or "", re.I) \
        or re.search(r"\bv\s?(\d+\.\d+(?:\.\d+){0,2})\b", text or "", re.I)
    if m:
        version = m.group(1)
    date = ""
    d = parse_date(text or "")
    if d:
        date = f"{d[0]:04d}" + (f"-{d[1]:02d}" if d[1] else "") + (f"-{d[2]:02d}" if d[2] else "")
    return version, date


def text_fingerprint(pdf_path, first_pages=3):
    """-> dict(text_sha, chars, head)  (text_sha is None for scanned / text-less PDFs)."""
    import pdfplumber
    h = hashlib.sha256(); chars = 0; head = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            raw = page.extract_text() or ""
            if i < first_pages:
                head.append(raw)
            n = normalize_text(raw)
            chars += len(n)
            h.update(n.encode("utf-8")); h.update(b"\x00")
    return dict(text_sha=h.hexdigest() if chars >= TEXT_MIN_CHARS else None, chars=chars, head="\n".join(head)[:8000])


# ------------------------------------------------------------------ level 1
def exact_match(c, sha):
    return c.execute("SELECT id,name,supplier,created,uploaded_by FROM catalogs WHERE file_sha=?", (sha,)).fetchone()


# ------------------------------------------------------------------ levels 2 + 3 (before extraction)
def _brief(r):
    return dict(id=r["id"], name=r["name"], supplier=r["supplier"], version=r["version"], date=r["catalog_date"], created=r["created"],
                by=r["uploaded_by"])


def classify(c, cid, text_sha, supplier, version, cdate):
    """-> dict(action in {'allow','confirm'}, kind, message, matches, candidate_previous)"""
    out = dict(action="allow", kind="", message="", matches=[], candidate_previous=None)
    cols = "id,name,supplier,version,catalog_date,created,uploaded_by"
    if text_sha:
        same = c.execute(f"SELECT {cols} FROM catalogs WHERE id<>? AND text_sha=? AND file_sha<>'manual'", (cid, text_sha)).fetchall()
        if same:
            r = same[0]
            return dict(action="confirm", kind="same_content", matches=[_brief(x) for x in same],
                        message=f"يتطابق المحتوى النصي مع كتالوج موجود باسم «{r['name']}» (رقم {r['id']})؛ وقد يكون الملف نفسه بعد تغيير اسمه.", candidate_previous=None)
    sup = norm_supplier(supplier)
    if not sup:
        return out
    rows = [r for r in c.execute(f"SELECT {cols} FROM catalogs WHERE id<>? AND file_sha<>'manual' AND status IN ('done','processing','checking','needs_decision')", (cid,)).fetchall()
            if norm_supplier(r["supplier"]) == sup]
    if not rows:
        return out
    v, d = parse_version(version), parse_date(cdate)
    newer_than, older_than = [], []
    for r in rows:
        rv, rd = parse_version(r["version"]), parse_date(r["catalog_date"])
        cv, cd = cmp_version(v, rv), cmp_date(d, rd)
        same_release = (cv == 0 and cd in (0, None)) or (cv is None and cd == 0)
        if same_release:
            return dict(action="confirm", kind="same_release", matches=[_brief(r)],
                        message=f"يوجد كتالوج للمورد نفسه بالإصدار أو التاريخ نفسيهما: «{r['name']}» (رقم {r['id']}). يُرجى التحقق مما إذا كان الملف مطابقًا.", candidate_previous=None)
        order = cv if cv is not None else cd
        if order == 1:
            newer_than.append((r, rv, rd))
        elif order == -1:
            older_than.append(r)
    if older_than:
        r = older_than[0]
        return dict(action="confirm", kind="older_release", matches=[_brief(x) for x in older_than],
                    message=f"إصدار هذا الكتالوج أو تاريخه أقدم من كتالوج آخر للمورد نفسه: «{r['name']}» (رقم {r['id']}).", candidate_previous=None)
    if newer_than:
        best = max(newer_than, key=lambda t: (t[1] or (0,), t[2] or (0, 0, 0)))[0]
        out.update(kind="newer_release", candidate_previous=best["id"], matches=[_brief(best)],
                   message=f"هذا إصدار أحدث من «{best['name']}» (رقم {best['id']})؛ سيُسمح برفعه وربطه بالإصدار السابق إذا تشابهت المنتجات.")
    return out


# ------------------------------------------------------------------ level 4 (after extraction)
def _vnorm(v):
    return re.sub(r"[\s×x*]+", "x", (v or "").lower()).replace(",", "")


def signature(c, cid):
    """{model code -> {attribute -> value}} for the active models of a catalog (final values, normalised)."""
    import dictionary
    amap = dictionary.load_map(c)
    models = {r["id"]: re.sub(r"[\s\-_/]+", "", (r["code"] or "").upper()) for r in
              c.execute("SELECT id,code FROM models WHERE catalog_id=? AND active=1", (cid,)).fetchall()}
    sig = {code: {} for code in models.values() if code}
    for r in c.execute("""SELECT s.model_id, s.attr, s.supplier_value, s.company_value FROM specs s JOIN models m ON m.id=s.model_id
                          WHERE m.catalog_id=? AND m.active=1 AND s.hidden=0""", (cid,)).fetchall():
        code = models.get(r["model_id"])
        if code:
            k = dictionary.key(r["attr"]); k = (amap.get(k) or {}).get("canonical") or k
            sig[code][k.lower()] = _vnorm(r["supplier_value"])
    return sig


def compare(c, a_id, b_id):
    """Compare catalog a (new) against b (existing)."""
    A, B = signature(c, a_id), signature(c, b_id)
    common = sorted(set(A) & set(B))
    same = tot = 0; changed = []
    for code in common:
        keys = set(A[code]) | set(B[code]); diff = [k for k in keys if A[code].get(k) != B[code].get(k)]
        tot += len(keys); same += len(keys) - len(diff)
        if diff:
            changed.append((code, sorted(diff)))
    union = len(set(A) | set(B))
    return dict(a=a_id, b=b_id, n_a=len(A), n_b=len(B), common=len(common), added=sorted(set(A) - set(B)), removed=sorted(set(B) - set(A)),
                jaccard=(len(common) / union) if union else 0.0, overlap_a=(len(common) / len(A)) if A else 0.0,
                overlap_b=(len(common) / len(B)) if B else 0.0, spec_same=(same / tot) if tot else 1.0, changed=changed)


def post_extraction(c, cid):
    """Level 4. Writes review items, may set catalogs.previous_catalog_id. Returns the list of findings."""
    me = c.execute("SELECT * FROM catalogs WHERE id=?", (cid,)).fetchone()
    cand = None
    if me["dup_info"]:
        try:
            cand = json.loads(me["dup_info"]).get("candidate_previous")
        except Exception:
            cand = None
    mine = {n for n in (re.sub(r"[\s\-_/]+", "", (r["code"] or "").upper()) for r in
                        c.execute("SELECT code FROM models WHERE catalog_id=? AND active=1", (cid,)).fetchall()) if n}
    if not mine:
        return []
    others = {}
    for r in c.execute("""SELECT m.catalog_id, m.code FROM models m JOIN catalogs k ON k.id=m.catalog_id
                          WHERE m.catalog_id<>? AND m.active=1 AND k.file_sha<>'manual' AND k.status='done'""", (cid,)).fetchall():
        others.setdefault(r["catalog_id"], set()).add(re.sub(r"[\s\-_/]+", "", (r["code"] or "").upper()))
    ranked = []
    for oid, codes in others.items():
        common = len(mine & codes)
        if common and (common / len(mine) >= UPDATE_OVERLAP or common / len(codes) >= UPDATE_OVERLAP):
            ranked.append((common / len(mine | codes), oid))
    findings = []
    for _j, oid in sorted(ranked, reverse=True)[:3]:
        cmpd = compare(c, cid, oid); other = c.execute("SELECT * FROM catalogs WHERE id=?", (oid,)).fetchone()
        stats = (f"الطرازات المشتركة {cmpd['common']} | الجديدة {len(cmpd['added'])} | المحذوفة {len(cmpd['removed'])} | "
                 f"الطرازات التي تغيّرت مواصفاتها {len(cmpd['changed'])} | نسبة تطابق المواصفات {cmpd['spec_same']:.0%}")
        meta_newer = (cand == oid)
        near = cmpd["jaccard"] >= NEAR_DUP_JACCARD and cmpd["spec_same"] >= NEAR_DUP_SPEC_SAME
        if near and not meta_newer:
            c.execute("INSERT INTO review(catalog_id,kind,ref,note) VALUES(?,?,?,?)",
                      (cid, "SIMILAR_CATALOG", f"catalog {oid}",
                       f"يوجد تشابه كبير مع «{other['name']}» (رقم {oid}): {stats}. يُرجّح أن تكون نسخة مكررة؛ يمكنك اتخاذ القرار من صفحة الكتالوج."))
            findings.append(("similar", oid)); continue
        if meta_newer or cmpd["overlap_a"] >= UPDATE_OVERLAP or cmpd["overlap_b"] >= UPDATE_OVERLAP:
            if meta_newer and not me["previous_catalog_id"]:
                c.execute("UPDATE catalogs SET previous_catalog_id=? WHERE id=?", (oid, cid))
                db.log(c, "catalog", cid, "link_previous", f"auto: newer release of catalog {oid}")
                c.execute("INSERT INTO review(catalog_id,kind,ref,note) VALUES(?,?,?,?)",
                           (cid, "SUPERSEDES", f"catalog {oid}", f"هذا إصدار أحدث من «{other['name']}» (رقم {oid}) ورُبط به تلقائيًا: {stats}."))
                findings.append(("superseded", oid))
            else:
                c.execute("INSERT INTO review(catalog_id,kind,ref,note) VALUES(?,?,?,?)",
                          (cid, "LIKELY_UPDATE", f"catalog {oid}",
                            f"قد يكون هذا تحديثًا لـ«{other['name']}» (رقم {oid}) وليس نسخة مكررة: {stats}. يمكنك ربطه بوصفه إصدارًا لاحقًا من صفحة الكتالوج."))
                findings.append(("likely_update", oid))
    return findings
