from shell import BASE

HOME = """{% extends "base" %}{% block head %}{% if busy %}<meta http-equiv="refresh" content="5">{% endif %}
<style>
.home{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);gap:16px;align-items:start}
.home .card{margin-bottom:0}
.sum{color:var(--muted);margin:0 0 14px}
.wq{display:flex;align-items:center;gap:12px;padding:11px 4px;border-bottom:1px solid #e8ebf2;text-decoration:none;color:var(--ink)}
.wq:last-child{border-bottom:0}.wq:hover{background:#f7f9fc}
.wq b{min-width:34px;text-align:center;font-size:18px;border-radius:8px;padding:2px 0;background:#eef1f7}
.wq.bad b{background:#fadcdc;color:var(--bad)}.wq.warn b{background:#fff1c9;color:var(--warn)}
.act{display:block;padding:11px 4px;border-bottom:1px solid #e8ebf2;text-decoration:none;color:var(--ink)}
.act:last-child{border-bottom:0}.act:hover{background:#f7f9fc}.act b{display:block;color:var(--navy)}.act span{color:var(--muted);font-size:14px}
.empty{padding:10px 0;color:var(--muted)}
@media(max-width:860px){.home{grid-template-columns:1fr}}
</style>{% endblock %}
{% block body %}
<p class="sum">{{ n_approved }} طراز معتمد من {{ n_models }} طراز، في {{ n_cats }} كتالوج.</p>
<div class="home" style="margin-bottom:16px">
<section class="card"><h3>ما ينتظر عملك</h3>
{% for w in work %}<a class="wq {{ w.tone }}" href="{{ w.url }}"><b>{{ w.n }}</b><span>{{ w.text }}</span></a>
{% else %}<p class="empty">لا شيء ينتظرك الآن. ارفع كتالوجًا جديدًا أو ابحث عن منتج.</p>{% endfor %}</section>
<section class="card"><h3>ابدأ من هنا</h3>
<a class="act" href="/catalogs#upload"><b>رفع كتالوج جديد</b><span>يستخرج النظام الطرازات والمواصفات والصور تلقائيًا.</span></a>
<a class="act" href="/search"><b>البحث في المنتجات</b><span>تصفح الطرازات المعتمدة بالصور والمواصفات.</span></a>
<a class="act" href="/export-center/"><b>تصدير ورقة بيانات أو كتالوج</b><span>ملفات PDF بهويتك وقوالبك.</span></a>
</section></div>
<section class="card"><h3>آخر الكتالوجات</h3>
<table><tr><th>الملف</th><th>المورد</th><th>الحالة</th><th>الطرازات</th><th>المعتمد</th></tr>
{% for c in cats %}<tr><td class="ltr"><a href="/catalog/{{ c.id }}">{{ c.name }}</a></td><td>{{ c.supplier }}</td>
<td>{% if c.state=='processing' %}<span class="warn">قيد المعالجة {{ c.msg }}</span>{% elif c.state=='checking' %}<span class="warn">فحص التكرار...</span>{% elif c.state=='needs_decision' %}<span class="bad">يتطلب قرارك</span>{% elif c.state=='done' %}<span class="ok">تم</span>{% elif c.state=='stale' %}<span class="bad">توقف؛ أعد المحاولة</span>{% else %}<span class="bad">خطأ</span>{% endif %}</td>
<td>{{ c.models }}</td><td>{{ c.approved }}</td></tr>
{% else %}<tr><td colspan="5" class="muted">لا توجد كتالوجات بعد. ابدأ برفع أول كتالوج.</td></tr>{% endfor %}</table>
<p style="margin:10px 0 0"><a href="/catalogs">كل الكتالوجات</a></p></section>
{% endblock %}"""

INDEX = """{% extends "base" %}{% block head %}{% if busy %}<meta http-equiv="refresh" content="3">{% endif %}{% endblock %}
{% block body %}
<div class="card" id="upload"><h3>رفع كتالوج جديد</h3>
<form method="post" action="/upload" enctype="multipart/form-data">
<input type="file" name="pdf" accept=".pdf" required> <input name="supplier" placeholder="اسم المورد (مثال: Henan Ideal)">
<input name="version" placeholder="الإصدار (اختياري)" size="12" class="ltr"> <input name="catalog_date" placeholder="تاريخ الكتالوج (اختياري) 2025-03" size="16" class="ltr">
<button>بدء الاستخراج</button></form>
<p class="muted">يفحص النظام التكرار تلقائيًا استنادًا إلى تطابق الملف أو محتواه، أو تطابق المورد والإصدار، أو تشابه المنتجات والمواصفات. ويُسمح برفع الإصدار الأحدث وربطه بالإصدار السابق.</p></div>
<div class="card"><h3>الكتالوجات</h3>
<table><tr><th>الملف</th><th>المورد</th><th>الإصدار</th><th>الحالة</th><th>الطرازات</th><th>المعتمد</th><th>بنود المراجعة</th><th>رفعه</th><th></th></tr>
{% for c in cats %}<tr><td class="ltr"><a href="/catalog/{{ c.id }}">{{ c.name }}</a></td><td>{{ c.supplier }}</td><td class="ltr">{{ c.version }}{% if c.catalog_date %} / {{ c.catalog_date }}{% endif %}</td>
<td>{% if c.state=='processing' %}<span class="warn">قيد المعالجة {{ c.msg }}</span>{% elif c.state=='checking' %}<span class="warn">فحص التكرار...</span>{% elif c.state=='needs_decision' %}<span class="bad">⚠ يتطلب قرارك (هل هو مكرر؟)</span>{% elif c.state=='done' %}<span class="ok">تم</span>{% elif c.state=='stale' %}<span class="bad">تتوقف؛ أعد المحاولة</span>{% else %}<span class="bad">خطأ</span>{% endif %}
{% if c.previous_catalog_id %}<div class="muted" style="font-size:12px">إصدار لاحق لـ #{{ c.previous_catalog_id }}</div>{% endif %}</td>
<td><a href="/models?catalog={{ c.id }}">{{ c.models }}</a></td><td>{{ c.approved }}</td><td>{{ c.reviews }}</td><td>{{ c.uploaded_by or '—' }}</td><td style="white-space:nowrap"><a href="/catalog/{{ c.id }}">مسار العمل</a>{% if c.reviews %} · <a href="/review?catalog={{ c.id }}">مراجعة ({{ c.reviews }})</a>{% endif %}{% if c.approved %} · <a href="/export-center/?doc_type=master_catalog&supplier={{ c.supplier|urlencode }}">تصدير</a>{% endif %}</td></tr>
{% else %}<tr><td colspan="9" class="muted">لا توجد كتالوجات بعد.</td></tr>{% endfor %}</table></div>
{% endblock %}"""

MODELS = """{% extends "base" %}{% block body %}
<div class="card"><form method="get">
<input name="q" value="{{ args.get('q','') }}" placeholder="بحث بالكود أو السلسلة">
<select name="family"><option value="">جميع المجموعات</option><option value="none" {{ 'selected' if args.get('family')=='none' }}>غير مصنف</option>
{% for f in fams %}<option value="{{ f.id }}" {{ 'selected' if args.get('family')==f.id|string }}>{{ f.name }}</option>{% endfor %}</select>
<select name="status"><option value="">جميع الحالات</option><option value="staging" {{ 'selected' if args.get('status')=='staging' }}>قيد المراجعة</option><option value="approved" {{ 'selected' if args.get('status')=='approved' }}>معتمد</option></select>
<select name="product"><option value="">جميع المنتجات</option><option value="none" {{ 'selected' if args.get('product')=='none' }}>دون منتج</option>
{% for p in prods %}<option value="{{ p.id }}" {{ 'selected' if args.get('product')==p.id|string }}>{{ p.name }}</option>{% endfor %}</select>
<select name="active"><option value="">النشط</option><option value="off" {{ 'selected' if args.get('active')=='off' }}>المعطّل</option><option value="all" {{ 'selected' if args.get('active')=='all' }}>الكل</option></select>
<select name="catalog"><option value="">جميع الكتالوجات</option>{% for c in cats %}<option value="{{ c.id }}" {{ 'selected' if args.get('catalog')==c.id|string }}>{{ c.name }}</option>{% endfor %}</select>
<button class="sec">تصفية</button></form></div>
<form method="post" action="/models/bulk"><div class="card">
<table><tr><th></th><th>الكود</th><th>السلسلة</th><th>المنتج</th><th>المجموعة</th><th>صفحة</th><th>مواصفات</th><th>صور</th><th>الحالة</th><th>ظاهر</th><th></th></tr>
{% for m in rows %}<tr><td><input type="checkbox" name="ids" value="{{ m.id }}"></td>
<td class="ltr"><a href="/model/{{ m.id }}"><b>{{ m.code }}</b></a></td><td class="ltr" style="font-size:12px">{{ m.series }}</td>
<td>{{ m.pname or '—' }}</td><td>{{ m.fam or '—' }}</td><td>{{ m.page }}</td><td>{{ m.nspec }}</td><td>{{ m.nimg }}</td>
<td>{% if m.status=='approved' %}<span class="tag g">معتمد</span>{% else %}<span class="tag y">مراجعة</span>{% endif %}{% if not m.active %} <span class="tag r">معطّل</span>{% endif %}</td>
<td>{{ '✔' if m.visible else '✖' }}</td><td><a href="/export-center/?doc_type=tds&model_id={{ m.id }}">ورقة بيانات</a></td></tr>{% endfor %}</table></div>
<div class="card"><b>إجراءات على العناصر المحددة:</b>
<select name="family_id"><option value="">— دون —</option>{% for f in fams %}<option value="{{ f.id }}">{{ f.name }}</option>{% endfor %}</select>
<button name="action" value="family">نقل للمجموعة</button>
<button type="button" class="sec" onclick="var v=Array.prototype.map.call(document.querySelectorAll('input[name=ids]:checked'),function(x){return x.value}).join(',');if(!v){alert('اختر طرازين على الأقل');return}location.href='/compare?ids='+v">مقارنة المحدد</button>
<button type="button" class="sec" onclick="var v=Array.prototype.map.call(document.querySelectorAll('input[name=ids]:checked'),function(x){return x.value}).join(',');if(!v){alert('اختر طرازًا واحدًا على الأقل');return}document.getElementById('goIds').value=v;document.getElementById('goForm').submit()">تصدير المحدد...</button>
<label><input type="checkbox" name="with_images" checked> اعتماد الصور المرشحة أيضًا</label>
<button name="action" value="approve">اعتماد</button><button class="sec" name="action" value="unapprove">إلغاء الاعتماد</button>
<select name="product_id"><option value="">— دون منتج —</option>{% for p in prods %}<option value="{{ p.id }}">{{ p.name }}</option>{% endfor %}</select>
<button name="action" value="product">نقل للمنتج</button>
<button class="sec" name="action" value="disable">تعطيل</button><button class="sec" name="action" value="enable">تفعيل</button>
<button class="sec" name="action" value="hide">إخفاء من الكتالوج</button><button class="sec" name="action" value="show">إظهار</button></div></form>
<form id="goForm" method="post" action="/export-center/"><input type="hidden" name="model_ids" id="goIds"></form>{% endblock %}"""

TREE_NODE = """{% macro modrow(m) %}<li class="ltr"><a href="/model/{{ m.id }}">{{ m.code }}</a> <span class="muted" style="font-size:12px">{{ m.series }}</span>
{% if m.status=='approved' %}<span class="tag g">معتمد</span>{% else %}<span class="tag y">مراجعة</span>{% endif %}
{% if not m.visible %}<span class="tag r">مخفي</span>{% endif %}{% if not m.active %}<span class="tag r">معطّل</span>{% endif %}</li>{% endmacro %}
{% macro prod(pp) %}<li><b>📦 {{ pp.p.name }}</b>{% if pp.p.suggested %} <span class="tag y">مقترح</span>{% endif %}
<a class="btn sec" href="/product/{{ pp.p.id }}">إدارة</a> <a class="btn sec" href="/catalog.pdf?product={{ pp.p.id }}">كتالوج PDF</a>
<ul class="tree">{% for m in pp.models %}{{ modrow(m) }}{% endfor %}</ul></li>{% endmacro %}
{% macro node(n) %}<li><b>📁 {{ n.f.name }}</b>
<form method="post" action="/family/{{ n.f.id }}/rename" style="display:inline"><input name="name" value="{{ n.f.name }}" size="14"><button class="sec">تعديل</button></form>
<form method="post" action="/family/{{ n.f.id }}/delete" style="display:inline"><button class="sec">حذف</button></form>
<a class="btn sec" href="/catalog.pdf?family={{ n.f.id }}">كتالوج PDF</a>
<ul class="tree">{% for pp in n.prods %}{{ prod(pp) }}{% endfor %}{% for m in n.models %}{{ modrow(m) }}{% endfor %}
{% for k in n.kids %}{{ node(k) }}{% endfor %}</ul></li>{% endmacro %}"""

TREE = """{% extends "base" %}{% from "treenode" import node, prod, modrow %}{% block body %}
<div class="card"><h3>شجرة المنتجات</h3>
<ul class="tree">{% for n in tree %}{{ node(n) }}{% endfor %}
{% for pp in free_prods %}{{ prod(pp) }}{% endfor %}
{% if free %}<li><b>📂 طرازات دون منتج ولا مجموعة</b> <span class="muted">({{ free|length }})</span><ul class="tree">
{% for m in free %}<li class="ltr"><a href="/model/{{ m.id }}">{{ m.code }}</a></li>{% endfor %}</ul></li>{% endif %}</ul></div>
<div class="card"><form method="post" action="/family/add">
<input name="name" placeholder="مجموعة جديدة" required>
<select name="parent_id"><option value="">(رئيسية)</option>{% for f in fams %}<option value="{{ f.id }}">داخل: {{ f.name }}</option>{% endfor %}</select>
<button>إضافة</button></form>
<form method="post" action="/families/suggest" style="margin-top:8px"><button class="sec">اقتراح تصنيف مبدئي بالبادئة (GQ, GW, ...)</button></form></div>
<div class="card"><a class="btn" href="/master.pdf">الكتالوج الرئيسي PDF (كل المعتمد والظاهر)</a></div>
{% endblock %}"""

MODEL = """{% extends "base" %}{% block body %}
<div class="crumb"><a href="/catalogs">الكتالوجات</a><span>›</span><a href="/catalog/{{ m.catalog_id }}" class="ltr">{{ m.cname }}</a><span>›</span><b class="ltr">{{ m.code }}</b><span class="sp"></span>
{% if prev_m %}<a class="btn sec" href="/model/{{ prev_m.id }}" title="الطراز السابق في الكتالوج">{{ prev_m.code }} ‹</a>{% endif %}{% if next_m %}<a class="btn sec" href="/model/{{ next_m.id }}" title="الطراز التالي في الكتالوج">› {{ next_m.code }}</a>{% endif %}
{% if m.product_id %}<a class="btn sec" href="/product/{{ m.product_id }}">المنتج</a>{% endif %}<a class="btn sec" href="/compare?ids={{ m.id }}">قارن</a>{% if m.product_id %}<a class="btn sec" href="/product/{{ m.product_id }}?tab=video&model={{ m.id }}">فيديو</a>{% endif %}<a class="btn" href="/export-center/?doc_type=tds&model_id={{ m.id }}">ورقة بيانات</a></div>
<form method="post" action="/model/{{ m.id }}/save"><input type="hidden" name="version" value="{{ m.version or 1 }}">
<div class="card"><h2 class="ltr" style="margin:0">{{ m.code }}
{% if m.status=='approved' %}<span class="tag g">معتمد</span>{% else %}<span class="tag y">قيد المراجعة</span>{% endif %}{% if not m.active %} <span class="tag r">معطّل</span>{% endif %}</h2>
<div class="muted ltr">{{ m.series }} — {{ m.supplier }} — {{ m.cname }} — page {{ m.page }}</div><br>
<label>الاسم العربي: <input name="ar_name" value="{{ m.ar_name }}" size="30"></label>
<label>المنتج: <select name="product_id"><option value="">— دون منتج —</option>
{% for p in prods %}<option value="{{ p.id }}" {{ 'selected' if m.product_id==p.id }}>{{ p.name }}</option>{% endfor %}</select></label>
{% if not m.product_id %}<label>المجموعة: <select name="family_id"><option value="">— غير مصنف —</option>
{% for f in fams %}<option value="{{ f.id }}" {{ 'selected' if m.family_id==f.id }}>{{ f.name }}</option>{% endfor %}</select></label>{% else %}<span class="muted">يُحدَّد تصنيف الطراز من المنتج</span>{% endif %}
<label><input type="checkbox" name="visible" {{ 'checked' if m.visible }}> ظاهر في الكتالوج</label></div>

<div class="card"><h3>المواصفات</h3><p class="muted">تُحفظ قيمة المورد كما هي. أدخل قيمة الشركة في خانة «قيمتك» لتظهر في المستند. احذف هذه القيمة للعودة إلى قيمة المورد.</p>
<table><tr><th>الخاصية</th><th>قيمة المورد</th><th>قيمتك</th><th>القاعدة</th><th>إخفاء من العرض</th></tr>
{% for s in specs %}<tr><td class="ltr">{{ s.attr }}</td><td class="ltr">{{ s.supplier_value }}{% if s.source_page %} <a href="/catalog/{{ m.catalog_id }}/page/{{ s.source_page }}.png" target="_blank" title="صفحة المصدر في الكتالوج" style="font-size:11px">p{{ s.source_page }}</a>{% endif %}{% if s.verified==0 %} <span class="tag y" title="لم يُعثر على القيمة في نص صفحة المصدر؛ يُرجى مراجعتها">راجع</span>{% endif %}</td>
<td><input class="ltr" name="cv_{{ s.id }}" value="{{ s.company_value }}" size="26"></td>
<td>{% if s.rule=='FREQ' %}<span class="tag y">تردد</span>{% elif s.rule=='MANUAL' %}<span class="tag g">يدوي</span>{% endif %}</td>
<td><input type="checkbox" name="hid_{{ s.id }}" {{ 'checked' if s.hidden }}></td></tr>{% endfor %}
{% for k in range(5) %}<tr><td><input name="new_attr_{{ k }}" placeholder="خاصية جديدة"></td><td></td><td><input class="ltr" name="new_val_{{ k }}" placeholder="قيمتها"></td><td></td><td></td></tr>{% endfor %}</table></div>

<div class="card"><h3>الصور</h3>
<div class="imgs">{% for l in links %}<div class="im"><img src="/img/{{ l.file }}"><div class="muted ltr" style="font-size:12px">{{ l.w }}×{{ l.h }} px — p{{ l.page }}</div>
<div style="font-size:12px">ثقة الربط: <b>{{ l.confidence }}</b></div>
{% set d = (l.w / (sheet_w_cm/2.54))|round|int %}<div style="font-size:12px" class="{{ 'ok' if d>=300 else 'warn' if d>=150 else 'bad' }}">دقتها في ورقة البيانات: {{ d }} dpi</div>
<label style="font-size:13px"><input type="radio" name="primary" value="{{ l.id }}" {{ 'checked' if l.is_primary }}> رئيسية</label><br>
<label style="font-size:13px"><input type="checkbox" name="okc_{{ l.id }}" {{ 'checked' if l.ok_catalog }}> معتمدة للكتالوج</label><br>
<label style="font-size:13px"><input type="checkbox" name="oks_{{ l.id }}" {{ 'checked' if l.ok_sheet }}> معتمدة لورقة البيانات</label><br>
<button class="sec" formaction="/model/{{ m.id }}/unlink/{{ l.id }}" formnovalidate>فك الربط</button></div>{% else %}<span class="muted">لا توجد صور مرتبطة.</span>{% endfor %}</div>
{% if free %}<h4>صور من الصفحة نفسها غير مرتبطة بالطراز:</h4><div class="imgs">{% for i in free %}<div class="im"><img src="/img/{{ i.file }}">
<button class="sec" formaction="/model/{{ m.id }}/link" name="image_id" value="{{ i.id }}" formnovalidate>ربط بهذا الطراز</button></div>{% endfor %}</div>{% endif %}</div>

<div class="card"><button name="action" value="save">حفظ</button>
<label><input type="checkbox" name="with_images" checked> اعتماد الصور المرشحة</label>
<button name="action" value="approve">حفظ واعتماد</button>
{% if m.status=='approved' %}<button class="sec" name="action" value="unapprove">إلغاء الاعتماد</button>{% endif %}
<br><br>{% if m.active %}<input name="reason" placeholder="سبب التعطيل (اختياري)"><button class="red" name="action" value="disable">تعطيل الطراز (دون حذف)</button>
{% else %}<button name="action" value="enable">إعادة تفعيل الطراز</button>{% endif %}
<a class="btn sec" target="_blank" href="/datasheet/{{ m.id }}.pdf">ورقة البيانات PDF</a>
{% if m.status!='approved' %}<span class="muted">(تظهر علامة DRAFT قبل الاعتماد)</span>{% endif %}</div>
</form>
<div class="card"><h3>سجل التغييرات</h3><h4>سجل تغييرات المواصفات (المصدر الأصلي غير قابل للتعديل)</h4><table><tr><th>الوقت</th><th>المستخدم</th><th>العملية</th><th>الخاصية</th><th>قيمة المصدر</th><th>قبل</th><th>بعد</th></tr>
{% for h in spec_hist %}<tr><td class="ltr" style="font-size:12px">{{ h.ts }}</td><td>{{ h.username or '—' }}</td><td>{{ h.action }}</td><td class="ltr">{{ h.attr }}</td><td class="ltr">{{ h.source_value }}</td><td class="ltr">{{ h.old_value }}</td><td class="ltr">{{ h.new_value }}</td></tr>
{% else %}<tr><td colspan="7" class="muted">لا توجد تعديلات على المواصفات.</td></tr>{% endfor %}</table><br>
<table><tr><th>الوقت</th><th>المستخدم</th><th>الإجراء</th><th>التفاصيل</th></tr>
{% for h in hist %}<tr><td class="ltr" style="font-size:12px">{{ h.ts }}</td><td>{{ h.username or '—' }}</td><td>{{ h.action }}</td><td class="ltr">{{ h.detail }}</td></tr>
{% else %}<tr><td colspan="4" class="muted">لا توجد تغييرات بعد.</td></tr>{% endfor %}</table></div>{% endblock %}"""

REVIEW = """{% extends "base" %}{% block body %}
<div class="card"><h3>المراجعة{% if cat %} <span class="muted ltr" style="font-size:14px">{{ cat.name }}</span> <a class="btn sec" href="/review" style="padding:3px 10px;font-size:13px">كل الكتالوجات</a>{% endif %}</h3><p class="muted">لا يفترض النظام وجود علاقات غير مؤكدة؛ وتظهر هنا جميع الحالات التي تتطلب المراجعة. <a href="/review{{ '' if show else '?all=1' }}">{{ 'المفتوحة فقط' if show else 'عرض الكل' }}</a></p>
<table><tr><th>النوع</th><th>المرجع</th><th>الملاحظة</th><th></th></tr>
{% for r in rows %}<tr><td class="ltr"><span class="tag {{ 'r' if r.kind in ('NO_TABLE','IMAGE_RENDERED','SIMILAR_CATALOG','PAGE_ERROR') else 'y' }}">{{ r.kind }}</span></td><td class="ltr">{{ r.ref }}{% if r.catalog_id %} <a href="/catalog/{{ r.catalog_id }}">#{{ r.catalog_id }}</a>{% if r.ref and r.ref.startswith('page ') %} <a href="/catalog/{{ r.catalog_id }}/page/{{ r.ref[5:] }}.png" target="_blank">عرض الصفحة</a>{% endif %}{% endif %}</td><td>{{ r.note }}</td>
<td>{% if r.resolved %}<span class="ok">تم</span>{% else %}<form method="post" action="/review/{{ r.id }}/resolve"><button class="sec">تم الحل</button></form>{% endif %}</td></tr>
{% else %}<tr><td colspan="4" class="muted">لا توجد بنود.</td></tr>{% endfor %}</table></div>{% endblock %}"""

RULES = """{% extends "base" %}{% block body %}
<div class="card"><h3>قاعدة التردد</h3>
<p>يجب أن تعمل جميع المعدات على التردد نفسه. وتُعدّل القاعدة قيمة الشركة فقط، مع الحفاظ على قيمة المورد كما هي.</p>
<form method="post"><label>التردد المطلوب: <input name="hz" value="{{ hz }}" size="4"> Hz</label>
<button name="do" value="apply">تطبيق على كل الطرازات</button><button class="sec" name="do" value="clear">إلغاء القاعدة</button></form>
<p>المطبقة حاليًا: <b>{{ aff.n }}</b> مواصفة في <b>{{ aff.m }}</b> طراز.</p>
<p class="warn">⚠ لا يؤدي تغيير التردد في الوصف إلى تغيير سرعة المحرك أو معدلات القطع والثني. وتتوفر مواصفات السرعة لـ{{ speed.n }} طرازًا؛ يُرجى تأكيد القيم مع المورد قبل تصدير مستند للعميل.</p></div>{% endblock %}"""

SETTINGS = """{% extends "base" %}{% block body %}
<div class="card"><h3>هوية المستندات</h3><form method="post" enctype="multipart/form-data">
<p><label>اسم الشركة: <input name="company" value="{{ s.company }}" size="30"></label></p>
<p><label>سطر التواصل (هاتف/موقع): <input name="contact" value="{{ s.contact }}" size="40"></label></p>
<p><label>اللون الأساسي: <input name="color" value="{{ s.color or '#1F3864' }}" size="9"></label></p>
<p><label>علامة مائية ثابتة (اختياري): <input name="watermark" value="{{ s.watermark }}" size="16"></label></p>
<p><label><input type="checkbox" name="show_supplier" value="1" {{ 'checked' if s.show_supplier=='1' }}> إظهار اسم المورد في المستندات</label></p>
<p><label>الشعار: <input type="file" name="logo" accept="image/*"></label> {% if s.logo %}<span class="ok">موجود</span>{% endif %}</p>
<button>حفظ</button></form></div>{% endblock %}"""

SYNC = """{% extends "base" %}{% block body %}
<div class="card"><h3>تقرير Google Sheets (اتجاه واحد: من قاعدة البيانات المركزية إلى الجدول)</h3>
<p>تُنقل الطرازات <b>المعتمدة</b> فقط ({{ info.n }} حاليًا)، مع مواصفاتها وصورها المعتمدة. ستُستبدل أي تعديلات تُجرى على الجدول عند المزامنة التالية؛ لذا أجرِ التعديلات من النظام.</p>
{% if not info.has_cred %}<p class="bad">لم يُعد اتصال Google على الخادم (راجع README_CLOUD_AR.md).</p>{% endif %}
<form method="post"><button {{ 'disabled' if not info.has_cred }}>مزامنة الآن</button></form>
{% if info.drive_mode %}<p class="muted">الصور محفوظة أصلًا في Google Drive، وتُضاف معرّفاتها إلى الجدول.</p>{% endif %}
{% if info.last %}<p>آخر مزامنة: <b>{{ info.last }}</b></p>{% endif %}
{% if info.sheet %}<p><a class="btn sec" target="_blank" href="https://docs.google.com/spreadsheets/d/{{ info.sheet }}">فتح الشيت</a>
<a class="btn sec" target="_blank" href="https://drive.google.com/drive/folders/{{ info.folder }}">فولدر الصور</a></p>{% endif %}
{% if res %}<p class="ok">{{ res.models }} طراز، {{ res.specs }} مواصفة، {{ res.images }} صورة (منها {{ res.uploaded }} صورة مرفوعة حديثًا).</p>{% endif %}
</div>{% endblock %}"""

PRODUCTS = """{% extends "base" %}{% block body %}
<div class="card"><h3>المنتجات الرئيسية</h3>
<p class="muted">المنتج هو العنصر الرئيس، وتتبع له الطرازات. {% if loose %}<b class="warn">{{ loose }} طرازًا لا يزال دون منتج.</b>{% endif %}</p>
<table><tr><th>المنتج</th><th>المجموعة</th><th>طرازات</th><th>معتمد</th><th></th></tr>
{% for p in rows %}<tr><td><b>{{ p.name }}</b>{% if p.suggested %} <span class="tag y">مقترح</span>{% endif %}<div class="muted">{{ p.ar_name }}</div></td>
<td>{{ p.fam or '—' }}</td><td>{{ p.n }}</td><td>{{ p.appr }}</td><td><a class="btn sec" href="/product/{{ p.id }}">إدارة</a></td></tr>
{% else %}<tr><td colspan="5" class="muted">لا توجد منتجات بعد. اختر «اقتراح» أو أضف منتجًا.</td></tr>{% endfor %}</table></div>
<div class="card"><form method="post" action="/product/add"><input name="name" placeholder="اسم المنتج (EN)" required>
<input name="ar_name" placeholder="الاسم العربي"> <select name="family_id"><option value="">— المجموعة —</option>
{% for f in fams %}<option value="{{ f.id }}">{{ f.name }}</option>{% endfor %}</select><button>إضافة منتج رئيسي</button></form>
<form method="post" action="/products/suggest" style="margin-top:8px;display:inline"><button class="sec">اقتراح منتجات من الطرازات غير المرتبطة بمنتج</button></form> <a class="btn sec" href="/products/merge">مقترحات دمج منتجات الموردين</a> <a class="btn sec" href="/products/archived">المؤرشفة</a>
<p class="muted">تجمع الاقتراحات الطرازات التي تتشارك بادئة الرمز والسلسلة (مثل GQ Standard). حدّد أسماء المنتجات الفعلية ثم اعتمدها.</p></div>{% endblock %}"""

PRODUCT = """{% extends "base" %}{% block body %}
<div class="crumb"><a href="/products">المنتجات</a><span>›</span><b>{{ p.name }}</b><span class="sp"></span>
<a class="btn sec" href="/models?product={{ p.id }}">الطرازات</a><a class="btn sec" href="/export-center/?doc_type=product_catalog&product_id={{ p.id }}">كتالوج المنتج</a><a class="btn" href="/export-center/?doc_type=tds&product_id={{ p.id }}">ورقة بيانات</a></div>
<form method="post" action="/product/{{ p.id }}/save"><input type="hidden" name="version" value="{{ p.version or 1 }}"><div class="card"><h2 style="margin:0">📦 {{ p.name }} {% if p.suggested %}<span class="tag y">مقترح: راجعه ثم اعتمده</span>{% endif %}</h2><br>
<p><label>الاسم: <input name="name" value="{{ p.name }}" size="32"></label> <label>الاسم العربي: <input name="ar_name" value="{{ p.ar_name }}" size="28"></label></p>
<p><label>المجموعة: <select name="family_id"><option value="">— دون —</option>{% for f in fams %}<option value="{{ f.id }}" {{ 'selected' if p.family_id==f.id }}>{{ f.name }}</option>{% endfor %}</select></label></p>
<p><label>الوصف:<br><textarea name="description" rows="3" cols="60">{{ p.description }}</textarea></label></p>
<button>حفظ وتأكيد</button> <a class="btn sec" href="/catalog.pdf?product={{ p.id }}">كتالوج المنتج PDF</a></div></form>
<div class="card"><h3>الطرازات ({{ ms|length }})</h3><table><tr><th>الكود</th><th>السلسلة</th><th>الحالة</th><th>مواصفات</th><th>صور معتمدة</th><th>نقل لمنتج آخر</th></tr>
{% for m in ms %}<tr><td class="ltr"><a href="/model/{{ m.id }}"><b>{{ m.code }}</b></a></td><td class="ltr" style="font-size:12px">{{ m.series }}</td>
<td>{% if m.status=='approved' %}<span class="tag g">معتمد</span>{% else %}<span class="tag y">مراجعة</span>{% endif %}{% if not m.active %} <span class="tag r">معطّل</span>{% endif %}</td>
<td>{{ m.nspec }}</td><td>{{ m.nimg }}</td>
<td><form method="post" action="/models/bulk" style="display:inline"><input type="hidden" name="ids" value="{{ m.id }}"><input type="hidden" name="action" value="product">
<select name="product_id"><option value="">— دون منتج —</option>{% for x in prods %}<option value="{{ x.id }}">{{ x.name }}</option>{% endfor %}</select><button class="sec">نقل</button></form></td></tr>
{% else %}<tr><td colspan="6" class="muted">المنتج فاضي.</td></tr>{% endfor %}</table></div>
<div class="card"><h3>إضافة طراز يدويًا</h3><form method="post" action="/product/{{ p.id }}/addmodel"><input name="code" placeholder="كود الطراز" required><input name="series" placeholder="السلسلة (اختياري)"><button>إضافة</button></form></div>
<div class="card"><form method="post" action="/product/{{ p.id }}/move" style="display:inline"><select name="target"><option value="">— دون منتج —</option>{% for x in prods %}<option value="{{ x.id }}">{{ x.name }}</option>{% endfor %}</select><button class="sec">نقل كل الطرازات</button></form>
<form method="post" action="/product/{{ p.id }}/archive" style="display:inline"><button class="red">أرشفة المنتج (لو فاضي)</button></form></div>
<div class="card"><h3>سجل المنتج</h3><table>{% for h in hist %}<tr><td class="ltr" style="font-size:12px">{{ h.ts }}</td><td>{{ h.username or '—' }}</td><td>{{ h.action }}</td><td class="ltr">{{ h.detail }}</td></tr>{% else %}<tr><td class="muted">لا توجد سجلات بعد.</td></tr>{% endfor %}</table></div>{% endblock %}"""

GAPS = """{% extends "base" %}{% block body %}
<div class="card"><h3>النواقص ({{ rows|length }} من {{ total }} طراز نشط)</h3>
<p>{% for k,v in kinds.items() %}<span class="tag y">{{ k }}: {{ v }}</span> {% endfor %}</p>
<table><tr><th>الطراز</th><th>المنتج</th><th>نواقص البيانات</th></tr>
{% for m,iss in rows %}<tr><td class="ltr"><a href="/model/{{ m.id }}"><b>{{ m.code }}</b></a> <span class="muted" style="font-size:12px">{{ m.series }}</span></td><td>{{ m.pname or '—' }}</td>
<td>{% for i in iss %}<span class="tag {{ 'r' if i in ('دون مواصفات','دون صورة معتمدة','دون منتج') else 'y' }}">{{ i }}</span> {% endfor %}</td></tr>
{% else %}<tr><td colspan="3" class="ok">لا توجد نواقص.</td></tr>{% endfor %}</table></div>{% endblock %}"""
SEARCH = """{% extends "base" %}{% block body %}
<div class="card"><form method="get">
<input name="q" value="{{ a.get('q','') }}" placeholder="بحث: كود، اسم، عربي، أي مواصفة (مثال: 380V 4kW)" size="44" autofocus> <button>🔎 بحث</button>
<details {{ 'open' if a.get('family') or a.get('product') or a.get('supplier') or a.get('a1') or a.get('status')=='all' else '' }} style="margin-top:8px"><summary>بحث متقدم</summary><div style="margin-top:8px">
<select name="family"><option value="">جميع المجموعات</option>{% for f in fams %}<option value="{{ f.id }}" {{ 'selected' if a.get('family')==f.id|string }}>{{ f.name }}</option>{% endfor %}</select>
<select name="product"><option value="">جميع المنتجات</option>{% for p in prods %}<option value="{{ p.id }}" {{ 'selected' if a.get('product')==p.id|string }}>{{ p.name }}</option>{% endfor %}</select>
<select name="supplier"><option value="">جميع الموردين</option>{% for s in sups %}<option {{ 'selected' if a.get('supplier')==s }}>{{ s }}</option>{% endfor %}</select>
<select name="status"><option value="approved" {{ 'selected' if a.get('status','approved')=='approved' }}>المعتمد</option><option value="all" {{ 'selected' if a.get('status')=='all' }}>الكل</option></select>
<select name="sort"><option value="code">ترتيب بالكود</option><option value="product" {{ 'selected' if a.get('sort')=='product' }}>بالمنتج</option><option value="new" {{ 'selected' if a.get('sort')=='new' }}>الأحدث</option></select>
<datalist id="attrs">{% for x in attrs %}<option value="{{ x }}">{% endfor %}</datalist>
{% for k in (1,2,3) %}<div style="margin-top:6px"><input name="a{{ k }}" list="attrs" value="{{ a.get('a'~k,'') }}" placeholder="خاصية (مثال: Motor power)">
<select name="o{{ k }}">{% for v,l in (('contains','يحتوي'),('gte','≥'),('lte','≤'),('eq','=')) %}<option value="{{ v }}" {{ 'selected' if a.get('o'~k)==v }}>{{ l }}</option>{% endfor %}</select>
<input name="v{{ k }}" value="{{ a.get('v'~k,'') }}" placeholder="القيمة" size="12"></div>{% endfor %}
<p class="muted">يقارن الشرطان ≥ و≤ أول رقم في قيمة الخاصية (مثال: 3 kW).</p></div></details></form></div>
<form method="post" action="/export/catalog" target="_blank" id="res"><div class="card bar" style="position:sticky;top:0;z-index:5;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
<b>{{ total }} نتيجة</b><label><input type="checkbox" onclick="document.querySelectorAll('.pick').forEach(function(x){x.checked=this.checked}.bind(this))"> تحديد الكل</label>
<select name="profile" id="prof" title="الهوية المستخدمة لورقة البيانات السريعة">{% for p in profs %}<option value="{{ p.id }}" {{ 'selected' if p.id==active }}>{{ p.name }}</option>{% endfor %}</select>
<input type="hidden" name="all_ids" value="{{ all_ids }}">
<span style="flex:1"></span>
<button type="button" class="sec" id="go-cmp">قارن المحدد</button><button type="button" id="go-sel">تصدير المحدد...</button><button type="button" class="sec" id="go-all">تصدير كل النتائج...</button>
<details style="flex-basis:100%"><summary class="muted">تصدير سريع بدون خيارات</summary><div style="margin-top:8px"><input name="title" placeholder="عنوان الكتالوج (اختياري)"> <button name="mode" value="selected">كتالوج PDF للمحدد</button> <button class="sec" name="mode" value="all">كتالوج PDF لكل النتائج</button></div></details></div>
<div class="imgs">{% for r in res %}{% set m = r.m %}<div class="im" style="width:235px">
{% if r.thumb %}<img src="/img/{{ r.thumb }}">{% else %}<div class="muted" style="height:150px">دون صورة</div>{% endif %}
<label><input class="pick" type="checkbox" name="ids" value="{{ m.id }}"> <b class="ltr">{{ m.code }}</b></label>
<div class="muted" style="font-size:12px">{{ m.pname or '' }} {{ m.series }}</div>{% if m.ar_name %}<div>{{ m.ar_name }}</div>{% endif %}
<div class="ltr" style="font-size:12px;margin:4px 0">{% for x,y in r.specs %}<div>{{ x }}: {{ y }}</div>{% endfor %}</div>
{% if m.status!='approved' %}<span class="tag y">غير معتمد</span>{% endif %}
<div style="margin-top:6px"><a class="btn sec ds" data-id="{{ m.id }}" target="_blank" href="/datasheet/{{ m.id }}.pdf">📄 داتا شيت</a> <a href="/model/{{ m.id }}">فتح</a>{% if m.catalog_id %} · <a href="/catalog/{{ m.catalog_id }}/page/{{ m.page }}.png" target="_blank" title="صفحة الكتالوج الأصلية">الصفحة {{ m.page }}</a>{% endif %}</div></div>
{% else %}<p class="muted">لا توجد نتائج.</p>{% endfor %}</div></form>
<form id="goForm" method="post" action="/export-center/"><input type="hidden" name="model_ids" id="goIds"></form>
<script>var P=document.getElementById('prof');function upd(){document.querySelectorAll('a.ds').forEach(function(x){x.href='/datasheet/'+x.dataset.id+'.pdf?profile='+P.value})}P.onchange=upd;upd();
function go(v){if(!v){alert('اختر طرازًا واحدًا على الأقل');return}document.getElementById('goIds').value=v;document.getElementById('goForm').submit()}
document.getElementById('go-sel').onclick=function(){go(Array.prototype.map.call(document.querySelectorAll('.pick:checked'),function(x){return x.value}).join(','))};
document.getElementById('go-all').onclick=function(){go(document.querySelector('[name=all_ids]').value)};
document.getElementById('go-cmp').onclick=function(){var v=Array.prototype.map.call(document.querySelectorAll('.pick:checked'),function(x){return x.value}).join(',');if(!v){alert('اختر طرازين على الأقل');return}location.href='/compare?ids='+v};</script>
{% endblock %}"""

EXSETTINGS = """{% extends "base" %}{% macro o(v,l,cur) %}<option value="{{ v }}" {{ 'selected' if v==cur }}>{{ l }}</option>{% endmacro %}{% block body %}
{% if not arabic_ok %}<div class="flash">مكتبتا arabic-reshaper وpython-bidi غير مثبتتين؛ لذلك قد لا يظهر النص العربي في PDF على نحو صحيح. شغّل run.bat لتثبيتهما.</div>{% endif %}
<div class="card"><b>الهويات:</b> {% for x in allp %}<a class="btn {{ '' if x.id==p.id else 'sec' }}" href="/export-settings?pid={{ x.id }}">{{ x.name }}{{ ' ★' if x.id==active }}</a> {% endfor %}
<form method="post" action="/export-settings/profile" style="margin-top:8px"><input type="hidden" name="pid" value="{{ p.id }}">
<input name="name" placeholder="اسم هوية جديدة"><button name="do" value="new" class="sec">نسخ كهوية جديدة</button>
<button name="do" value="activate" class="sec">اجعلها الافتراضية ★</button><button name="do" value="delete" class="red">حذف</button></form></div>
<form method="post" action="/export-settings/save" enctype="multipart/form-data"><input type="hidden" name="pid" value="{{ p.id }}">
<div class="card"><h3>الهوية</h3><p><label>اسم الهوية: <input name="name" value="{{ p.name }}"></label> <label>اسم الشركة: <input name="company" value="{{ p.company }}" size="26"></label></p>
<p><label>سطر التواصل: <input name="contact" value="{{ p.contact }}" size="46"></label> <label>روابط التواصل: <input name="social_links" value="{{ p.social_links }}" size="40"></label></p>
<p><label>الشعار: <input type="file" name="logo" accept="image/*"></label> {% if p.logo %}<span class="ok">موجود</span> <label><input type="checkbox" name="remove_logo"> إزالة</label>{% endif %}</p>
<p><label><input type="checkbox" name="show_supplier" {{ 'checked' if p.show_supplier=='1' }}> اسم المورد</label>
<label><input type="checkbox" name="show_date" {{ 'checked' if p.show_date=='1' }}> التاريخ</label>
<label><input type="checkbox" name="show_page" {{ 'checked' if p.show_page=='1' }}> رقم الصفحة</label>
<label><input type="checkbox" name="show_desc" {{ 'checked' if p.show_desc=='1' }}> وصف المنتج في الداتا شيت</label></p></div>
<div class="card"><h3>اللغة والخطوط</h3>
<p><label>لغة عناوين المستند: <select name="lang">{{ o('en','English',p.lang) }}{{ o('ar','العربية',p.lang) }}{{ o('both','كلاهما',p.lang) }}</select></label></p>
<p><label>الخط الإنجليزي: <select name="font_en">{{ o('auto','تلقائي',p.font_en) }}{% for f in fonts_en %}{{ o(f,f,p.font_en) }}{% endfor %}</select></label>
<label>الخط العربي: <select name="font_ar">{{ o('auto','تلقائي',p.font_ar) }}{% for f in fonts_ar %}{{ o(f,f,p.font_ar) }}{% endfor %}</select></label></p>
<p><label>حجم الخط الأساسي: <input name="font_size_base" type="number" min="6" max="24" step="0.5" value="{{ p.font_size_base }}"></label></p>
<p class="muted">تُضمّن الخطوط في ملف PDF لضمان ظهور المستند بالتنسيق نفسه لدى العميل. لاستخدام خط مخصص (مثل Cairo أو Tajawal)، ارفع ملف TTF من النموذج أدناه.</p></div>
<div class="card"><h3>الألوان</h3><p>جاهزة: {% for n,v in presets.items() %}<button type="button" class="sec" onclick="preset({{ v|tojson|forceescape }})">{{ n }}</button> {% endfor %}</p>
<p><label>الأساسي <input type="color" name="c_primary" value="{{ p.c_primary }}"></label> <label>التمييز <input type="color" name="c_accent" value="{{ p.c_accent }}"></label>
<label>النص <input type="color" name="c_text" value="{{ p.c_text }}"></label> <label>صفوف الجدول <input type="color" name="c_zebra" value="{{ p.c_zebra }}"></label>
<label>العناوين <input type="color" name="c_heading" value="{{ p.c_heading }}"></label> <label>نص عنوان الجدول <input type="color" name="c_th_text" value="{{ p.c_th_text }}"></label></p></div>
<div class="card"><h3>الورق (مظهر رقمي لملف PDF، غير مخصص للطباعة)</h3>
<p><label>المقاس: <select name="paper">{% for x in papers %}{{ o(x,x,p.paper) }}{% endfor %}</select></label>
<label>الاتجاه: <select name="orient">{{ o('portrait','طولي',p.orient) }}{{ o('landscape','عرضي',p.orient) }}</select></label>
<label>الهوامش: <select name="margin">{{ o('narrow','ضيقة',p.margin) }}{{ o('normal','عادية',p.margin) }}{{ o('wide','واسعة',p.margin) }}</select></label></p>
<p>لون الورق: {% for n,v in paper_colors.items() %}<button type="button" class="sec" onclick="document.getElementById('pc').value='{{ v }}'">{{ n }}</button> {% endfor %}
<input type="color" id="pc" name="paper_color" value="{{ p.paper_color }}"></p>
<p><label>الخامة: <select name="texture">{% for k,v in textures.items() %}{{ o(k,v,p.texture) }}{% endfor %}</select></label> <label>هوامش الطباعة (مم): <input name="bleed_mm" type="number" min="0" max="20" step="0.5" value="{{ p.bleed_mm }}"></label></p>
<p><label>صورة خلفية: <input type="file" name="bg_image_file" accept="image/*"></label> {% if p.bg_image %}<span class="ok">موجودة</span> <label><input type="checkbox" name="remove_bg"> إزالة</label>{% endif %}</p>
<p><label>شفافية الخلفية: <input name="bg_opacity" type="number" min="0" max="1" step="0.01" value="{{ p.bg_opacity }}"></label>
<label>عرض الخلفية: <select name="bg_repeat">{{ o('cover','ملء الصفحة',p.bg_repeat) }}{{ o('center','توسيط',p.bg_repeat) }}{{ o('repeat','تكرار',p.bg_repeat) }}</select></label>
<label>تطبيق الخلفية: <select name="bg_scope">{{ o('all','كل الصفحات',p.bg_scope) }}{{ o('cover','الغلاف فقط',p.bg_scope) }}{{ o('inner','الصفحات الداخلية',p.bg_scope) }}</select></label></p>
<p><label>إطار الصفحة: <select name="border_style">{{ o('none','دون',p.border_style) }}{{ o('simple','رفيع',p.border_style) }}{{ o('double','مزدوج',p.border_style) }}{{ o('accent_frame','بلون التمييز',p.border_style) }}</select></label>
<label>الزخرفة: <select name="decor_style">{{ o('none','دون',p.decor_style) }}{{ o('side_stripe','شريط جانبي',p.decor_style) }}{{ o('corner_bars','أشرطة الزوايا',p.decor_style) }}</select></label></p></div>
<div class="card"><h3>الهيدر والشعار</h3><p><label>شكل الهيدر: <select name="header">{{ o('band','شريط ملون',p.header) }}{{ o('line','خط رفيع',p.header) }}{{ o('none','دون',p.header) }}</select></label>
<label>مكان الشعار: <select name="logo_pos">{{ o('left','يسار',p.logo_pos) }}{{ o('center','وسط',p.logo_pos) }}{{ o('right','يمين',p.logo_pos) }}</select></label>
<label><input type="checkbox" name="logo_plate" {{ 'checked' if p.logo_plate=='1' }}> خلفية بيضاء للشعار على الشريط</label> <label>حجم الشعار: <select name="logo_size">{{ o('s','صغير',p.logo_size) }}{{ o('m','متوسط',p.logo_size) }}{{ o('l','كبير',p.logo_size) }}</select></label></p></div>
<div class="card"><h3>العلامة المائية</h3><p><label>النوع: <select name="wm_type">{{ o('none','دون',p.wm_type) }}{{ o('text','نص',p.wm_type) }}{{ o('image','صورة',p.wm_type) }}</select></label>
<label>النص: <input name="wm_text" value="{{ p.wm_text }}"></label> <label>صورة: <input type="file" name="wm_image_file" accept="image/*"></label>
{% if p.wm_image %}<span class="ok">موجودة</span> <label><input type="checkbox" name="remove_wm"> إزالة</label>{% endif %}</p>
<p><label>الشفافية: <input name="wm_opacity" value="{{ p.wm_opacity }}" size="4"></label> <label>الزاوية: <select name="wm_angle">{{ o('45','قطرية',p.wm_angle) }}{{ o('0','أفقية',p.wm_angle) }}{{ o('-45','قطرية عكسية',p.wm_angle) }}</select></label></p></div>
<div class="card"><h3>الكتالوج: الغلاف والفهرس</h3><p><label><input type="checkbox" name="cover" {{ 'checked' if p.cover=='1' }}> غلاف</label>
<label><input type="checkbox" name="toc" {{ 'checked' if p.toc=='1' }}> فهرس محتويات</label>
<label><input type="checkbox" name="dividers" {{ 'checked' if p.dividers=='1' }}> صفحة فاصلة لكل منتج</label>
<label>شكل الغلاف: <select name="cover_style">{{ o('block','كتلة لونية',p.cover_style) }}{{ o('minimal','بسيط',p.cover_style) }}</select></label></p>
<p><label>عنوان الغلاف: <input name="cover_title" value="{{ p.cover_title }}" size="30"></label> <label>عنوان فرعي: <input name="cover_sub" value="{{ p.cover_sub }}" size="30"></label></p></div>
<div class="card"><button>حفظ</button></div></form>
<div class="card"><h3>معاينة (آخر نسخة محفوظة)</h3><img src="/preview.png?profile={{ p.id }}&kind=datasheet&t={{ ts }}" style="max-width:48%;border:1px solid #ccd">
<img src="/preview.png?profile={{ p.id }}&kind=cover&t={{ ts }}" style="max-width:48%;border:1px solid #ccd"></div>
<div class="card"><h3>سجل إصدارات الهوية</h3><table><tr><th>الإصدار</th><th>الاسم</th><th>التاريخ</th><th>استعادة</th></tr>
{% for v in versions %}<tr><td>v{{ v.version_num }}</td><td>{{ v.name }}</td><td>{{ v.created }}</td><td><form method="post" action="/export-settings/restore-version"><input type="hidden" name="vid" value="{{ v.id }}"><input type="hidden" name="pid" value="{{ p.id }}"><button class="sec">استعادة كإصدار جديد</button></form></td></tr>
{% else %}<tr><td colspan="4" class="muted">لا توجد إصدارات محفوظة.</td></tr>{% endfor %}</table></div>
<div class="card"><h3>رفع خط مخصص</h3><form method="post" action="/export-settings/font" enctype="multipart/form-data">
<input name="fname" placeholder="اسم الخط (مثل Cairo)"> عادي: <input type="file" name="regular" accept=".ttf"> عريض (اختياري): <input type="file" name="bold" accept=".ttf"><button class="sec">رفع</button></form></div>
<script>function preset(v){for(var k in v){var e=document.querySelector('[name='+k+']');if(e)e.value=v[k]}}</script>
{% endblock %}"""

DICTIONARY = """{% extends "base" %}{% block body %}
<div class="card"><h3>قاموس المواصفات</h3>
<p class="muted">يوحّد قاموس المواصفات المسميات الواردة من الموردين (مثل N.W وNet weight وPeso netto) تحت اسم موحد، ويتيح إضافة المقابل العربي وترتيب العرض. لا يتغير الاسم الأصلي للمورد؛ ويُستخدم الاسم الموحد في البحث وملفات PDF.</p>
<p>{% if unmapped %}<span class="tag y">{{ unmapped }} خاصية دون اسم موحد</span>{% else %}<span class="tag g">كله متوحد</span>{% endif %}</p>
<form method="post" action="/dictionary/suggest" style="display:inline"><button class="sec">اقتراح من القاموس الجاهز</button></form></div>
<form method="post" action="/dictionary/save"><datalist id="cn">{% for x in canon %}<option value="{{ x }}">{% endfor %}</datalist>
<div class="card"><table><tr><th>الاسم عند المورد</th><th>مرات</th><th>مثال</th><th>الاسم الموحد</th><th>عربي</th><th>ترتيب</th></tr>
{% for r in rows %}<tr><td class="ltr"><input type="hidden" name="k" value="{{ r.k }}">{{ r.shown }}</td><td>{{ r.n }}</td><td class="ltr" style="font-size:12px">{{ r.sample }}</td>
<td><input class="ltr" name="canonical" list="cn" value="{{ r.m.get('canonical','') }}" size="20"></td>
<td><input name="ar" value="{{ r.m.get('ar','') }}" size="16"></td>
<td><input name="sort" value="{{ r.m.get('sort','') if r.m else '' }}" size="4"></td></tr>{% endfor %}</table></div>
<div class="card"><button>حفظ القاموس</button></div></form>{% endblock %}"""
TEMPLATES = {"dictionary": DICTIONARY, "search": SEARCH, "exsettings": EXSETTINGS, "products": PRODUCTS, "product": PRODUCT, "gaps": GAPS, "sync": SYNC, "base": BASE, "home": HOME, "index": INDEX, "models": MODELS, "treenode": TREE_NODE, "tree": TREE,
             "model": MODEL, "review": REVIEW, "rules": RULES, "settings": SETTINGS}
