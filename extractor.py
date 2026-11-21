"""Catalog extraction pipeline.

pipeline(): fingerprint + duplicate checks (levels 2-3)  ->  extraction  ->  level-4 comparison.
A failed extraction is rolled back completely (no half-imported catalog), and can be retried.
Everything the engine is not sure about goes to the review list; nothing is guessed silently.
"""
import io, os, re, hashlib, traceback, threading, datetime, json
import pdfplumber, pypdfium2 as pdfium
from PIL import Image
import db, storage, dedupe

LABEL_RE = re.compile(r"^[A-Z]{1,5}-?\d{2,4}[A-Z]?(\([^)]*\))?$")
# standards / ratings that look like model codes (IP54, ISO9001 ...) but are not
LABEL_BLACK = re.compile(r"^(IP|ISO|EN|DIN|UL|CE|IEC|AWG|AC|DC|TUV|SGS|ASTM|JIS|HS)-?\d", re.I)
MODEL_HEAD_RE = re.compile(r"^\s*(model|type|موديل|الموديل|طراز|الطراز)\b", re.I)
WEAK_HEAD_RE = re.compile(r"^\s*(item|items|product|no\.?|code|spec(?:ification)?s?|parameters?|ref\.?|الصنف|المنتج|كود|المواصفات)\b", re.I)
CODE_LIKE = re.compile(r"^(?=.*\d)[A-Za-z0-9\u0600-\u06FF][A-Za-z0-9\u0600-\u06FF \-_/.()+Φφ]{0,29}$")
PURE_NUM = re.compile(r"^\d+([.,]\d+)?$")
REPEAT_MIN_PAGES = 3           # same image on this many pages of one catalog
REPEAT_MIN_MODELS = 3          # an image linked to this many models of one catalog is probably a logo / banner


def _now():
    return datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _clean(x):
    return (x or "").replace("\n", " ").strip()


def _is_code(s):
    s = _clean(s)
    return bool(s) and not PURE_NUM.match(s) and bool(CODE_LIKE.match(s))


def _image_bytes(im):
    """Return (bytes, ext). JPEG stays byte-identical; JPX is decoded with its transparency mask."""
    st = im["stream"]
    flt = st.attrs.get("Filter")
    names = [getattr(f, "name", str(f)) for f in (flt if isinstance(flt, list) else [flt])]
    if "DCTDecode" in names:
        return st.get_data(), "jpg"
    # JPX (JPEG 2000, usually with a transparency mask and clipping) is NOT decoded from the raw stream: on real catalogs
    # (e.g. Corel-exported ones) that gave tiled / striped pictures. The caller renders the placed region with pdfium instead.
    return None, None


def _is_jpx(im):
    flt = im["stream"].attrs.get("Filter")
    return any(getattr(f, "name", str(f)) == "JPXDecode" for f in (flt if isinstance(flt, list) else [flt]))


def _render_region(pdfi, page, pl_page, bbox, scale=4, pil=None):
    pil = pil if pil is not None else pdfi[page].render(scale=scale).to_pil()   # pass `pil` to render a page once for many crops
    ox, oy = pl_page.bbox[0], pl_page.bbox[1]
    box = [int((bbox[0] - ox) * scale), int((bbox[1] - oy) * scale), int((bbox[2] - ox) * scale), int((bbox[3] - oy) * scale)]
    buf = io.BytesIO(); pil.crop(box).convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def render_page_png(pdf_path, page_no, scale=1.6):
    pdfi = pdfium.PdfDocument(pdf_path)
    try:
        pil = pdfi[page_no - 1].render(scale=scale).to_pil().convert("RGB")
    finally:
        pdfi.close()
    buf = io.BytesIO(); pil.save(buf, "PNG")
    return buf.getvalue()


# ------------------------------------------------------------------ table understanding
def parse_table(t):
    """-> list of (code, [(attr, value, row_index, raw_cell, missing_cell)]) or None.
    Handles models-in-columns (the common layout) and models-in-rows (transposed) tables."""
    if not t or len(t) < 2 or not t[0] or not _clean(t[0][0]):
        return None
    head0 = _clean(t[0][0])
    strong, weak = bool(MODEL_HEAD_RE.match(head0)), bool(WEAK_HEAD_RE.match(head0))
    if not (strong or weak):
        return None
    hdr = [_clean(x) for x in t[0][1:]]
    first_col = [_clean(r[0]) if r else "" for r in t[1:]]
    hn, hcode = sum(1 for x in hdr if x), sum(1 for x in hdr if _is_code(x))
    fn, fcode = sum(1 for x in first_col if x), sum(1 for x in first_col if _is_code(x))
    transposed = fn >= 2 and fcode / fn >= 0.8 and (hn == 0 or hcode / hn < 0.5)
    if transposed:
        out = []
        for row in t[1:]:
            code = _clean(row[0]) if row else ""
            if not code:
                continue
            specs = []
            for j, attr in enumerate(hdr, start=1):
                if not attr:
                    continue
                raw = row[j] if j < len(row) else None
                if raw is None or not str(raw).strip():
                    continue
                specs.append((attr, _clean(raw), j - 1, str(raw), False))
            out.append((code, specs))
        return out or None
    if not hn:
        return None
    if not strong and hcode / hn < 0.8:           # weak header: every header cell must look like a model code
        return None
    out = []
    for ci, code in enumerate(hdr, start=1):
        if not code:
            continue
        specs = []
        for si, row in enumerate(t[1:]):
            if not row or not row[0]:
                continue
            raw = row[ci] if ci < len(row) else None
            if raw is None:
                specs.append((_clean(row[0]), "", si, "", True))      # None = merged / missing cell, reported by the caller
                continue
            if not str(raw).strip():
                continue
            specs.append((_clean(row[0]), _clean(raw), si, str(raw), False))
        out.append((code, specs))
    return out or None


# ------------------------------------------------------------------ cleanup / recovery
def purge_children(c, cid):
    """Remove everything an (unfinished or failed) extraction created for catalog `cid`. Returns Drive ids of removed images."""
    c.execute("DELETE FROM image_links WHERE model_id IN (SELECT id FROM models WHERE catalog_id=?)", (cid,))      # links of other catalogs to our images stay
    c.execute("DELETE FROM specs WHERE model_id IN (SELECT id FROM models WHERE catalog_id=?)", (cid,))
    c.execute("DELETE FROM models WHERE catalog_id=?", (cid,))
    c.execute("DELETE FROM review WHERE catalog_id=?", (cid,))
    gone = c.execute("""SELECT id, file, drive_id FROM images WHERE catalog_id=?
                       AND id NOT IN (SELECT image_id FROM image_links)
                       AND id NOT IN (SELECT image_id FROM product_image_links)""", (cid,)).fetchall()
    for r in gone:
        c.execute("DELETE FROM images WHERE id=?", (r["id"],))
    return [(r["file"], r["drive_id"]) for r in gone]


def _drop_files(files):
    for fname, did in files:
        if did:
            storage.delete(did)
        try:
            os.remove(os.path.join(db.IMG_DIR, fname))
        except OSError:
            pass
        try:
            os.remove(os.path.join(db.CACHE_DIR, "images", fname))
        except OSError:
            pass


SNAP_TABLES = ["models", "specs", "image_links", "review"]


def snapshot_catalog(c, cid):
    """Everything needed to bring a deleted catalog back (original ids kept)."""
    import dataio
    snap = {"catalog": dataio.read_table(c, "catalogs") and [r for r in dataio.read_table(c, "catalogs") if r["id"] == cid][0]}
    mids = [r["id"] for r in c.execute("SELECT id FROM models WHERE catalog_id=?", (cid,)).fetchall()]
    snap["models"] = [{k: r[k] for k in r.keys()} for r in c.execute("SELECT * FROM models WHERE catalog_id=? ORDER BY id", (cid,)).fetchall()]
    snap["specs"] = [{k: r[k] for k in r.keys()} for r in c.execute("SELECT s.* FROM specs s JOIN models m ON m.id=s.model_id WHERE m.catalog_id=? ORDER BY s.id", (cid,)).fetchall()]
    snap["image_links"] = [{k: r[k] for k in r.keys()} for r in c.execute("SELECT l.* FROM image_links l JOIN models m ON m.id=l.model_id WHERE m.catalog_id=? ORDER BY l.id", (cid,)).fetchall()]
    snap["images"] = [{k: r[k] for k in r.keys()} for r in c.execute("SELECT * FROM images WHERE catalog_id=? ORDER BY id", (cid,)).fetchall()]
    snap["review"] = [{k: r[k] for k in r.keys()} for r in c.execute("SELECT * FROM review WHERE catalog_id=? ORDER BY id", (cid,)).fetchall()]
    return snap


def delete_catalog(c, cid):
    """Delete a catalog and what it created. Refused (returns message) when it has approved models.
    A snapshot is kept in `deleted_records` (restorable from the Trash page) and the stored files (Drive) are kept."""
    cat = c.execute("SELECT * FROM catalogs WHERE id=?", (cid,)).fetchone()
    if not cat or cat["file_sha"] == "manual":
        return "الكتالوج غير موجود، أو أنه أُنشئ عبر الإدخال اليدوي."
    n = c.execute("SELECT COUNT(*) n FROM models WHERE catalog_id=? AND status='approved'", (cid,)).fetchone()["n"]
    if n:
        return f"يحتوي هذا الكتالوج على {n} طرازًا معتمدًا. انقل هذه الطرازات أو ألغِ اعتمادها أولًا."
    snap = snapshot_catalog(c, cid)
    c.execute("INSERT INTO deleted_records(username,entity,entity_id,label,snapshot) VALUES(?,?,?,?,?)",
              (db.actor(), "catalog", cid, f"{cat['name']} ({cat['supplier']})", json.dumps(snap, ensure_ascii=False, default=str)))
    purge_children(c, cid)                         # DB rows only: the files stay in storage so the catalog can be restored
    kept = [r["id"] for r in c.execute("SELECT id FROM images WHERE catalog_id=?", (cid,)).fetchall()]     # images still used by other catalogs' models
    if kept:
        c.execute("UPDATE images SET catalog_id=NULL WHERE catalog_id=?", (cid,))
        db.log(c, "catalog", cid, "images_kept", f"{len(kept)} images are linked from other catalogs and stay available")
    c.execute("UPDATE catalogs SET previous_catalog_id=NULL WHERE previous_catalog_id=?", (cid,))
    snap["kept_images"] = kept
    c.execute("UPDATE deleted_records SET snapshot=? WHERE entity='catalog' AND entity_id=? AND restored=0", (json.dumps(snap, ensure_ascii=False, default=str), cid))
    c.execute("DELETE FROM catalogs WHERE id=?", (cid,))
    db.log(c, "catalog", cid, "delete", f"{cat['name']} ({cat['supplier']}): {len(snap['models'])} models, {len(snap['specs'])} specs (restorable from Trash)")
    c.commit()
    return ""


def restore_catalog(c, rec_id):
    """Re-create a deleted catalog from its snapshot. Returns an error message or ''."""
    rec = c.execute("SELECT * FROM deleted_records WHERE id=? AND entity='catalog' AND restored=0", (rec_id,)).fetchone()
    if not rec:
        return "السجل غير موجود أو سبق أن تمت استعادته."
    snap = json.loads(rec["snapshot"]); cat = snap["catalog"]
    if c.execute("SELECT 1 FROM catalogs WHERE id=? OR file_sha=?", (cat["id"], cat["file_sha"])).fetchone():
        return "كتالوج بنفس الرقم أو نفس الملف موجود حاليًا."
    import dataio
    fam = {r["id"] for r in c.execute("SELECT id FROM families").fetchall()}; prd = {r["id"] for r in c.execute("SELECT id FROM products").fetchall()}
    try:
        cat = dict(cat); cat["status"] = "done"; cat["previous_catalog_id"] = None if not c.execute("SELECT 1 FROM catalogs WHERE id=?", (cat.get("previous_catalog_id"),)).fetchone() else cat.get("previous_catalog_id")
        dataio.insert_rows(c, "catalogs", [cat])
        models = []
        for m in snap["models"]:
            m = dict(m); m["family_id"] = m["family_id"] if m["family_id"] in fam else None; m["product_id"] = m["product_id"] if m["product_id"] in prd else None; models.append(m)
        dataio.insert_rows(c, "models", models)
        dataio.insert_rows(c, "specs", snap["specs"])
        have = {r["id"] for r in c.execute("SELECT id FROM images").fetchall()}
        dataio.insert_rows(c, "images", [i for i in snap["images"] if i["id"] not in have])
        for iid in snap.get("kept_images", []):
            c.execute("UPDATE images SET catalog_id=? WHERE id=? AND catalog_id IS NULL", (cat["id"], iid))
        have = {r["id"] for r in c.execute("SELECT id FROM images").fetchall()}
        dataio.insert_rows(c, "image_links", [l for l in snap["image_links"] if l["image_id"] in have])
        dataio.insert_rows(c, "review", snap["review"])
        c.execute("UPDATE deleted_records SET restored=1 WHERE id=?", (rec_id,))
        db.log(c, "catalog", cat["id"], "restore", f"{cat['name']}: {len(models)} models, {len(snap['specs'])} specs")
        c.commit()
    except Exception as ex:
        c.rollback(); return f"تعذرت الاستعادة: {ex}"
    return ""


# ------------------------------------------------------------------ pipeline
def start(cid, actor="system", skip_checks=False):
    threading.Thread(target=pipeline, args=(cid, actor, skip_checks), daemon=True).start()


def _status(c, cid, status, msg=""):
    c.execute("UPDATE catalogs SET status=?, msg=?, heartbeat=? WHERE id=?", (status, msg, _now(), cid)); c.commit()


def pipeline(cid, actor="system", skip_checks=False):
    db.set_actor(actor)
    c = db.conn()
    try:
        cat = c.execute("SELECT * FROM catalogs WHERE id=?", (cid,)).fetchone()
        path = storage.catalog_path(cat["file_sha"], cat["drive_id"])
        if not skip_checks:
            _status(c, cid, "checking", "فحص التكرار")
            fp = dedupe.text_fingerprint(path)
            ver, date, src = cat["version"] or "", cat["catalog_date"] or "", cat["meta_source"] or ""
            dv, dd = dedupe.detect_metadata(fp["head"])
            if not ver and dv:
                ver, src = dv, "auto"
            if not date and dd:
                date, src = dd, "auto"
            verdict = dedupe.classify(c, cid, fp["text_sha"], cat["supplier"], ver, date)
            c.execute("UPDATE catalogs SET text_sha=?, text_chars=?, version=?, catalog_date=?, meta_source=?, dup_info=? WHERE id=?",
                      (fp["text_sha"], fp["chars"], ver, date, src, json.dumps(verdict, ensure_ascii=False), cid))
            if verdict["action"] == "confirm":
                db.log(c, "catalog", cid, "duplicate_warning", f"{verdict['kind']}: {verdict['message']}")
                _status(c, cid, "needs_decision", verdict["message"])
                return
            c.commit()
        run(cid, path)
    except Exception:
        try:
            c.rollback()
            _status(c, cid, "error", traceback.format_exc()[-800:])
        except Exception:
            pass
    finally:
        c.close()


def run(catalog_id, pdf_path):
    """Extraction + level-4 comparison. On any failure the partial import is removed and the status becomes 'error'."""
    c = db.conn()
    try:
        _drop_files(purge_children(c, catalog_id))
        _status(c, catalog_id, "processing", "")
        _run(c, catalog_id, pdf_path)
        dedupe.post_extraction(c, catalog_id)
        c.execute("UPDATE catalogs SET status='done', msg='', heartbeat=? WHERE id=?", (_now(), catalog_id))
        c.commit()
    except Exception:
        tb = traceback.format_exc()[-800:]
        c.rollback()
        try:
            _drop_files(purge_children(c, catalog_id))
            c.execute("UPDATE catalogs SET status='error', msg=?, heartbeat=? WHERE id=?", (tb, _now(), catalog_id))
            c.commit()
        except Exception:
            c.rollback()
    finally:
        c.close()


def _tokens_on_page(val, words):
    """Every word of the value is present on the page (used when a wrapped cell is interleaved with its neighbour column)."""
    have = {}
    for w in words:
        have[w["text"].lower()] = have.get(w["text"].lower(), 0) + 1
    need = {}
    for t in val.lower().split():
        need[t] = need.get(t, 0) + 1
    return bool(need) and all(have.get(t, 0) >= n for t, n in need.items())


# first heading line that names the product category (a descriptor like "Signature Series" never matches); extend per catalog family
CATEGORY_LINE = re.compile(r"\b(machines?|machinery|tools?|equipment|cutters?|benders?|saws?)\b", re.I)
HEAD_CODE = re.compile(r"^[A-Z]{1,5}-?\d{2,4}[A-Z]?$")


def _header_series(page, codes, top_limit=150, max_gap=45):
    """Series/descriptor text from the page heading. The first heading line is the product category title
    (e.g. 'Rebar Bending Machine') and is dropped, unless it holds a model code (then it is already a descriptor
    line, because some catalogs draw the title bar as a picture). Model codes are removed. Lines are read downward
    until a big vertical gap (a photo caption is not part of the heading).
    Returns (series, stray_codes_not_in_the_table)."""
    try:
        words = [w for w in page.extract_words(x_tolerance=6) if w["top"] < top_limit]    # no crop(): pages can have an offset bbox
    except Exception:
        return "", []
    lines, prev = [], None
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if lines and abs(lines[-1][0] - w["top"]) < 5:
            if prev is not None and len(prev["text"]) == 1 and prev["text"].isupper() and w["text"][:1].islower() \
                    and w["x0"] - prev["x1"] < 2:                       # 'S' + 'ignature': a glyph split by the PDF, not two words
                lines[-1][1][-1] = prev["text"] + w["text"]
            else:
                lines[-1][1].append(w["text"])
        else:
            lines.append([w["top"], [w["text"]]])
        prev = w
    kept = []
    for i, (top, ws) in enumerate(lines):
        if i and top - lines[i - 1][0] > max_gap:
            break
        kept.append(ws)
    if kept and not any(t in codes for t in kept[0]) and CATEGORY_LINE.search(" ".join(kept[0])):
        kept = kept[1:]                                        # 'Rebar Bending Machine' = the category title, not a series
    stray, out = [], []
    for ws in kept:
        toks = []
        for tok in ws:
            if tok in codes:
                toks.append(None); continue
            if HEAD_CODE.match(tok):
                stray.append(tok); toks.append(None); continue
            toks.append(tok)
        toks = [t for t in toks if t is not None]
        while toks and not re.search(r"\w", toks[0]): toks.pop(0)          # leftovers like '/' next to removed codes
        while toks and not re.search(r"\w", toks[-1]): toks.pop()
        s_ = " ".join(toks).strip()
        if s_ and s_ not in out:
            out.append(s_)
    return " ".join(out)[:120], stray


def _review(c, cid, kind, ref, note):
    c.execute("INSERT INTO review(catalog_id,kind,ref,note) VALUES(?,?,?,?)", (cid, kind, ref, note))


def _run(c, cid, pdf_path):
    pdfi = pdfium.PdfDocument(pdf_path)
    try:
        with pdfplumber.open(pdf_path) as pdf:
            _extract(c, cid, pdf, pdfi)
    finally:
        pdfi.close()


def _extract(c, cid, pdf, pdfi):
    npages = len(pdf.pages)
    c.execute("UPDATE catalogs SET pages=? WHERE id=?", (npages, cid)); c.commit()
    sha_seen = {r["sha"]: r["file"] for r in c.execute("SELECT sha,file FROM images")}
    code_series = {}
    sha_pages, sha_image = {}, {}

    for pi, page in enumerate(pdf.pages):
        pno = pi + 1
        c.execute("UPDATE catalogs SET msg=?, heartbeat=? WHERE id=?", (f"{pno}/{npages}", _now(), cid)); c.commit()
        try:
            words = page.extract_words(x_tolerance=4)
            tables = page.extract_tables()
            page_images = [i for i in page.images if i["srcsize"][0] >= 150 and i["srcsize"][1] >= 100]
        except Exception as e:
            _review(c, cid, "PAGE_ERROR", f"page {pno}", f"could not read this page ({type(e).__name__}); add its content manually"); c.commit()
            continue
        page_norm = re.sub(r"\s+", "", " ".join(w["text"] for w in words).lower())
        page_model_ids = []

        # ---- spec tables
        for t in tables:
            parsed = parse_table(t)
            if not parsed:
                continue
            codes = [code for code, _s in parsed]
            series, stray = _header_series(page, codes)
            for sc in stray:
                _review(c, cid, "HEADER_CODE_MISMATCH", f"page {pno}",
                        f"the page heading says {sc} but the spec table says {', '.join(codes)}: the catalog may contain a typo; the table was used")
            merged_rows = {}
            for code, specs in parsed:
                grp = re.match(r"[A-Za-z]+", code)
                mid = c.execute("INSERT INTO models(catalog_id,code,series,page,suggested_group) VALUES(?,?,?,?,?)",
                                (cid, code, series, pno, grp.group(0) if grp else "")).lastrowid
                page_model_ids.append(mid)
                code_series.setdefault(code, []).append((mid, series, pno))
                for attr, val, si, raw, missing in specs:
                    if missing:
                        merged_rows.setdefault(attr, []).append(code)
                        continue
                    ok = 1 if (re.sub(r"\s+", "", val.lower()) in page_norm or _tokens_on_page(val, words)) else 0
                    c.execute("INSERT INTO specs(model_id,attr,supplier_value,sort,source_page,source_text,verified) VALUES(?,?,?,?,?,?,?)",
                              (mid, attr, val, si, pno, raw.strip()[:300], ok))
            for attr, cs in merged_rows.items():
                if len(cs) < len(codes):          # other models have a value in this row -> the blank cells are probably merged cells
                    _review(c, cid, "MERGED_CELL", f"page {pno}: {attr}",
                            f"no value read for {', '.join(cs)} (merged or missing cell); other models have a value. Check the page and fill the spec manually.")

        # ---- images
        page_pil = None
        imgs = sorted(page_images, key=lambda i: (round(i["top"] / 30), i["x0"]))
        grid_page = (not page_model_ids) and len(imgs) >= 3
        labels = [w for w in words if LABEL_RE.match(w["text"]) and not LABEL_BLACK.match(w["text"])] if grid_page else []
        main_idx = max(range(len(imgs)), key=lambda k: imgs[k]["srcsize"][0] * imgs[k]["srcsize"][1]) if imgs else None
        label_models = {}
        for k, im in enumerate(imgs):
            sw, sh = im["srcsize"]
            data, ext = _image_bytes(im)
            source = "original"
            if data is None:
                if page_pil is None:
                    page_pil = pdfi[pi].render(scale=4).to_pil()            # one render per page, cropped per image
                data = _render_region(pdfi, pi, page, (im["x0"], im["top"], im["x1"], im["bottom"]), pil=page_pil); ext = "png"; source = "rendered"
                if not _is_jpx(im):
                    _review(c, cid, "IMAGE_RENDERED", f"page {pno}", "image encoding not supported; saved as a rendering of the page area, check it")
            h = _sha(data)
            if h in sha_seen:
                fname = sha_seen[h]
                image_id = c.execute("SELECT id FROM images WHERE sha=?", (h,)).fetchone()["id"]
                _review(c, cid, "DUPLICATE_IMAGE", f"page {pno}", f"identical to an existing image (file {fname}); reused, nothing deleted")
            else:
                fname = f"{h[:20]}.{ext}"
                did = storage.save_image(fname, data)
                wpt = im["x1"] - im["x0"]
                dpi = round(sw / (wpt / 72), 0) if wpt else 0
                image_id = c.execute("INSERT INTO images(catalog_id,page,file,sha,w,h,dpi,source,drive_id) VALUES(?,?,?,?,?,?,?,?,?)",
                                     (cid, pno, fname, h, sw, sh, dpi, source, did)).lastrowid
                sha_seen[h] = fname

            sha_pages.setdefault(h, set()).add(pno); sha_image[h] = image_id

            if page_model_ids:
                if len(page_model_ids) > 1:
                    _review(c, cid, "SHARED_IMAGE", f"page {pno}", f"page has {len(page_model_ids)} models; choose which model each image belongs to")
                    continue                                   # not linked automatically
                if k == main_idx:
                    conf = "high"
                else:
                    conf = "review"
                    _review(c, cid, "EXTRA_IMAGE", f"page {pno}", "more than one image on a single-model page: only the largest is linked automatically")
                c.execute("INSERT INTO image_links(image_id,model_id,confidence) VALUES(?,?,?) ON CONFLICT DO NOTHING", (image_id, page_model_ids[0], conf))
            elif grid_page and labels:
                cx = (im["x0"] + im["x1"]) / 2
                cands = []
                for lb in labels:
                    if lb["top"] >= im["bottom"] - 5 and lb["top"] - im["bottom"] < 120:
                        cands.append((abs((lb["x0"] + lb["x1"]) / 2 - cx) + (lb["top"] - im["bottom"]) * 0.8, lb["text"]))
                cands.sort()
                if not cands:
                    _review(c, cid, "IMAGE_NO_LABEL", f"page {pno}", "no model label found under this image")
                    continue
                raw_code = cands[0][1]
                sm = re.match(r"^(.*?)\(([^)]*)\)$", raw_code)
                code, suffix = (sm.group(1), sm.group(2)) if sm else (raw_code, "")
                if code not in label_models:
                    mid = c.execute("INSERT INTO models(catalog_id,code,series,page,suggested_group) VALUES(?,?,?,?,?)",
                                    (cid, code, suffix, pno, re.match(r"[A-Za-z]+", code).group(0))).lastrowid
                    label_models[code] = mid
                    _review(c, cid, "NO_SPECS", code, "listed with a photo only (no spec table); add specs manually")
                    conf = "medium"
                else:
                    conf = "review"
                    _review(c, cid, "SECOND_IMAGE", code, "two images map to this label: one may be a detail inset; choose the main one")
                c.execute("INSERT INTO image_links(image_id,model_id,confidence) VALUES(?,?,?) ON CONFLICT DO NOTHING", (image_id, label_models[code], conf))
            else:
                _review(c, cid, "IMAGE_UNLINKED", f"page {pno}", "image on a page without a parsed model table")
        if imgs and not page_model_ids and not grid_page:
            _review(c, cid, "NO_TABLE", f"page {pno}", "no spec table recognised on this page (text-only or unruled table): add manually")
        c.commit()

    for code, lst in code_series.items():
        if len(lst) > 1:
            _review(c, cid, "SAME_CODE_MULTIPLE", code,
                    f"{len(lst)} entries on pages {[x[2] for x in lst]}; they may be different series with different specs: treated as separate models")
    # an image that shows up on many pages (or is attached to many models) of one catalog is almost certainly a logo / banner
    rep = {sha_image[h]: len(p) for h, p in sha_pages.items() if len(p) >= REPEAT_MIN_PAGES}
    for r in c.execute("""SELECT l.image_id, COUNT(DISTINCT l.model_id) n FROM image_links l JOIN models m ON m.id=l.model_id
                          WHERE m.catalog_id=? AND l.confidence IN ('high','medium') GROUP BY l.image_id HAVING COUNT(DISTINCT l.model_id)>=?""",
                      (cid, REPEAT_MIN_MODELS)).fetchall():
        rep.setdefault(r["image_id"], r["n"])
    for image_id, n in rep.items():
        c.execute("UPDATE image_links SET confidence='review' WHERE image_id=? AND confidence IN ('high','medium') AND model_id IN (SELECT id FROM models WHERE catalog_id=?)", (image_id, cid))
        _review(c, cid, "REPEATED_IMAGE", f"image {image_id}", f"the same image appears {n} times in this catalog (logo/banner?): its links are set to 'review', confirm or remove them")
    c.commit()
