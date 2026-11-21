"""Application shell (the page frame every screen shares).

Design: a people-first frame instead of a list of seventeen equal links.
  - Five places (Home, Catalogs, Products, Export, Admin) in a side bar (desktop) or a bottom bar (phone).
  - Inside a place, its pages are TABS of one workspace, not separate destinations.
  - One search box on every page.
Navigation is defined in ui.py (GROUPS); this file is only the frame.
"""

ICONS = {
    "home": '<path d="M3 11.5 12 4l9 7.5"/><path d="M5.5 10v9.5h13V10"/><path d="M10 19.5v-5h4v5"/>',
    "catalogs": '<path d="M5 4.5h10.5a2 2 0 0 1 2 2V20H7a2 2 0 0 1-2-2z"/><path d="M5 18a2 2 0 0 1 2-2h10.5"/><path d="M9 8.5h5"/>',
    "products": '<path d="M12 3 20 7.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5 12 12l8-4.5"/><path d="M12 12v9"/>',
    "export": '<path d="M7 9V4h10v5"/><rect x="4" y="9" width="16" height="8" rx="1.5"/><path d="M7 14h10v6H7z"/>',
    "admin": '<circle cx="12" cy="12" r="3"/><path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M18.4 5.6l-1.8 1.8M7.4 16.6l-1.8 1.8"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/>',
    "upload": '<path d="M12 16V5"/><path d="m7.5 9.5 4.5-4.5 4.5 4.5"/><path d="M5 19.5h14"/>',
    "download": '<path d="M12 4v11"/><path d="m7.5 10.5 4.5 4.5 4.5-4.5"/><path d="M5 19.5h14"/>',
}

BASE = """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#1F3864">
<title>{% if nav and nav.tab %}{{ nav.tab.label }} - {% endif %}نظام الكتالوجات</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;500;600;700&display=swap">
{% block head %}{% endblock %}
<style>
:root{--ink:#17202e;--navy:#1F3864;--navy-2:#2a4a80;--accent:#E8590C;--paper:#f4f6fa;--card:#fff;--line:#dfe3ec;--muted:#667085;
 --ok:#1a7f37;--warn:#b26a00;--bad:#c0392b;--side:236px;--r:10px}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{font-family:"Cairo","Segoe UI",Tahoma,Arial,sans-serif;margin:0;background:var(--paper);color:var(--ink);line-height:1.55}
a{color:var(--navy)}
:focus-visible{outline:3px solid #f2a97a;outline-offset:2px;border-radius:4px}
.skip{position:absolute;inset-inline-start:8px;top:-60px;background:#fff;padding:8px 12px;border-radius:6px;z-index:50}.skip:focus{top:8px}

/* ---- frame */
.app{display:flex;min-height:100vh}
.side{width:var(--side);flex:none;background:var(--navy);color:#fff;position:sticky;top:0;height:100vh;display:flex;flex-direction:column;padding:16px 12px}
.brand{display:flex;align-items:center;gap:10px;color:#fff;text-decoration:none;font-weight:600;font-size:17px;padding:4px 8px 18px}
.brand i{width:34px;height:34px;border-radius:9px;background:var(--accent);display:grid;place-items:center;flex:none}
.brand svg{width:20px;height:20px;stroke:#fff}
.gnav{display:flex;flex-direction:column;gap:2px}
.gnav a{display:flex;align-items:center;gap:11px;color:#d7e0f2;text-decoration:none;padding:10px 10px;border-radius:8px;font-size:15px;position:relative}
.gnav a:hover{background:#ffffff14;color:#fff}
.gnav a[aria-current=page]{background:#ffffff1f;color:#fff;font-weight:600}
.gnav a[aria-current=page]::before{content:"";position:absolute;inset-inline-start:-12px;top:8px;bottom:8px;width:4px;border-radius:0 4px 4px 0;background:var(--accent)}
svg.ic{width:21px;height:21px;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;flex:none}
.badge{background:var(--accent);color:#fff;border-radius:10px;padding:0 7px;font-size:12px;font-weight:600;margin-inline-start:auto}
.who{margin-top:auto;border-top:1px solid #ffffff22;padding:12px 8px 0;font-size:14px;color:#d7e0f2}
.who b{display:block;color:#fff;font-weight:500}
.who form{margin:8px 0 0}
.who button{background:#ffffff1a;color:#fff;border:0;padding:5px 12px;border-radius:6px;font-size:13px;cursor:pointer}
.who a{color:#d7e0f2;font-size:13px}

.col{flex:1;min-width:0;display:flex;flex-direction:column}
.top{background:#fff;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:12px;padding:10px 22px;position:sticky;top:0;z-index:20}
.gsearch{flex:1;max-width:520px;position:relative}
.gsearch input{width:100%;padding:9px 40px 9px 12px;border:1px solid var(--line);border-radius:9px;background:var(--paper);font-size:15px}
.gsearch svg{position:absolute;inset-inline-start:auto;right:12px;top:50%;transform:translateY(-50%);width:18px;height:18px;stroke:var(--muted);fill:none;stroke-width:1.8;stroke-linecap:round}
.top .sp{flex:1}
.btn-up{display:inline-flex;align-items:center;gap:8px;background:var(--accent);color:#fff;text-decoration:none;padding:8px 14px;border-radius:9px;font-size:14px;font-weight:500}
.btn-up svg{width:18px;height:18px;stroke:#fff;fill:none;stroke-width:1.9;stroke-linecap:round;stroke-linejoin:round}
.mbrand{display:none}

.tabs{background:#fff;border-bottom:1px solid var(--line);padding:0 22px;display:flex;gap:4px;overflow-x:auto;scrollbar-width:none}
.tabs::-webkit-scrollbar{display:none}
.tabs a{white-space:nowrap;text-decoration:none;color:var(--muted);padding:12px 14px;font-size:15px;border-bottom:3px solid transparent;margin-bottom:-1px}
.tabs a:hover{color:var(--ink)}
.tabs a[aria-current=page]{color:var(--ink);border-bottom-color:var(--accent);font-weight:600}
.tabs .dl{margin-inline-start:auto;display:inline-flex;align-items:center;gap:6px}
.tabs .dl svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.9;stroke-linecap:round;stroke-linejoin:round}

main{width:100%;max-width:1180px;margin:0 auto;padding:20px 22px 40px}
.flash{background:#fff4d6;border:1px solid #f0d58a;padding:9px 13px;border-radius:9px;margin-bottom:12px}

/* ---- content primitives (kept for every existing page) */
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:16px;margin-bottom:16px}
.card h2,.card h3{margin-top:0}
h2{font-size:21px}h3{font-size:17px}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:8px 10px;border-bottom:1px solid #e8ebf2;text-align:right;vertical-align:middle}
th{background:#f0f3f9;font-weight:600;color:#3a4458}
.ltr{direction:ltr;text-align:left;unicode-bidi:embed}
input,select,button,textarea{font:inherit;padding:8px 10px;border:1px solid #c5cbd8;border-radius:8px;background:#fff;color:var(--ink)}
button,.btn{background:var(--navy);color:#fff;border:0;cursor:pointer;padding:8px 15px;border-radius:8px;text-decoration:none;display:inline-block;font-size:14px}
button:hover,.btn:hover{background:var(--navy-2)}
button.sec,.btn.sec{background:#e6eaf3;color:var(--ink)}button.sec:hover,.btn.sec:hover{background:#d9dfec}
button.red{background:var(--bad)}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.muted{color:var(--muted)}
.imgs{display:flex;flex-wrap:wrap;gap:10px}.imgs .im{border:1px solid #dde;border-radius:8px;padding:6px;width:210px;background:#fff}
.imgs img{max-width:100%;max-height:150px;display:block;margin:0 auto 6px;background:#fff}
ul.tree{list-style:none;padding-right:18px}ul.tree li{margin:4px 0}
.tag{font-size:12px;border-radius:5px;padding:1px 6px;background:#e3e7ef}
.tag.g{background:#d6f2dc}.tag.y{background:#fff1c9}.tag.r{background:#fadcdc}

.crumb{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:12px;font-size:14px;color:var(--muted)}
.crumb a:not(.btn){text-decoration:none}.crumb a:not(.btn):hover{text-decoration:underline}.crumb b{color:var(--ink)}.crumb .sp{flex:1}
.crumb .btn{padding:6px 12px}
.flow .step{display:flex;align-items:center;gap:12px;padding:11px 0;border-bottom:1px solid #e8ebf2;flex-wrap:wrap}.flow .step:last-child{border-bottom:0}
.flow .step i{width:30px;height:30px;border-radius:50%;display:grid;place-items:center;font-style:normal;font-weight:600;background:#eef1f7;color:var(--muted);flex:none}
.flow .step.ok i{background:#d6f2dc;color:var(--ok)}.flow .step.todo i{background:var(--accent);color:#fff}
.flow .step>div{flex:1;min-width:200px}.flow .step b{display:block}.flow .step span{color:var(--muted);font-size:14px}
.flow .step form{margin:0}
/* ---- phone */
.bnav{display:none}
@media (max-width:860px){
 .side{display:none}
 .top{padding:8px 12px;gap:8px;flex-wrap:wrap}
 .mbrand{display:flex;align-items:center;gap:8px;font-weight:600;color:var(--navy);text-decoration:none;flex:none}
 .mbrand i{width:28px;height:28px;border-radius:8px;background:var(--accent);display:grid;place-items:center}
 .mbrand svg{width:17px;height:17px;stroke:#fff}
 .gsearch{max-width:none;flex:1 1 180px}
 .btn-up span{display:none}.btn-up{padding:8px 10px}
 .tabs{padding:0 8px}
 main{padding:14px 12px 88px}
 .card>table,.card>form>table{display:block;overflow-x:auto}
 input:not([type=checkbox]):not([type=radio]),select,textarea{min-width:0!important;max-width:100%}
 label{max-width:100%}
 .card{overflow-x:auto}
 .bnav{display:flex;position:fixed;inset-inline:0;bottom:0;background:#fff;border-top:1px solid var(--line);z-index:30;padding-bottom:env(safe-area-inset-bottom,0)}
 .bnav a{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;padding:7px 2px 6px;text-decoration:none;color:var(--muted);font-size:12px;position:relative}
 .bnav a[aria-current=page]{color:var(--navy);font-weight:600}
 .bnav a[aria-current=page]::before{content:"";position:absolute;top:0;width:28px;height:3px;border-radius:0 0 3px 3px;background:var(--accent)}
 .bnav .badge{position:absolute;top:3px;margin:0;inset-inline-start:calc(50% + 6px)}
}
@media (prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
</style></head><body>
<a class="skip" href="#main">تخطي إلى المحتوى</a>
{% macro ico(name) %}<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">{{ icons[name]|safe }}</svg>{% endmacro %}
<div class="app">
<aside class="side"><a class="brand" href="/"><i><svg viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{{ icons.catalogs|safe }}</svg></i>نظام الكتالوجات</a>
<nav class="gnav" aria-label="الأقسام">{% for g in nav.groups %}<a href="{{ g.url }}"{% if g.active %} aria-current="page"{% endif %}>{{ ico(g.icon) }}{{ g.label }}{% if g.badge %}<span class="badge">{{ g.badge }}</span>{% endif %}</a>{% endfor %}</nav>
{% if cur_user and cur_user.username!="local" %}<div class="who"><b>{{ cur_user.display_name or cur_user.username }}</b><a href="/account">حسابي</a>
<form method="post" action="/logout"><button>تسجيل الخروج</button></form></div>{% endif %}</aside>
<div class="col">
<header class="top"><a class="mbrand" href="/"><i><svg viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{{ icons.catalogs|safe }}</svg></i></a>
<form class="gsearch" role="search" action="/search" method="get"><svg viewBox="0 0 24 24">{{ icons.search|safe }}</svg><input type="search" name="q" value="{{ gq or '' }}" placeholder="ابحث عن طراز أو منتج أو مواصفة" aria-label="بحث"></form>
<span class="sp"></span><a class="btn-up" href="/catalogs#upload"><svg viewBox="0 0 24 24">{{ icons.upload|safe }}</svg><span>رفع كتالوج</span></a></header>
{% if nav.group and nav.group.tabs %}<nav class="tabs" aria-label="{{ nav.group.label }}">{% for t in nav.group.tabs %}{% if t.download %}<a class="dl" href="{{ t.url }}"><svg viewBox="0 0 24 24">{{ icons.download|safe }}</svg>{{ t.label }}</a>{% else %}<a href="{{ t.url }}"{% if t.active %} aria-current="page"{% endif %}>{{ t.label }}{% if t.badge %} <span class="badge">{{ t.badge }}</span>{% endif %}</a>{% endif %}{% endfor %}</nav>{% endif %}
<main id="main">{% with msgs = get_flashed_messages() %}{% for m in msgs %}<div class="flash" role="status">{{ m }}</div>{% endfor %}{% endwith %}
{% block body %}{% endblock %}</main>
</div></div>
<nav class="bnav" aria-label="الأقسام">{% for g in nav.groups %}<a href="{{ g.url }}"{% if g.active %} aria-current="page"{% endif %}>{{ ico(g.icon) }}{{ g.label }}{% if g.badge %}<span class="badge">{{ g.badge }}</span>{% endif %}</a>{% endfor %}</nav>
</body></html>"""
