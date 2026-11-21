"""Templates for the pages added by the cloud version (same look as the original UI)."""

LOGIN = """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700&display=swap"><meta name="viewport" content="width=device-width,initial-scale=1"><title>تسجيل الدخول - نظام الكتالوجات</title>
<style>body{font-family:"Cairo",Tahoma,Arial,sans-serif;background:#f4f6fa;color:#1d2433;margin:0;display:flex;min-height:100vh;align-items:center;justify-content:center}
.box{background:#fff;border-radius:12px;padding:26px;width:320px;box-shadow:0 2px 8px #0002}h2{margin-top:0;color:#1F3864}
input,button{font:inherit;padding:8px 10px;border:1px solid #c5cbd8;border-radius:6px;width:100%;box-sizing:border-box;margin-bottom:10px}
button{background:#1F3864;color:#fff;border:0;cursor:pointer}.flash{background:#fff4d6;border:1px solid #f0d58a;padding:8px 12px;border-radius:8px;margin-bottom:10px}.muted{color:#778;font-size:13px}</style></head>
<body><form class="box" method="post"><h2>📚 نظام الكتالوجات</h2>
{% with msgs = get_flashed_messages() %}{% for m in msgs %}<div class="flash">{{ m }}</div>{% endfor %}{% endwith %}
{% if empty %}<div class="flash">لا يوجد مستخدمون بعد. أنشئ حساب المالك أولًا باستخدام الأمر: <span dir="ltr">python manage.py create-user --owner</span> أو ضبط OWNER_USERNAME / OWNER_PASSWORD.</div>{% endif %}
<input name="username" placeholder="اسم المستخدم" autocomplete="username" autofocus required>
<input name="password" type="password" placeholder="كلمة المرور" autocomplete="current-password" required>
<button>تسجيل الدخول</button></form></body></html>"""

ACCOUNT = """{% extends "base" %}{% block body %}
<div class="card"><h3>حسابي: {{ cur_user.display_name or cur_user.username }}</h3>
<form method="post"><p><label>كلمة المرور الحالية: <input type="password" name="old" required></label></p>
<p><label>كلمة المرور الجديدة: <input type="password" name="new" required minlength="8"></label></p>
<p><label>تأكيدها: <input type="password" name="new2" required minlength="8"></label></p><button>تغيير كلمة المرور</button></form></div>{% endblock %}"""

USERS = """{% extends "base" %}{% block body %}
<div class="card"><h3>المستخدمون</h3><p class="muted">يتمتع جميع المستخدمين بالصلاحيات نفسها للاستخدام والإضافة والتعديل والحذف. ويقتصر دور المالك على إدارة المستخدمين وتنزيل النسخ الاحتياطية.</p>
<table><tr><th>اسم المستخدم</th><th>الاسم</th><th>النوع</th><th>آخر تسجيل دخول</th><th>الحالة</th><th>تعيين كلمة مرور جديدة</th><th></th></tr>
{% for u in rows %}<tr><td class="ltr">{{ u.username }}</td><td>{{ u.display_name }}</td><td>{{ 'مالك' if u.is_owner else 'موظف' }}</td><td class="ltr" style="font-size:12px">{{ u.last_login or '—' }}</td>
<td>{% if u.active %}<span class="ok">نشط</span>{% else %}<span class="bad">موقوف</span>{% endif %}{% if u.locked_until %} <span class="warn">مغلق مؤقتًا</span>{% endif %}</td>
<td><form method="post" action="/users/{{ u.id }}/password" style="display:flex;gap:4px"><input type="password" name="password" placeholder="ثمانية أحرف على الأقل" size="14" required><button class="sec">تغيير</button></form></td>
<td><form method="post" action="/users/{{ u.id }}/toggle"><button class="sec">{{ 'إيقاف' if u.active else 'تفعيل' }}</button></form></td></tr>{% endfor %}</table></div>
<div class="card"><h3>إضافة مستخدم</h3><form method="post">
<label>اسم المستخدم (إنجليزي صغير): <input name="username" class="ltr" required></label>
<label>الاسم: <input name="display_name"></label>
<label>كلمة المرور: <input type="password" name="password" minlength="8" required></label>
<label><input type="checkbox" name="is_owner" value="1"> مالك</label> <button>إضافة</button></form></div>{% endblock %}"""

CATALOG = """{% extends "base" %}{% block head %}{% if cat.state in ('processing','checking') %}<meta http-equiv="refresh" content="3">{% endif %}{% endblock %}
{% block body %}
<div class="card"><h2 class="ltr" style="margin:0">{{ cat.name }}</h2>
<div class="muted">المورد: <b>{{ cat.supplier or '—' }}</b> | الإصدار: <b class="ltr">{{ cat.version or '—' }}</b> | تاريخ الكتالوج: <b class="ltr">{{ cat.catalog_date or '—' }}</b>
{% if cat.meta_source=='auto' %}<span class="tag y">إصدار/تاريخ مكتشف تلقائيًا</span>{% endif %}<br>
رُفع بواسطة <b>{{ cat.uploaded_by or '—' }}</b> في <span class="ltr">{{ cat.created }}</span> | <a href="/catalog/{{ cat.id }}/file">تنزيل الملف الأصلي</a></div><br>
{% if cat.state=='needs_decision' %}
<div class="flash"><b>⚠ تنبيه تكرار:</b> {{ dup.message or cat.msg }}
{% for m in dup.matches or [] %}<div><a href="/catalog/{{ m.id }}">{{ m.name }}</a> — {{ m.supplier }} {{ m.version }} {{ m.date }} (رُفع {{ m.created }} بواسطة {{ m.by or '—' }})</div>{% endfor %}
<form method="post" action="/catalog/{{ cat.id }}/continue" style="display:inline"><button>تابع الاستخراج رغم التحذير</button></form>
<form method="post" action="/catalog/{{ cat.id }}/delete" style="display:inline" onsubmit="return confirm('حذف هذا الملف؟')"><button class="red">حذف الكتالوج</button></form></div>
{% elif cat.state in ('processing','checking') %}<p class="warn">قيد {{ 'فحص التكرار' if cat.state=='checking' else 'المعالجة' }} {{ cat.msg }}</p>
{% elif cat.state in ('error','stale') %}
<div class="flash"><b class="bad">{{ 'فشل الاستخراج' if cat.state=='error' else 'توقف الاستخراج (ربما أُعيد تشغيل الخادم)' }}.</b> حُذفت البيانات الجزئية. يمكنك إعادة المحاولة.<div class="ltr muted" style="font-size:11px;white-space:pre-wrap">{{ cat.msg }}</div>
<form method="post" action="/catalog/{{ cat.id }}/retry" style="display:inline"><button>إعادة المحاولة</button></form>
<form method="post" action="/catalog/{{ cat.id }}/delete" style="display:inline" onsubmit="return confirm('حذف هذا الكتالوج؟')"><button class="red">حذف</button></form></div>
{% else %}<p><span class="ok">تم الاستخراج</span> — <a href="/models?catalog={{ cat.id }}">{{ stats.models }} طراز</a> ({{ stats.approved }} معتمد) — {{ stats.specs }} مواصفة
{% if stats.unverified %}<span class="warn">({{ stats.unverified }} مواصفة لم يُعثر عليها في نص صفحة المصدر؛ راجعها من صفحة الطراز عبر رابط المصدر)</span>{% endif %} — {{ stats.images }} صورة</p>{% endif %}</div>

{% if cat.state=='done' and stats.models %}<div class="card flow"><h3>مسار العمل</h3>
<div class="step ok"><i>✓</i><div><b>الاستخراج</b><span>{{ stats.models }} طراز و{{ stats.specs }} مواصفة و{{ stats.images }} صورة.</span></div></div>
<div class="step {{ 'todo' if flow.open_reviews else 'ok' }}"><i>{{ '٢' if flow.open_reviews else '✓' }}</i><div><b>المراجعة</b><span>{% if flow.open_reviews %}{{ flow.open_reviews }} بند ينتظر قرارك.{% else %}لا بنود مفتوحة.{% endif %}</span></div>
{% if flow.open_reviews %}<a class="btn" href="/review?catalog={{ cat.id }}">افتح المراجعة</a>{% endif %}</div>
<div class="step {{ 'todo' if flow.ready else 'ok' }}"><i>{{ '٣' if flow.ready else '✓' }}</i><div><b>الاعتماد</b><span>{% if flow.ready %}{{ flow.ready }} طراز جاهز للاعتماد.{% else %}{{ stats.approved }} طراز معتمد.{% endif %}{% if flow.nospec %} {{ flow.nospec }} طراز بلا مواصفات (لن يُعتمد تلقائيًا).{% endif %}{% if flow.approved_noimg %} <span class="warn">{{ flow.approved_noimg }} معتمد بلا صورة معتمدة.</span> <a href="/studio/unlinked-images?catalog={{ cat.id }}">اربط الصور</a>{% endif %}</span></div>
{% if flow.ready %}<form method="post" action="/catalog/{{ cat.id }}/approve-all"><button>اعتمد الجاهز مع صوره</button></form>{% endif %}<a class="btn sec" href="/models?catalog={{ cat.id }}">عرض الطرازات</a></div>
<div class="step {{ 'todo' if stats.approved else '' }}"><i>{{ '٤' if stats.approved else '·' }}</i><div><b>التصدير</b><span>{% if stats.approved %}ورقة بيانات لكل طراز أو كتالوج PDF بهويتك.{% else %}يتاح بعد اعتماد طراز واحد على الأقل.{% endif %}</span></div>
{% if stats.approved %}<a class="btn" href="/export-center/?doc_type=master_catalog&supplier={{ cat.supplier|urlencode }}">تصدير كتالوج المورد</a>{% endif %}</div></div>{% endif %}
<div class="card"><h3>سلسلة الإصدارات</h3>
{% if prev %}<p>إصدار لاحق لـ: <a href="/catalog/{{ prev.id }}">{{ prev.name }}</a> ({{ prev.version or '—' }} {{ prev.catalog_date }}) —
<a href="/catalog/{{ cat.id }}/compare/{{ prev.id }}">مقارنة</a>
<form method="post" action="/catalog/{{ cat.id }}/previous" style="display:inline"><input type="hidden" name="previous_id" value=""><button class="sec">فك الربط</button></form></p>
{% else %}<form method="post" action="/catalog/{{ cat.id }}/previous">ربط كإصدار لاحق لكتالوج:
<select name="previous_id">{% for o in others %}<option value="{{ o.id }}">#{{ o.id }} {{ o.name }} — {{ o.supplier }} {{ o.version }}</option>{% endfor %}</select> <button class="sec">ربط</button></form>{% endif %}
{% for n in newer %}<p>صدر لهذا الكتالوج إصدار أحدث: <a href="/catalog/{{ n.id }}">{{ n.name }}</a> ({{ n.version or '—' }} {{ n.catalog_date }})</p>{% endfor %}
<form method="post" action="/catalog/{{ cat.id }}/meta"><label>المورد: <input name="supplier" value="{{ cat.supplier }}"></label> <label>الإصدار: <input name="version" value="{{ cat.version }}" size="8" class="ltr"></label>
<label>تاريخ الكتالوج: <input name="catalog_date" value="{{ cat.catalog_date }}" size="10" class="ltr" placeholder="2025-03"></label> <button class="sec">حفظ بيانات الكتالوج</button></form></div>

{% if similar %}<div class="card"><h3>تشابه مع كتالوجات أخرى</h3>
{% for s in similar %}<div style="margin-bottom:10px"><span class="tag {{ 'r' if s.review.kind=='SIMILAR_CATALOG' else 'y' }}">{{ s.review.kind }}</span> {{ s.review.note }}<br>
<a class="btn sec" href="/catalog/{{ cat.id }}/compare/{{ s.other.id }}">مقارنة تفصيلية</a>
{% if not s.review.resolved and s.review.kind!='SUPERSEDES' %}
<form method="post" action="/catalog/{{ cat.id }}/previous" style="display:inline"><input type="hidden" name="previous_id" value="{{ s.other.id }}"><button class="sec">ربط كإصدار لاحق</button></form>
<form method="post" action="/catalog/{{ cat.id }}/not-duplicate" style="display:inline"><button class="sec">ليس مكررًا؛ أغلق التنبيه</button></form>
{% if s.review.kind=='SIMILAR_CATALOG' %}<form method="post" action="/catalog/{{ cat.id }}/delete" style="display:inline" onsubmit="return confirm('حذف هذا الكتالوج وجميع البيانات المستخرجة منه؟')"><button class="red">نسخة مكررة: احذفها</button></form>{% endif %}
{% elif s.review.resolved %}<span class="ok">أُغلق</span>{% endif %}</div>{% endfor %}</div>{% endif %}

<div class="card"><h3>بنود المراجعة ({{ reviews|length }})</h3><table><tr><th>النوع</th><th>المرجع</th><th>الملاحظة</th><th></th></tr>
{% for r in reviews %}<tr><td class="ltr"><span class="tag">{{ r.kind }}</span></td><td class="ltr">{{ r.ref }}</td><td>{{ r.note }}</td>
<td>{% if r.resolved %}<span class="ok">تم</span>{% else %}<form method="post" action="/review/{{ r.id }}/resolve"><button class="sec">تم الحل</button></form>{% endif %}</td></tr>
{% else %}<tr><td colspan="4" class="muted">لا توجد بنود.</td></tr>{% endfor %}</table></div>
<div class="card"><h3>السجل</h3><table>{% for h in hist %}<tr><td class="ltr" style="font-size:12px">{{ h.ts }}</td><td>{{ h.username or '—' }}</td><td>{{ h.action }}</td><td class="ltr">{{ h.detail }}</td></tr>{% endfor %}</table></div>
{% endblock %}"""

COMPARE = """{% extends "base" %}{% block body %}
<div class="card"><h3>مقارنة</h3><p><b class="ltr">{{ a.name }}</b> (#{{ a.id }}) ⟵ مقابل ⟶ <b class="ltr">{{ b.name }}</b> (#{{ b.id }})</p>
<p>الطرازات المشتركة: <b>{{ r.common }}</b> من {{ r.n_a }} / {{ r.n_b }} — نسبة تطابق المواصفات بينها: <b>{{ '%.0f'|format(r.spec_same*100) }}%</b> — الطرازات التي تغيّرت مواصفاتها: <b>{{ r.changed|length }}</b></p>
<p class="muted">لا يُعد التحديث الذي يتضمن طرازات جديدة أو تغييرات في المواصفات نسخة مكررة، حتى إن كانت معظم طرازاته موجودة في الإصدار السابق.</p></div>
<div class="card"><h4>جديدة في #{{ a.id }} ({{ r.added|length }})</h4><p class="ltr">{{ r.added|join(', ') or '—' }}</p>
<h4>موجودة في #{{ b.id }} وغايبة من #{{ a.id }} ({{ r.removed|length }})</h4><p class="ltr">{{ r.removed|join(', ') or '—' }}</p></div>
<div class="card"><h4>مواصفات تغيّرت</h4><table><tr><th>الطراز</th><th>الخصائص</th></tr>
{% for code, attrs in r.changed[:80] %}<tr><td class="ltr">{{ code }}</td><td class="ltr">{{ attrs|join(', ') }}</td></tr>{% else %}<tr><td colspan="2" class="muted">لا توجد اختلافات.</td></tr>{% endfor %}</table></div>
<a class="btn sec" href="/catalog/{{ a.id }}">العودة إلى الكتالوج</a>{% endblock %}"""

ACTIVITY = """{% extends "base" %}{% block body %}
<div class="card"><h3>سجل النشاط (آخر 300 عملية)</h3>
<form method="get"><label>المستخدم: <select name="user"><option value="">الكل</option>{% for u in users %}<option {{ 'selected' if u==who }}>{{ u }}</option>{% endfor %}</select></label>
<label>النوع: <select name="entity"><option value="">الكل</option>{% for e in ['model','product','catalog','family','rule','export','profile','user','sync','system'] %}<option {{ 'selected' if e==ent }}>{{ e }}</option>{% endfor %}</select></label>
<button class="sec">تصفية</button></form><br>
<table><tr><th>الوقت (UTC)</th><th>اسم المستخدم</th><th>النوع</th><th>العملية</th><th>التفاصيل</th></tr>
{% for h in rows %}<tr><td class="ltr" style="font-size:12px">{{ h.ts }}</td><td>{{ h.username or '—' }}</td>
<td class="ltr">{% if h.entity in ('model','product','catalog') %}<a href="/{{ h.entity }}/{{ h.entity_id }}">{{ h.entity }} #{{ h.entity_id }}</a>{% else %}{{ h.entity }}{% endif %}</td>
<td>{{ h.action }}</td><td class="ltr">{{ h.detail }}</td></tr>{% endfor %}</table></div>{% endblock %}"""

BACKUP = """{% extends "base" %}{% block body %}
<div class="card"><h3>نسخة احتياطية</h3>
<p>البيانات الحالية: {{ counts.catalogs }} كتالوج، {{ counts.models }} طراز، {{ counts.specs }} مواصفة، {{ counts.images }} صورة، {{ counts.users }} مستخدم.</p>
<p><a class="btn" href="/backup/download">تنزيل نسخة كاملة من قاعدة البيانات (ZIP)</a></p>
<p class="muted">النسخة فيها كل الجداول بمعرّفاتها (المنتجات والطرازات والمواصفات) وقائمة بملفات Drive. محتوى الصور والكتالوجات نفسه محفوظ في Google Drive.
الاستعادة من سطر الأوامر: <span class="ltr">python manage.py restore backup.zip</span>.
{% if pg %}<br>على Cloud SQL فيه كمان نسخ تلقائية يومية واستعادة لأي لحظة (راجع README_CLOUD_AR.md).{% endif %}</p></div>
<div class="card"><h3>فحص سلامة البيانات</h3>
{% for i in issues %}<p><span class="tag y">{{ i.kind }}</span> {{ i.label }}: <b>{{ i.count }}</b> <span class="muted ltr">(أمثلة: {{ i.sample|join(', ') }})</span>
{% if i.kind=='MODEL_FAMILY_MISMATCH' %}<form method="post" action="/backup/fix-family" style="display:inline"><button class="sec">اجعل تصنيف الطرازات مطابقًا لتصنيف منتجاتها</button></form>{% endif %}</p>
{% else %}<p class="ok">لا توجد مشكلات.</p>{% endfor %}</div>{% endblock %}"""

MERGE = """{% extends "base" %}{% block body %}
<div class="card"><h3>مقترحات دمج المنتجات بين الموردين</h3>
<p class="muted">منتجات من موردين مختلفين تتشارك كلمة دالة على السلسلة وخصائص مواصفات متقاربة. هذه اقتراحات للمراجعة، ويُترك قرار الدمج لك مع الحفاظ على سجل التغييرات.</p>
<table><tr><th>منتج أ</th><th>منتج ب</th><th>تشابه الخصائص</th><th></th></tr>
{% for r in rows %}<tr><td><a href="/product/{{ r.a.id }}">{{ r.a.name }}</a><div class="muted ltr" style="font-size:12px">{{ r.sup_a }}</div></td>
<td><a href="/product/{{ r.b.id }}">{{ r.b.name }}</a><div class="muted ltr" style="font-size:12px">{{ r.sup_b }}</div></td><td>{{ '%.0f'|format(r.score*100) }}%</td>
<td><form method="post" action="/products/merge-do" style="display:inline" onsubmit="return confirm('دمج؟')"><input type="hidden" name="keep" value="{{ r.a.id }}"><input type="hidden" name="drop" value="{{ r.b.id }}"><button class="sec">ب ← أ</button></form>
<form method="post" action="/products/merge-do" style="display:inline" onsubmit="return confirm('دمج؟')"><input type="hidden" name="keep" value="{{ r.b.id }}"><input type="hidden" name="drop" value="{{ r.a.id }}"><button class="sec">أ ← ب</button></form></td></tr>
{% else %}<tr><td colspan="4" class="muted">لا توجد مقترحات.</td></tr>{% endfor %}</table></div>{% endblock %}"""

TRASH = """{% extends "base" %}{% block body %}
<div class="card"><h3>المحذوفات (قابلة للاستعادة)</h3><p class="muted">يُحفظ من كل كتالوج محذوف سجل كامل بمعرّفاته (الطرازات والمواصفات وروابط الصور). وتظل ملفات الصور وملفات PDF الأصلية محفوظة في Drive.</p>
<table><tr><th>الوقت (UTC)</th><th>حذفه</th><th>النوع</th><th>الوصف</th><th></th></tr>
{% for r in rows %}<tr><td class="ltr" style="font-size:12px">{{ r.ts }}</td><td>{{ r.username or '—' }}</td><td>{{ r.entity }} #{{ r.entity_id }}</td><td class="ltr">{{ r.label }}</td>
<td>{% if r.restored %}<span class="ok">تمت الاستعادة</span>{% else %}<form method="post" action="/trash/{{ r.id }}/restore"><button class="sec">استعادة</button></form>{% endif %}</td></tr>
{% else %}<tr><td colspan="5" class="muted">لا توجد عناصر محذوفة.</td></tr>{% endfor %}</table></div>{% endblock %}"""

ARCHIVED = """{% extends "base" %}{% block body %}
<div class="card"><h3>المنتجات المؤرشفة</h3><table><tr><th>المنتج</th><th></th></tr>
{% for r in rows %}<tr><td>{{ r.name }}</td><td><form method="post" action="/product/{{ r.id }}/unarchive"><button class="sec">إرجاع</button></form></td></tr>
{% else %}<tr><td colspan="2" class="muted">لا توجد منتجات مؤرشفة.</td></tr>{% endfor %}</table></div>{% endblock %}"""

TEMPLATES = {"archived": ARCHIVED, "trash": TRASH, "login": LOGIN, "account": ACCOUNT, "users": USERS, "catalog": CATALOG, "compare": COMPARE, "activity": ACTIVITY, "backup": BACKUP, "merge": MERGE}
