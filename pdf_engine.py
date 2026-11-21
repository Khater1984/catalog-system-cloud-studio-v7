"""Central Unified PDF Export Engine with 5 Independent Templates & Real Identity Backgrounds/Textures."""
import io, os, re, datetime, math
from xml.sax.saxutils import escape
from PIL import Image as PILImage
from reportlab.lib.pagesizes import A4, A3, A5, LETTER, LEGAL, landscape
from reportlab.lib.units import cm, mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Table, TableStyle,
    Image, PageBreak, NextPageTemplate, Flowable
)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import db, profiles, storage

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
    ARABIC_OK = True
except Exception:
    ARABIC_OK = False

TEMPLATES_META = {
    "Minimal":   {"header_override": "line",    "table_grid": False, "title_scale": 1.0,  "accent_bar": False},
    "Corporate": {"header_override": "band",    "table_grid": False, "title_scale": 1.05, "accent_bar": True},
    "Technical": {"header_override": "minimal", "table_grid": True,  "title_scale": 0.95, "accent_bar": True},
    "Premium":   {"header_override": "band",    "table_grid": False, "title_scale": 1.15, "accent_bar": True},
    "Editorial": {"header_override": "line",    "table_grid": False, "title_scale": 1.2,  "accent_bar": False},
}

PAPER = {"A4": A4, "A3": A3, "A5": A5, "Letter": LETTER, "Legal": LEGAL}
AR_CH = "\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF"
AR_RUN = re.compile(f"[{AR_CH}][{AR_CH}\\s\u060C\u061B\u061F0-9]*")
_reg = {}
_font_choice_cache = {}


class ShapedRTLText(Flowable):
    """Wrap logical Arabic text first, then shape each visual line independently."""
    def __init__(self, text, font_name, font_size, leading, color, shape_text,
                 max_width=None, lines=None, space_before=0, space_after=0):
        super().__init__()
        self.text = text or ""
        self.font_name = font_name
        self.font_size = font_size
        self.leading = max(leading, font_size)
        self.color = color
        self.shape_text = shape_text
        self.max_width = max_width
        self.lines = list(lines) if lines is not None else None
        self.spaceBefore = space_before
        self.spaceAfter = space_after
        self.hAlign = "RIGHT"

    def _wrap_logical(self, width):
        lines = []
        for para in (self.text.splitlines() or [""]):
            words = para.split()
            current = ""
            for word in words:
                candidate = f"{current} {word}".strip()
                if pdfmetrics.stringWidth(self.shape_text(candidate), self.font_name, self.font_size) <= width:
                    current = candidate
                    continue
                if current:
                    lines.append(current)
                    current = ""
                if pdfmetrics.stringWidth(self.shape_text(word), self.font_name, self.font_size) <= width:
                    current = word
                    continue
                # Keep a single overlong token within the frame where possible.
                part = ""
                for char in word:
                    trial = part + char
                    if part and pdfmetrics.stringWidth(self.shape_text(trial), self.font_name, self.font_size) > width:
                        lines.append(part)
                        part = char
                    else:
                        part = trial
                current = part
            if current or not lines:
                lines.append(current)
        return lines or [""]

    def wrap(self, avail_width, avail_height):
        self.width = avail_width
        if self.max_width is not None:
            avail_width = min(avail_width, self.max_width)
        self.lines = self.lines if self.lines is not None else self._wrap_logical(max(1, avail_width))
        self.height = max(1, len(self.lines)) * self.leading
        return self.width, self.height

    def split(self, avail_width, avail_height):
        if self.lines is None:
            self.wrap(avail_width, avail_height)
        count = int(max(0, avail_height) // self.leading)
        if count < 1:
            return []
        if count >= len(self.lines):
            return [self]
        return [
            ShapedRTLText(self.text, self.font_name, self.font_size, self.leading,
                          self.color, self.shape_text, lines=self.lines[:count],
                          space_before=self.spaceBefore),
            ShapedRTLText(self.text, self.font_name, self.font_size, self.leading,
                          self.color, self.shape_text, lines=self.lines[count:],
                          space_after=self.spaceAfter),
        ]

    def draw(self):
        self.canv.setFont(self.font_name, self.font_size)
        self.canv.setFillColor(self.color)
        ascent, _ = pdfmetrics.getAscentDescent(self.font_name, self.font_size)
        y = self.height - ascent
        for line in self.lines or [""]:
            self.canv.drawRightString(self.width, y, self.shape_text(line))
            y -= self.leading


def _register(path, tag):
    name = f"UF_{abs(hash(path)) % 10**8}_{tag}"
    if name not in _reg:
        pdfmetrics.registerFont(TTFont(name, path))
        _reg[name] = 1
    return name


def resolve_fonts(p):
    cache_key = (p.get("font_en", "auto"), p.get("font_ar", "auto"))
    if cache_key in _font_choice_cache:
        return _font_choice_cache[cache_key]
    fl = profiles.fonts()
    def pick(label, need_ar):
        if label != "auto" and label in fl and (not need_ar or fl[label]["arabic"]):
            return fl[label]
        for k in ["Cairo", "Tajawal", "IBM Plex Sans Arabic", "Arial", "Tahoma", "DejaVu Sans"] + list(fl):
            if k in fl and (not need_ar or fl[k]["arabic"]):
                return fl[k]
        return None
    en = pick(p["font_en"], False)
    ar = pick(p["font_ar"], True) or en
    if en is None:
        result = ("Helvetica", "Helvetica-Bold", "Helvetica", "Helvetica-Bold")
    else:
        result = (_register(en["regular"], "r"), _register(en["bold"], "b"),
                  _register(ar["regular"], "r"), _register(ar["bold"], "b"))
    _font_choice_cache[cache_key] = result
    return result


def _col(h, default="#000000"):
    try:
        return colors.HexColor(h if str(h).startswith("#") else "#" + str(h))
    except Exception:
        return colors.HexColor(default)


def _opacity(value, default=0.12):
    """Return a safe opacity in [0, 1], preserving an explicit zero."""
    try:
        number = float(value) if value not in (None, "") else float(default)
    except (TypeError, ValueError):
        number = float(default)
    if not math.isfinite(number):
        number = float(default)
    return max(0.0, min(1.0, number))


def page_count(pdf_bytes):
    """Return the actual page count for export history metadata."""
    import pypdfium2 as pdfium
    return len(pdfium.PdfDocument(pdf_bytes))


def shape_arabic(text):
    """Return a display-ready Arabic line while leaving the input untouched when unavailable."""
    text = "" if text is None else str(text)
    if not ARABIC_OK or not AR_RUN.search(text):
        return text
    try:
        return get_display(arabic_reshaper.reshape(text), base_dir="R")
    except TypeError:
        return get_display(arabic_reshaper.reshape(text))


def text_flowable(text, profile, font_size=10, leading=None, color=None, bold=False):
    """Create one profile-font text flowable, with line-wise shaping for Arabic."""
    raw = "" if text is None else str(text)
    en_r, en_b, ar_r, ar_b = resolve_fonts(profile)
    leading = float(leading or font_size * 1.35)
    txt_color = color if color is not None else _col(profile.get("c_text"), "#1D2433")
    if AR_RUN.search(raw):
        return ShapedRTLText(raw, ar_b if bold else ar_r, font_size, leading,
                             txt_color, shape_arabic)
    style = ParagraphStyle(
        f"PRO_{'B' if bold else 'R'}_{font_size}_{leading}",
        fontName=en_b if bold else en_r,
        fontSize=font_size,
        leading=leading,
        textColor=txt_color,
    )
    return Paragraph(escape(raw).replace("\n", "<br/>"), style)


def image_flowable(file_name, max_width, max_height):
    """Load and scale an image through the configured storage backend."""
    if not file_name:
        return None
    try:
        path = storage.image_path(file_name)
        if not os.path.isfile(path):
            return None
        with PILImage.open(path) as im:
            width, height = im.size
            im.verify()
    except (FileNotFoundError, OSError, PILImage.UnidentifiedImageError, ValueError):
        return None
    draw_w = max_width
    draw_h = draw_w * height / max(1, width)
    if draw_h > max_height:
        draw_h = max_height
        draw_w = draw_h * width / max(1, height)
    return Image(path, width=draw_w, height=draw_h, mask="auto")


def build_custom_document(story, profile, doc_type="Enterprise Pro",
                          template_name="Corporate", options=None, title=None):
    """Render specialized story content with the same profile framing as standard exports."""
    options = options or {}
    tpl = TEMPLATES_META.get(template_name, TEMPLATES_META["Corporate"])
    en_r, en_b, ar_r, ar_b = resolve_fonts(profile)
    size = PAPER.get(options.get("paper") or profile.get("paper", "A4"), A4)
    orient = options.get("orient") or profile.get("orient", "portrait")
    if orient == "landscape":
        size = landscape(size)
    W, H = size
    mg = {"normal": 2.0, "narrow": 1.2, "wide": 2.8}.get(profile.get("margin", "normal"), 2.0) * cm
    mg += float(profile.get("bleed_mm") or 0) * mm
    logo_h = {"s": 0.8, "m": 1.1, "l": 1.5}.get(profile.get("logo_size", "m"), 1.1) * cm
    header_mode = tpl["header_override"] if profile.get("header", "band") != "none" else "none"
    band_h = max(1.7 * cm, logo_h + 0.6 * cm) if header_mode == "band" else (logo_h + 0.5 * cm if header_mode != "none" else 0.7 * cm)
    top_m, bot_m = band_h + 0.8 * cm, 1.8 * cm
    fw, fh = W - 2 * mg, H - top_m - bot_m
    primary = _col(profile.get("c_primary"), "#1F3864")
    accent = _col(profile.get("c_accent"), "#E8590C")
    paper = _col(profile.get("paper_color"), "#FFFFFF")
    company = profile.get("company") or "Industrial Systems"
    logo = profile.get("logo") if profile.get("logo") and os.path.isfile(profile["logo"]) else None
    doc_title = title or doc_type
    today = datetime.date.today().isoformat()
    tile = profiles.texture_tile(profile.get("texture", "none"))
    tile_reader = ImageReader(tile) if tile else None
    bg_reader = None
    if profile.get("bg_image") and os.path.isfile(profile["bg_image"]):
        try:
            bg_image = PILImage.open(profile["bg_image"]).convert("RGBA")
            opacity = _opacity(profile.get("bg_opacity"), 0.12)
            bg_image.putalpha(bg_image.getchannel("A").point(lambda v: int(v * opacity)))
            bg_reader = (ImageReader(bg_image), bg_image.size)
        except (OSError, ValueError):
            bg_reader = None

    def draw_text(canvas, text, x, y, size_pt, bold=False, align="left", fill=colors.black):
        text = "" if text is None else str(text)
        is_ar = bool(AR_RUN.search(text))
        font = (ar_b if bold else ar_r) if is_ar else (en_b if bold else en_r)
        canvas.setFont(font, size_pt)
        canvas.setFillColor(fill)
        shown = shape_arabic(text) if is_ar else text
        if align == "right":
            canvas.drawRightString(x, y, shown)
        elif align == "center":
            canvas.drawCentredString(x, y, shown)
        else:
            canvas.drawString(x, y, shown)

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(paper)
        canvas.rect(0, 0, W, H, stroke=0, fill=1)
        if tile_reader:
            step = 96
            for y in range(0, int(H) + step, step):
                for x in range(0, int(W) + step, step):
                    canvas.drawImage(tile_reader, x, y, width=step, height=step, mask="auto")
        if bg_reader:
            reader, (bw, bh) = bg_reader
            canvas.drawImage(reader, 0, 0, width=W, height=H, mask="auto")
        if header_mode == "band":
            canvas.setFillColor(primary)
            canvas.rect(0, H - band_h, W, band_h, stroke=0, fill=1)
            if tpl["accent_bar"]:
                canvas.setFillColor(accent)
                canvas.rect(0, H - band_h - 2.5, W, 2.5, stroke=0, fill=1)
        elif header_mode in ("line", "minimal"):
            canvas.setStrokeColor(accent if header_mode == "line" else primary)
            canvas.setLineWidth(1.3)
            canvas.line(mg, H - band_h, W - mg, H - band_h)
        if header_mode != "none":
            header_y = H - band_h / 2 - 4
            header_color = colors.white if header_mode == "band" else primary
            x = mg
            if logo:
                try:
                    with PILImage.open(logo) as im:
                        iw, ih = im.size
                    lw = logo_h * iw / max(1, ih)
                    canvas.drawImage(logo, x, H - band_h / 2 - logo_h / 2, width=lw, height=logo_h, mask="auto")
                    x += lw + 0.3 * cm
                except OSError:
                    pass
            draw_text(canvas, company, x, header_y, 11, True, fill=header_color)
            draw_text(canvas, doc_type, W - mg, header_y, 9, False, "right",
                      colors.white if header_mode == "band" else _col(profile.get("c_text"), "#1D2433"))
        if profile.get("wm_type") == "text" and profile.get("wm_text"):
            canvas.saveState()
            canvas.translate(W / 2, H / 2)
            canvas.rotate(float(profile.get("wm_angle") or 45))
            canvas.setFillColor(colors.Color(0.5, 0.5, 0.5, alpha=_opacity(profile.get("wm_opacity"), 0.12)))
            draw_text(canvas, profile["wm_text"], 0, 0, W / 10, True, "center")
            canvas.restoreState()
        canvas.setStrokeColor(colors.HexColor("#D0D5DD"))
        canvas.line(mg, 1.35 * cm, W - mg, 1.35 * cm)
        footer = " | ".join(x for x in (profile.get("contact"), profile.get("social_links")) if x)
        if footer:
            draw_text(canvas, footer[:110], mg, 0.82 * cm, 7.2, fill=colors.HexColor("#555555"))
        if profile.get("show_date") == "1":
            draw_text(canvas, today, W * 0.70, 0.82 * cm, 7.2, align="center", fill=colors.HexColor("#555555"))
        if profile.get("show_page") == "1":
            draw_text(canvas, f"{doc.page}", W - mg, 0.82 * cm, 8, True, "right", primary)
        canvas.restoreState()

    buf = io.BytesIO()
    frame = Frame(mg, bot_m, fw, fh, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, id="pro")
    class ProDoc(BaseDocTemplate):
        pass
    doc = ProDoc(buf, pagesize=size,
                 pageTemplates=[PageTemplate("pro", frames=[frame], onPage=on_page)],
                 title=doc_title, author=company)
    doc.build(story)
    buf.seek(0)
    return buf


def build_document(items, profile, doc_type="tds", template_name="Corporate", options=None):
    options = options or {}
    tpl = TEMPLATES_META.get(template_name, TEMPLATES_META["Corporate"])
    en_r, en_b, ar_r, ar_b = resolve_fonts(profile)

    primary = _col(profile["c_primary"], "#1F3864")
    accent = _col(profile["c_accent"], "#E8590C")
    head_c = _col(profile.get("c_heading") or profile["c_primary"], "#1F3864")
    txt_c = _col(profile["c_text"], "#1D2433")
    zebra = _col(profile["c_zebra"], "#F2F4F8")
    th_txt = _col(profile["c_th_text"], "#FFFFFF")
    paper_c = _col(profile["paper_color"], "#FFFFFF")

    custom_w = float(options.get("custom_w_mm") or 0)
    custom_h = float(options.get("custom_h_mm") or 0)
    size = (custom_w * mm, custom_h * mm) if (custom_w > 50 and custom_h > 50) else PAPER.get(profile["paper"], A4)
    land = (options.get("orient") or profile["orient"]) == "landscape"
    if land:
        size = landscape(size)
    W, H = size
    k = min(W, H) / 595.3

    bleed = float(profile.get("bleed_mm") or 0) * mm
    mg = {"normal": 2.0, "narrow": 1.2, "wide": 2.8}.get(profile["margin"], 2.0) * cm + bleed
    logo_h = {"s": 0.8, "m": 1.1, "l": 1.5}.get(profile["logo_size"], 1.1) * cm
    header_mode = tpl["header_override"] if profile["header"] != "none" else "none"
    band_h = max(1.7 * cm, logo_h + 0.6 * cm) if header_mode == "band" else (logo_h + 0.5 * cm if header_mode != "none" else 0.7 * cm)
    top_m, bot_m = band_h + 0.8 * cm, 1.8 * cm
    fw, fh = W - 2 * mg, H - top_m - bot_m

    lang = options.get("lang") or profile["lang"]
    company = profile["company"] or "Industrial Systems"
    logo = profile["logo"] if profile["logo"] and os.path.exists(profile["logo"]) else None
    today = datetime.date.today().strftime("%Y-%m-%d")
    title = options.get("title") or ("Technical Data Sheet" if doc_type == "tds" else "Master Catalog" if doc_type == "master_catalog" else "Product Catalog")
    draft = doc_type == "tds" and options.get("force_final") != "1" and any(
        (d.get("m") or {}).get("status") != "approved" for d in items
    )

    L = {
        "en": dict(spec="Specification", val="Value", ds="Technical Data Sheet", cat="Product Catalog", master="Master Catalog", toc="Table of Contents", draw="Technical Drawings & Diagrams", notes="Technical Notes", noimg="No approved image for this document.", other="Other products"),
        "ar": dict(spec="المواصفة", val="القيمة المعتمدة", ds="ورقة بيانات فنية", cat="كتالوج المنتجات", master="الكتالوج الشامل", toc="فهرس المحتويات", draw="الرسومات الفنية والمخططات", notes="ملاحظات فنية", noimg="لا توجد صورة معتمدة لهذا المستند.", other="منتجات أخرى")
    }

    def lab(key):
        return L["en"][key] if lang == "en" else L["ar"][key] if lang == "ar" else f"{L['en'][key]} | {L['ar'][key]}"

    def shape(text):
        """Shape and reorder the complete text run so Arabic and Latin stay in sequence."""
        if not ARABIC_OK or not AR_RUN.search(text or ""):
            return text
        try:
            return get_display(arabic_reshaper.reshape(text), base_dir="R")
        except TypeError:  # python-bidi releases before the base_dir argument
            return get_display(arabic_reshaper.reshape(text))

    def T(s, bold=False):
        s = "" if s is None else str(s)
        if AR_RUN.search(s):
            return f'<font name="{ar_b if bold else ar_r}">{escape(shape(s))}</font>'
        return escape(s)

    def P(text, style, bold=False):
        """Create a paragraph with RTL alignment when its content contains Arabic."""
        raw = "" if text is None else str(text)
        if AR_RUN.search(raw):
            return ShapedRTLText(raw, ar_b if bold else ar_r, style.fontSize,
                                 style.leading, style.textColor, shape,
                                 space_before=style.spaceBefore, space_after=style.spaceAfter)
        return Paragraph(T(raw, bold), style)

    use_product_dividers = doc_type in ("product_catalog", "master_catalog") and (doc_type == "master_catalog" or profile.get("dividers", "1") == "1")
    model_toc_level = 2 if use_product_dividers and doc_type in ("product_catalog", "master_catalog") else 1

    def cfont(s, bold=False):
        return (ar_b if bold else ar_r) if AR_RUN.search(s or "") else (en_b if bold else en_r)

    def cdraw(c, x, y, s, sz, bold=False, align="l", color=None):
        sz = sz * k
        s = "" if s is None else str(s)
        fn = cfont(s, bold)
        if AR_RUN.search(s):
            s = shape(s)
        c.setFont(fn, sz)
        if color is not None:
            c.setFillColor(color)
        {"l": c.drawString, "r": c.drawRightString, "c": c.drawCentredString}[align](x, y, s)

    tile = profiles.texture_tile(profile.get("texture", "none"))
    tile_r = ImageReader(tile) if tile else None

    bg_reader = None
    if profile.get("bg_image") and os.path.exists(profile["bg_image"]):
        bg_im = PILImage.open(profile["bg_image"]).convert("RGBA")
        bg_alpha = _opacity(profile.get("bg_opacity"), 0.12)
        alpha_band = bg_im.split()[3].point(lambda v: int(v * bg_alpha))
        bg_im.putalpha(alpha_band)
        bg_reader = (ImageReader(bg_im), bg_im.size)

    def render_paper_layers(c, is_cover=False):
        c.setFillColor(paper_c)
        c.rect(0, 0, W, H, stroke=0, fill=1)
        scope = profile.get("bg_scope", "all")
        apply_bg = (scope == "all") or (scope == "cover" and is_cover) or (scope == "inner" and not is_cover)

        if apply_bg and tile_r:
            step = 96
            yy = 0
            while yy < H:
                xx = 0
                while xx < W:
                    c.drawImage(tile_r, xx, yy, width=step, height=step, mask="auto")
                    xx += step
                yy += step

        if apply_bg and bg_reader:
            r, (bw, bh) = bg_reader
            rep = profile.get("bg_repeat", "cover")
            if rep == "cover":
                c.drawImage(r, 0, 0, width=W, height=H, mask="auto")
            elif rep == "center":
                ww = W * 0.65; hh = ww * bh / bw
                c.drawImage(r, (W - ww) / 2, (H - hh) / 2, width=ww, height=hh, mask="auto")
            else:
                yy = 0
                while yy < H:
                    xx = 0
                    while xx < W:
                        c.drawImage(r, xx, yy, width=160, height=160 * bh / bw, mask="auto")
                        xx += 160
                    yy += 160 * bh / bw

        bstyle = profile.get("border_style", "none")
        if bstyle != "none":
            c.setStrokeColor(primary if bstyle != "accent_frame" else accent)
            c.setLineWidth(1.5 if bstyle in ("double", "accent_frame") else 0.7)
            c.rect(mg * 0.5, bot_m * 0.6, W - mg, H - bot_m * 1.1, stroke=1, fill=0)
            if bstyle == "double":
                c.setLineWidth(0.5)
                c.rect(mg * 0.5 + 4, bot_m * 0.6 + 4, W - mg - 8, H - bot_m * 1.1 - 8, stroke=1, fill=0)

        dstyle = profile.get("decor_style", "none")
        if dstyle == "side_stripe":
            c.setFillColor(accent)
            c.rect(0, 0, 6 * mm, H, stroke=0, fill=1)
        elif dstyle == "corner_bars":
            c.setFillColor(accent)
            c.rect(0, H - 8 * mm, 45 * mm, 8 * mm, stroke=0, fill=1)
            c.rect(W - 45 * mm, 0, 45 * mm, 8 * mm, stroke=0, fill=1)

    def deco_inner(c, doc):
        c.saveState()
        render_paper_layers(c, is_cover=False)
        yc = H - band_h / 2
        if header_mode == "band":
            c.setFillColor(primary)
            c.rect(0, H - band_h, W, band_h, stroke=0, fill=1)
            if tpl["accent_bar"]:
                c.setFillColor(accent)
                c.rect(0, H - band_h - 2.5, W, 2.5, stroke=0, fill=1)
        elif header_mode in ("line", "minimal"):
            c.setStrokeColor(accent if header_mode == "line" else primary)
            c.setLineWidth(1.5 if header_mode == "line" else 0.6)
            c.line(mg, H - band_h, W - mg, H - band_h)

        if header_mode != "none":
            col = colors.white if header_mode == "band" else primary
            x = mg
            if logo:
                iw, ih = PILImage.open(logo).size
                lw = logo_h * iw / ih
                c.drawImage(logo, x, yc - logo_h / 2, width=lw, height=logo_h, mask="auto")
                x += lw + 0.3 * cm
            cdraw(c, x, yc - 4, company, 12, True, "l", col)
            doc_lbl = lab("ds") if doc_type == "tds" else (lab("master") if doc_type == "master_catalog" else lab("cat"))
            cdraw(c, W - mg, yc - 3.5, doc_lbl, 9.5, False, "r", colors.white if header_mode == "band" else txt_c)

        if draft:
            c.saveState()
            c.translate(W / 2, H / 2)
            c.rotate(45)
            c.setFillColor(colors.Color(0.6, 0.6, 0.6, alpha=0.18))
            c.setFont(en_b, W / 6.5)
            c.drawCentredString(0, 0, "DRAFT")
            c.restoreState()
        elif profile.get("wm_type") == "text" and profile.get("wm_text"):
            c.saveState()
            c.translate(W / 2, H / 2)
            c.rotate(float(profile.get("wm_angle") or 45))
            c.setFillColor(colors.Color(0.5, 0.5, 0.5, alpha=_opacity(profile.get("wm_opacity"), 0.12)))
            cdraw(c, 0, 0, profile["wm_text"], W / 9, True, "c")
            c.restoreState()

        c.setStrokeColor(colors.HexColor("#D0D5DD"))
        c.setLineWidth(0.5)
        c.line(mg, 1.35 * cm, W - mg, 1.35 * cm)
        footer_txt = " | ".join(x for x in [profile.get("contact"), profile.get("social_links")] if x)
        if footer_txt:
            cdraw(c, mg, 0.85 * cm, footer_txt[:95], 7.5, False, "l", colors.HexColor("#555555"))
        if profile.get("show_date") == "1":
            cdraw(c, W * 0.72, 0.85 * cm, today, 7.5, False, "c", colors.HexColor("#555555"))
        if profile.get("show_page") == "1":
            cdraw(c, W - mg, 0.85 * cm, f"{doc.page}", 8.5, True, "r", primary)
        c.restoreState()

    def deco_cover(c, doc):
        c.saveState()
        render_paper_layers(c, is_cover=True)
        c.setFillColor(primary)
        c.rect(0, H * 0.46, W, H * 0.54, stroke=0, fill=1)
        c.setFillColor(accent)
        c.rect(0, H * 0.46 - 5, W, 5, stroke=0, fill=1)

        if logo:
            iw, ih = PILImage.open(logo).size
            lh = 2.2 * cm
            c.drawImage(logo, mg, H - mg - lh, width=lh * iw / ih, height=lh, mask="auto")
        cdraw(c, mg, H - mg - 3.0 * cm, company, 14, True, "l", colors.white)

        y = H * 0.66
        fs = min(36, W / 14)
        title_font = cfont(title, True)
        for ln in simpleSplit(title, title_font, fs, W - 2 * mg):
            c.setFont(cfont(title, True), fs)
            c.setFillColor(colors.white)
            if AR_RUN.search(ln):
                c.drawRightString(W - mg, y, shape(ln))
            else:
                c.drawString(mg, y, ln)
            y -= fs * 1.25

        sub = profile.get("cover_sub") or options.get("subtitle") or ""
        if sub:
            cdraw(c, mg, H * 0.38, sub, 14, False, "l", txt_c)
        cdraw(c, mg, 1.5 * cm, f"{company}  •  {today}", 10, False, "l", colors.HexColor("#555555"))
        c.restoreState()

    base_fs = float(profile.get("font_size_base") or 9.5)
    tscale = tpl["title_scale"]

    def S(name, **kw):
        b = dict(fontName=en_r, textColor=txt_c)
        b.update(kw)
        for key in ("fontSize", "leading"):
            if key in b:
                b[key] = b[key] * k
        return ParagraphStyle(name, **b)

    st_title = S("H_TITLE", fontName=en_b, fontSize=22 * tscale, leading=26 * tscale, textColor=head_c)
    st_ar = S("H_AR", fontName=ar_b, fontSize=14 * tscale, leading=20 * tscale, textColor=head_c)
    st_sub = S("SUB", fontSize=10, leading=14, textColor=colors.HexColor("#4B5563"))
    st_body = S("BODY", fontSize=base_fs, leading=base_fs * 1.4)
    st_cell = S("CELL", fontSize=base_fs, leading=base_fs * 1.32)
    st_cell_b = S("CELLB", fontName=en_b, fontSize=base_fs, leading=base_fs * 1.32)
    st_th = S("TH", fontName=en_b, fontSize=base_fs, leading=base_fs * 1.32, textColor=th_txt)
    st_sec = S("SEC", fontName=en_b, fontSize=26 * tscale, leading=32 * tscale, textColor=head_c)

    def make_scaled_img(file_name, max_w, max_h):
        try:
            path = storage.image_path(file_name)
            if not os.path.isfile(path):
                return None
            with PILImage.open(path) as im:
                w0, h0 = im.size
                im.verify()
        except (FileNotFoundError, OSError, PILImage.UnidentifiedImageError, ValueError):
            # A deleted Drive object or corrupt cached image must not abort an export.
            return None
        iw, ih = max_w, max_w * h0 / max(1, w0)
        if ih > max_h:
            ih = max_h
            iw = ih * w0 / max(1, h0)
        return Image(path, width=iw, height=ih, mask="auto")

    def render_item_block(d):
        m = d["m"]
        prod = d.get("product") or {}
        specs = d.get("specs") or []
        imgs = d.get("images") or []
        out = []

        hdr_txt = m["code"] if m.get("code") else prod.get("name", "")
        p_head = P(hdr_txt, st_title, True)
        p_head._toc = (model_toc_level, T(hdr_txt))
        out.append(p_head)

        ar_lbl = m.get("ar_name") or prod.get("ar_name") or ""
        if ar_lbl:
            out.append(P(ar_lbl, st_ar, True))

        meta_parts = [prod.get("name"), m.get("series"), d.get("family_name")]
        if (options.get("show_supplier") or profile.get("show_supplier")) == "1":
            sup = prod.get("supplier") or m.get("supplier")
            if sup:
                meta_parts.append(f"Supplier: {sup}")
        meta_str = "  •  ".join(x for x in meta_parts if x)
        if meta_str:
            out.append(P(meta_str, st_sub))

        if options.get("show_desc", "1") == "1" and prod.get("description"):
            out.append(Spacer(1, 0.2 * cm))
            out.append(P(prod["description"], st_body))

        out.append(Spacer(1, 0.35 * cm))

        approval_field = "ok_sheet" if doc_type == "tds" else "ok_catalog"
        approved_imgs = [im for im in imgs if int(im.get(approval_field) or 0) == 1]
        main_imgs = [im for im in approved_imgs if im.get("image_role") not in ("technical_drawing", "diagram")]
        tech_imgs = [im for im in approved_imgs if im.get("image_role") in ("technical_drawing", "diagram")]

        img_flow = None
        for im in main_imgs:
            img_flow = make_scaled_img(im["file"], fw * (0.38 if land else 0.55), fh * (0.72 if land else 0.36))
            if img_flow:
                break

        show_specs = options.get("show_specs", "1") == "1"
        tb = None
        if show_specs:
            rows = [[P(lab("spec"), st_th, True), P(lab("val"), st_th, True)]]
            for sp in specs:
                if sp.get("hidden") or sp.get("deleted"):
                    continue
                _, _, ffull = db.compute_final_spec(sp)
                if not ffull:
                    continue
                rows.append([P(sp["attr"], st_cell_b, True), P(ffull, st_cell)])
            if len(rows) > 1:
                tw = fw * (0.58 if land else 1.0)
                tb = Table(rows, colWidths=[tw * 0.40, tw * 0.60], repeatRows=1)
                ts = [
                    ("BACKGROUND", (0, 0), (-1, 0), primary),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.Color(1, 1, 1, alpha=0.0), zebra]),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
                if tpl["table_grid"]:
                    ts.append(("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")))
                else:
                    ts.append(("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#D0D5DD")))
                tb.setStyle(TableStyle(ts))

        if land and img_flow and tb:
            lay = Table([[img_flow, tb]], colWidths=[fw * 0.40, fw * 0.60])
            lay.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
            out.append(lay)
        else:
            noimg_style = S("NO_APPROVED_IMAGE", fontSize=9, leading=13, textColor=colors.HexColor("#888888"))
            if not img_flow:
                out.append(P(lab("noimg"), noimg_style))
                out.append(Spacer(1, 0.2 * cm))
            if img_flow:
                out.append(img_flow)
                out.append(Spacer(1, 0.3 * cm))
            if tb:
                out.append(tb)

        if options.get("show_extra_images", "1") == "1" and len(main_imgs) > 1:
            out.append(Spacer(1, 0.3 * cm))
            row_cells = []
            for ex in main_imgs[1:4]:
                imf = make_scaled_img(ex["file"], fw * 0.28, fh * 0.20)
                if imf:
                    row_cells.append(imf)
            if row_cells:
                out.append(Table([row_cells], colWidths=[fw / len(row_cells)] * len(row_cells)))

        if tech_imgs:
            out.append(Spacer(1, 0.35 * cm))
            out.append(P(lab("draw"), st_cell_b, True))
            for ti in tech_imgs[:2]:
                tif = make_scaled_img(ti["file"], fw * 0.85, fh * 0.28)
                if tif:
                    out.append(Spacer(1, 0.15 * cm))
                    out.append(tif)

        if options.get("tech_notes"):
            out.append(Spacer(1, 0.3 * cm))
            out.append(P(lab("notes") + ": " + options["tech_notes"], st_sub))

        return out

    story = []
    use_cover = (doc_type != "tds") and (options.get("cover", profile["cover"]) == "1")
    if use_cover:
        story += [NextPageTemplate("normal"), Spacer(1, 1), PageBreak()]

    if options.get("intro_text") and doc_type != "tds":
        story.append(P(options["intro_text"], st_body))
        story.append(PageBreak())

    use_toc = (doc_type != "tds") and (options.get("toc", profile["toc"]) == "1")
    if use_toc:
        story.append(P(lab("toc"), st_sec, True))
        story.append(Spacer(1, 0.4 * cm))
        toc = TableOfContents()
        toc.dotsMinLevel = 0
        toc.levelStyles = [
            S("toc0", fontName=en_b, fontSize=12, leading=18, spaceBefore=6),
            S("toc1", fontSize=10, leading=14, leftIndent=14),
            S("toc2", fontSize=9, leading=13, leftIndent=28),
        ]
        story += [toc, PageBreak()]

    last_cat = object()
    last_product = object()
    for idx, d in enumerate(items):
        cat_lbl = d.get("family_name") or "General"
        if doc_type == "master_catalog" and cat_lbl != last_cat:
            last_cat = cat_lbl
            last_product = object()
            h_sec = P(cat_lbl, st_sec, True)
            h_sec._toc = (0, T(cat_lbl))
            story += [Spacer(1, fh * 0.2), h_sec, PageBreak()]
        prod = d.get("product") or {}
        product_key = prod.get("id") if prod.get("id") is not None else prod.get("name")
        use_divider = use_product_dividers
        if use_divider and product_key != last_product:
            last_product = product_key
            product_title = prod.get("name") or lab("other")
            h_prod = P(product_title, st_sec, True)
            h_prod._toc = (1, T(product_title))
            story.append(Spacer(1, fh * 0.2))
            story.append(h_prod)
            if prod.get("ar_name"):
                story.append(P(prod["ar_name"], st_ar, True))
            if options.get("show_desc", "1") == "1" and prod.get("description"):
                story.append(Spacer(1, 0.3 * cm))
                story.append(P(prod["description"], st_body))
            story.append(PageBreak())
        story += render_item_block(d)
        if idx < len(items) - 1:
            story.append(PageBreak())

    class CatalogDoc(BaseDocTemplate):
        def afterFlowable(self, fl):
            t = getattr(fl, "_toc", None)
            if t:
                self.notify("TOCEntry", (t[0], t[1], self.page))

    buf = io.BytesIO()
    f_inner = Frame(mg, bot_m, fw, fh, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, id="inner")
    f_full = Frame(0, 0, W, H, id="full")
    pts = ([PageTemplate("cover", frames=[f_full], onPage=deco_cover)] if use_cover else []) + [
        PageTemplate("normal", frames=[f_inner], onPage=deco_inner)
    ]
    doc = CatalogDoc(buf, pagesize=size, pageTemplates=pts, title=title, author=company)
    (doc.multiBuild if use_toc else doc.build)(story)
    buf.seek(0)
    return buf
