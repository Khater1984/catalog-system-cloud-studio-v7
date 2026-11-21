"""Unified 7-Step Export Center with Version History."""
import os, json, datetime, io, uuid
from flask import Blueprint, request, render_template_string, send_file, redirect, flash, abort
from werkzeug.utils import secure_filename
import db, profiles, pdf_engine, storage

bp = Blueprint("export_center", __name__, url_prefix="/export-center")


def collect_items(doc_type, product_id=None, model_id=None, family_id=None, supplier=None, only_approved=False, model_ids=None):
    c = db.conn()
    items = []
    approval_field = "ok_sheet" if doc_type == "tds" else "ok_catalog"

    if doc_type == "tds" and model_id:
        models = c.execute("SELECT * FROM models WHERE id=? AND active=1", (model_id,)).fetchall()
    elif doc_type == "tds" and product_id:
        models = c.execute("SELECT * FROM models WHERE product_id=? AND active=1 ORDER BY sort_order, code", (product_id,)).fetchall()
        if not models:
            p = c.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
            if p:
                pspecs = c.execute("SELECT * FROM specs WHERE product_id=? AND model_id IS NULL AND deleted=0 ORDER BY sort", (product_id,)).fetchall()
                pimgs = c.execute("""
                    SELECT pl.image_role, pl.is_primary, pl.ok_sheet, pl.ok_catalog, i.file FROM product_image_links pl
                    JOIN images i ON i.id=pl.image_id WHERE pl.product_id=? AND i.disabled=0 AND pl.ok_sheet=1
                    ORDER BY pl.is_primary DESC, pl.sort_order ASC
                """, (product_id,)).fetchall()
                fam = c.execute("SELECT name FROM families WHERE id=?", (p["family_id"],)).fetchone()
                items.append(dict(m={"code": p["name"], "ar_name": p["ar_name"]}, product=dict(p),
                                  specs=[dict(s) for s in pspecs], images=[dict(im) for im in pimgs],
                                  family_name=fam["name"] if fam else ""))
            c.close()
            return items
    else:
        sql = """
            SELECT m.* FROM models m
            LEFT JOIN products p ON p.id=m.product_id
            LEFT JOIN catalogs c ON c.id=m.catalog_id
            LEFT JOIN families f ON f.id=COALESCE(m.family_id, p.family_id)
            WHERE m.active=1 AND m.visible=1 AND COALESCE(p.active,1)=1
        """
        args = []
        if product_id:
            sql += " AND m.product_id=?"; args.append(product_id)
        if family_id:
            sql += " AND COALESCE(m.family_id, p.family_id)=?"; args.append(family_id)
        if supplier:
            sql += " AND COALESCE(NULLIF(p.supplier,''), c.supplier)=?"; args.append(supplier)
        if only_approved:
            sql += " AND m.status='approved'"
        if model_ids:
            selected_ids = [int(x) for x in model_ids if str(x).isdigit()]
            if selected_ids:
                sql += " AND m.id IN (" + ",".join("?" for _ in selected_ids) + ")"
                args.extend(selected_ids)
        sql += " ORDER BY f.sort_order ASC, f.name ASC, p.sort_order ASC, m.sort_order ASC, m.code ASC"
        models = c.execute(sql, args).fetchall()

    for m in models:
        prod = c.execute("SELECT * FROM products WHERE id=?", (m["product_id"],)).fetchone() if m["product_id"] else None
        fam_id = m["family_id"] or (prod["family_id"] if prod else None)
        fam = c.execute("SELECT name FROM families WHERE id=?", (fam_id,)).fetchone() if fam_id else None

        spec_map = {}
        if prod:
            for ps in c.execute("SELECT * FROM specs WHERE product_id=? AND model_id IS NULL AND deleted=0 ORDER BY sort", (prod["id"],)):
                spec_map[ps["attr"].strip().lower()] = dict(ps)
        for ms in c.execute("SELECT * FROM specs WHERE model_id=? AND deleted=0 ORDER BY sort", (m["id"],)):
            spec_map[ms["attr"].strip().lower()] = dict(ms)

        m_imgs = [dict(r) for r in c.execute(f"""
            SELECT l.image_role, l.is_primary, l.ok_sheet, l.ok_catalog, i.file FROM image_links l
            JOIN images i ON i.id=l.image_id WHERE l.model_id=? AND i.disabled=0
              AND l.{approval_field}=1
            ORDER BY l.is_primary DESC, l.sort_order ASC
        """, (m["id"],))]
        if not m_imgs and prod:
            m_imgs = [dict(r) for r in c.execute(f"""
                SELECT pl.image_role, pl.is_primary, pl.ok_sheet, pl.ok_catalog, i.file FROM product_image_links pl
                JOIN images i ON i.id=pl.image_id WHERE pl.product_id=? AND i.disabled=0
                  AND pl.{approval_field}=1
                ORDER BY pl.is_primary DESC, pl.sort_order ASC
            """, (prod["id"],))]

        items.append(dict(
            m=dict(m),
            product=dict(prod) if prod else {},
            specs=list(spec_map.values()),
            images=m_imgs,
            family_name=fam["name"] if fam else "General"
        ))

    c.close()
    return items


@bp.route("/", methods=["GET", "POST"])
def index():
    c = db.conn()
    prods = c.execute("SELECT id, name FROM products WHERE active=1 ORDER BY name").fetchall()
    models = c.execute("SELECT id, code, name FROM models WHERE active=1 ORDER BY code").fetchall()
    fams = c.execute("SELECT id, name FROM families ORDER BY name").fetchall()
    profs = profiles.load_all(c)
    sups = c.execute("""SELECT supplier FROM catalogs WHERE supplier<>''
                       UNION SELECT supplier FROM products WHERE supplier<>'' ORDER BY supplier""").fetchall()
    versions = c.execute("SELECT * FROM export_versions ORDER BY id DESC LIMIT 30").fetchall()
    reuse = {}
    reuse_id = request.args.get("reuse_id", "")
    if reuse_id:
        try:
            reuse_row = c.execute("SELECT * FROM export_versions WHERE id=?", (int(reuse_id),)).fetchone()
        except ValueError:
            reuse_row = None
        if reuse_row:
            try:
                saved = json.loads(reuse_row["options_json"] or "{}")
                if isinstance(saved, dict):
                    reuse.update(saved)
            except (TypeError, json.JSONDecodeError):
                pass
            reuse.update({
                "doc_type": reuse_row["doc_type"],
                "template_name": reuse_row["template_name"],
                "profile_id": str(reuse_row["profile_id"] or ""),
                "title": reuse_row["title"] or "",
            })
    c.close()
    ids_raw = reuse.get("model_ids") or request.values.get("model_ids", "")
    sel_ids = [x for x in str(ids_raw).split(",") if x.strip().isdigit()]
    default_doc = "product_catalog" if sel_ids else "tds"
    return render_template_string(
        EXPORT_HTML, sel_ids=sel_ids,
        prods=prods, models=models, fams=fams, profs=profs,
        sups=sups,
        templates=list(pdf_engine.TEMPLATES_META.keys()),
        versions=versions,
        reuse=reuse,
        sel_doc=reuse.get("doc_type", request.values.get("doc_type", default_doc)),
        sel_pid=reuse.get("product_id", request.values.get("product_id", "")),
        sel_mid=reuse.get("model_id", request.values.get("model_id", "")),
        sel_fid=reuse.get("family_id", request.values.get("family_id", "")),
        sel_supplier=reuse.get("supplier", request.values.get("supplier", "")),
        sel_profile=reuse.get("profile_id", ""),
        sel_template=reuse.get("template_name", "Corporate"),
    )


def _field(row, key, default=None):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


@bp.route("/scope")
def scope():
    """What would this export contain? Used by the page to warn before exporting (unapproved models, missing approved images)."""
    a = request.args
    ids = [x for x in a.get("model_ids", "").split(",") if x.strip().isdigit()]
    items = collect_items(a.get("doc_type", "tds"), a.get("product_id") or None, a.get("model_id") or None, a.get("family_id") or None,
                          (a.get("supplier") or "").strip() or None, a.get("only_approved") == "1", ids)
    approved, missing = 0, []
    for it in items:
        if _field(it["m"], "status", "approved") == "approved":
            approved += 1
        if not it.get("images"):
            missing.append(str(_field(it["m"], "code", "")))
    return {"n": len(items), "approved": approved, "missing": len(missing), "missing_codes": "، ".join(missing[:5]) + (" ..." if len(missing) > 5 else "")}


@bp.route("/generate", methods=["POST"])
def generate():
    f = request.form
    doc_type = f.get("doc_type", "tds")
    template_name = f.get("template_name", "Corporate")
    profile_id = int(f.get("profile_id") or 0)
    product_id = f.get("product_id") or None
    model_id = f.get("model_id") or None
    family_id = f.get("family_id") or None
    supplier = f.get("supplier", "").strip() or None
    only_approved = f.get("only_approved") == "1"
    model_ids = [x for x in f.get("model_ids", "").split(",") if x.strip().isdigit()]

    c = db.conn()
    prof = profiles.load(c, profile_id)
    items = collect_items(doc_type, product_id, model_id, family_id, supplier, only_approved, model_ids)
    if not items:
        c.close()
        flash("لا توجد بيانات مطابقة لخيارات التصدير المحددة.")
        return redirect("/export-center/")

    opts = {
        "doc_type": doc_type,
        "template_name": template_name,
        "profile_id": str(prof["id"]),
        "product_id": product_id or "",
        "model_id": model_id or "",
        "family_id": family_id or "",
        "supplier": supplier or "",
        "only_approved": "1" if only_approved else "0",
        "model_ids": ",".join(model_ids),
        "title": f.get("title", "").strip(),
        "subtitle": f.get("subtitle", "").strip(),
        "lang": f.get("lang") or prof["lang"],
        "orient": f.get("orient") or prof["orient"],
        "show_supplier": "1" if f.get("show_supplier") else "0",
        "show_specs": "1" if f.get("show_specs") else "0",
        "show_extra_images": "1" if f.get("show_extra_images") else "0",
        "cover": "1" if f.get("cover") else "0",
        "toc": "1" if f.get("toc") else "0",
        "intro_text": f.get("intro_text", "").strip(),
        "tech_notes": f.get("tech_notes", "").strip(),
    }

    pdf_buf = pdf_engine.build_document(items, prof, doc_type=doc_type, template_name=template_name, options=opts)
    pdf_bytes = pdf_buf.getvalue()
    page_count = pdf_engine.page_count(pdf_bytes)
    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    fname = f"{secure_filename(doc_type) or 'export'}_{secure_filename(template_name) or 'template'}_{ts}_{uuid.uuid4().hex[:8]}.pdf"
    is_preview = f.get("mode") == "preview"
    if not is_preview:
        drive_id = None
        try:
            drive_id = storage.save_export_pdf(fname, pdf_bytes)
            c.execute("""INSERT INTO export_versions(doc_type,template_name,profile_id,title,file_name,options_json,page_count,drive_id)
                         VALUES(?,?,?,?,?,?,?,?)""",
                      (doc_type, template_name, prof["id"] or None, opts["title"] or fname, fname,
                       json.dumps(opts, ensure_ascii=False), page_count, drive_id))
            c.commit()
        except Exception:
            c.rollback()
            c.close()
            if drive_id:
                storage.delete(drive_id)
            elif not storage.IS_DRIVE:
                try:
                    os.remove(storage.export_path(fname))
                except OSError:
                    pass
            raise
    c.close()

    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf", as_attachment=not is_preview, download_name=fname)


@bp.route("/download/<int:version_id>")
def download_version(version_id):
    c = db.conn()
    try:
        row = c.execute("SELECT file_name,drive_id FROM export_versions WHERE id=?", (version_id,)).fetchone()
    finally:
        c.close()
    if not row:
        abort(404)
    try:
        fpath = storage.export_path(row["file_name"], row["drive_id"])
    except FileNotFoundError:
        abort(404)
    return send_file(fpath, mimetype="application/pdf", as_attachment=True, download_name=row["file_name"])


EXPORT_HTML = """{% extends "base" %}{% block head %}<style>
.types{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}
.type{display:block;border:2px solid var(--line);border-radius:12px;padding:12px 14px;cursor:pointer;background:#fff;position:relative}
.type input{position:absolute;opacity:0;pointer-events:none}
.type b{display:block;font-size:16px}.type span{color:var(--muted);font-size:13px}
.type.on{border-color:var(--accent);background:#fff6f0}
.type:has(input:focus-visible){outline:3px solid #f2a97a}
.fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px}
.fields label{display:block;font-weight:600;font-size:14px}.fields select,.fields input:not([type=checkbox]),.fields textarea{width:100%;margin-top:4px;font-weight:400}
.chip{display:inline-flex;gap:8px;align-items:center;background:#eef1f7;border-radius:18px;padding:4px 12px;margin-bottom:10px;font-size:14px}
.summary{margin-top:12px;padding:10px 12px;border-radius:10px;background:#eef6ee;border:1px solid #cfe6d1;font-size:15px}
.summary.warn{background:#fff6dd;border-color:#f0d58a}.summary.bad{background:#fdecec;border-color:#f3c3c3}
.summary a{margin-inline-start:6px}
.actionbar{position:sticky;bottom:0;background:#ffffffee;backdrop-filter:blur(4px);border:1px solid var(--line);border-radius:12px;padding:10px 14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:16px}
.actionbar .sp{flex:1}.actionbar button{padding:10px 20px;font-size:15px}
details.card>summary{cursor:pointer;font-weight:600;font-size:17px}
.checks{display:flex;gap:16px;flex-wrap:wrap;margin-top:12px}.checks label{font-weight:400}
@media(max-width:860px){.actionbar{bottom:64px}}
</style>{% endblock %}
{% block body %}
<form id="ec" method="post" action="/export-center/generate">
{% if sel_ids %}<input type="hidden" name="model_ids" value="{{ sel_ids|join(',') }}">{% endif %}
<section class="card"><h3>١. ماذا تريد أن تُصدّر؟</h3>
<div class="types">
 <label class="type"><input type="radio" name="doc_type" value="tds" {{ 'checked' if sel_doc=='tds' }}><b>ورقة بيانات</b><span>صفحة فنية لطراز أو منتج: صورة ومواصفات.</span></label>
 <label class="type"><input type="radio" name="doc_type" value="product_catalog" {{ 'checked' if sel_doc=='product_catalog' }}><b>كتالوج منتج</b><span>عدة طرازات في كتالوج واحد.</span></label>
 <label class="type"><input type="radio" name="doc_type" value="master_catalog" {{ 'checked' if sel_doc=='master_catalog' }}><b>كتالوج شامل</b><span>مرتب حسب التصنيف ثم المنتج، بفواصل.</span></label>
</div></section>

<section class="card"><h3>٢. ما الذي يدخل فيه؟</h3>
{% if sel_ids %}<div class="chip">المحدد من القائمة: <b>{{ sel_ids|length }}</b> طراز <a href="/export-center/">إلغاء التحديد</a></div>{% endif %}
<div class="fields">
 <label data-only="tds">الطراز<select name="model_id"><option value="">كل طرازات المنتج</option>{% for m in models %}<option value="{{ m.id }}" {{ 'selected' if sel_mid==m.id|string }}>{{ m.code }} {{ m.name }}</option>{% endfor %}</select></label>
 <label>المنتج<select name="product_id"><option value="">كل المنتجات</option>{% for p in prods %}<option value="{{ p.id }}" {{ 'selected' if sel_pid==p.id|string }}>{{ p.name }}</option>{% endfor %}</select></label>
 <label data-only="product_catalog master_catalog">التصنيف<select name="family_id"><option value="">كل التصنيفات</option>{% for f in fams %}<option value="{{ f.id }}" {{ 'selected' if sel_fid==f.id|string }}>{{ f.name }}</option>{% endfor %}</select></label>
 <label data-only="product_catalog master_catalog">المورد<select name="supplier"><option value="">كل الموردين</option>{% for s in sups %}<option value="{{ s.supplier }}" {{ 'selected' if sel_supplier==s.supplier }}>{{ s.supplier }}</option>{% endfor %}</select></label>
</div>
<div class="checks" data-only="product_catalog master_catalog"><label><input type="checkbox" name="only_approved" value="1" {{ 'checked' if reuse.get('only_approved')=='1' }}> المعتمد فقط</label></div>
<div id="scope-summary" class="summary" role="status" aria-live="polite">جارٍ حساب ما سيدخل في المستند...</div></section>

<section class="card"><h3>٣. الشكل</h3>
<div class="fields">
 <label>الهوية البصرية<select name="profile_id">{% for pr in profs %}<option value="{{ pr.id }}" {{ 'selected' if sel_profile==pr.id|string }}>{{ pr.name }}</option>{% endfor %}</select></label>
 <label>القالب<select name="template_name">{% for t in templates %}<option value="{{ t }}" {{ 'selected' if sel_template==t }}>{{ t }}</option>{% endfor %}</select></label>
 <label>اللغة<select name="lang"><option value="both" {{ 'selected' if reuse.get('lang','both')=='both' }}>عربي + إنجليزي</option><option value="ar" {{ 'selected' if reuse.get('lang')=='ar' }}>عربي فقط</option><option value="en" {{ 'selected' if reuse.get('lang')=='en' }}>إنجليزي فقط</option></select></label>
 <label>الاتجاه<select name="orient"><option value="portrait" {{ 'selected' if reuse.get('orient','portrait')=='portrait' }}>طولي</option><option value="landscape" {{ 'selected' if reuse.get('orient')=='landscape' }}>عرضي</option></select></label>
</div>
<p class="muted" style="margin:10px 0 0">الهويات والقوالب والخطوط تُعدَّل من <a href="/export-settings">الهوية والقوالب</a>.</p></section>

<details class="card"><summary>٤. العناوين والمحتوى (اختياري)</summary>
<div class="fields" style="margin-top:12px">
 <label>عنوان المستند<input name="title" value="{{ reuse.get('title','') }}" placeholder="مثال: كتالوج ماكينات الحديد 2026"></label>
 <label>العنوان الفرعي<input name="subtitle" value="{{ reuse.get('subtitle','') }}"></label>
 <label>مقدمة<textarea name="intro_text" rows="3">{{ reuse.get('intro_text','') }}</textarea></label>
 <label>ملاحظات فنية<textarea name="tech_notes" rows="3">{{ reuse.get('tech_notes','') }}</textarea></label>
</div>
<div class="checks">
 <label data-only="product_catalog master_catalog"><input type="checkbox" name="cover" value="1" {{ 'checked' if (reuse.get('cover','1') if reuse else '1')=='1' }}> صفحة غلاف</label>
 <label data-only="product_catalog master_catalog"><input type="checkbox" name="toc" value="1" {{ 'checked' if (reuse.get('toc','1') if reuse else '1')=='1' }}> فهرس محتويات</label>
 <label><input type="checkbox" name="show_specs" value="1" {{ 'checked' if (reuse.get('show_specs','1') if reuse else '1')=='1' }}> جدول المواصفات</label>
 <label><input type="checkbox" name="show_extra_images" value="1" {{ 'checked' if (reuse.get('show_extra_images','1') if reuse else '1')=='1' }}> الصور الإضافية والرسومات</label>
 <label><input type="checkbox" name="show_supplier" value="1" {{ 'checked' if (reuse.get('show_supplier','1') if reuse else '1')=='1' }}> إظهار المورد</label>
</div></details>

<div class="actionbar"><button type="submit" class="sec" name="mode" value="preview" formtarget="_blank" id="pv">معاينة</button>
<button type="submit" name="mode" value="export" id="go">تصدير PDF وحفظ الإصدار</button><span class="sp"></span><span class="muted">كل تصدير يُحفظ كإصدار ولا يستبدل ما قبله.</span></div>
</form>

<div class="card"><h3>الإصدارات المصدّرة</h3>
<table><tr><th>التاريخ</th><th>النوع</th><th>القالب</th><th>العنوان</th><th></th></tr>
{% for v in versions %}<tr><td class="ltr">{{ v.created }}</td><td>{{ {'tds':'ورقة بيانات','product_catalog':'كتالوج منتج','master_catalog':'كتالوج شامل'}.get(v.doc_type, v.doc_type) }}</td><td><span class="tag">{{ v.template_name }}</span></td><td>{{ v.title }}</td>
<td><a class="btn sec" href="/export-center/download/{{ v.id }}">تنزيل PDF</a> <a class="btn" href="/export-center/?reuse_id={{ v.id }}">أعد بنفس الإعدادات</a></td></tr>
{% else %}<tr><td colspan="5" class="muted">لا إصدارات بعد. أول تصدير يظهر هنا.</td></tr>{% endfor %}</table></div>
<script>
(function(){
 var f=document.getElementById('ec'),sum=document.getElementById('scope-summary'),go=document.getElementById('go'),pv=document.getElementById('pv'),t0;
 var skip=['title','subtitle','intro_text','tech_notes','profile_id','template_name','lang','orient','cover','toc','show_specs','show_extra_images','show_supplier','mode'];
 function type(){return f.querySelector('input[name=doc_type]:checked').value}
 function sync(){var t=type();
  f.querySelectorAll('[data-only]').forEach(function(el){var on=el.dataset.only.split(' ').indexOf(t)>=0;el.hidden=!on;el.querySelectorAll('select,input').forEach(function(i){i.disabled=!on})});
  f.querySelectorAll('.type').forEach(function(l){l.classList.toggle('on',l.querySelector('input').checked)});
  clearTimeout(t0);t0=setTimeout(scope,120)}
 function say(cls,text,link){sum.className='summary '+cls;sum.textContent=text;if(link){var a=document.createElement('a');a.href=link[0];a.textContent=link[1];sum.appendChild(a)}}
 function scope(){var q=new URLSearchParams(new FormData(f));skip.forEach(function(k){q.delete(k)});
  fetch('/export-center/scope?'+q.toString()).then(function(r){return r.json()}).then(function(d){
   go.disabled=pv.disabled=!d.n;
   if(!d.n){say('bad','لا توجد بيانات مطابقة. غيّر النطاق، أو اعتمد الطرازات أولًا.',['/catalogs','الكتالوجات']);return}
   var n=d.n,nm=n==1?'عنصر واحد':n==2?'عنصران':n<=10?n+' عناصر':n+' عنصرًا';var txt='سيدخل '+nm+'، منها '+d.approved+' معتمد.';
   if(d.missing){say('warn',txt+' '+d.missing+' بلا صورة معتمدة'+(d.missing_codes?' ('+d.missing_codes+')':'')+'.',['/gaps','عرض النواقص']);return}
   if(d.approved<d.n){say('warn',txt+' بعضها لم يُعتمد بعد.',['/models?status=staging','مراجعة غير المعتمد']);return}
   say('',txt+' كلها بصور معتمدة.')}).catch(function(){say('','تعذّر حساب النطاق؛ يمكنك المتابعة.')})}
 f.addEventListener('change',sync);sync();
})();
</script>
{% endblock %}"""
