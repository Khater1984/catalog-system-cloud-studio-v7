"""Enterprise Pro tools using the shared data, storage, and PDF services."""
import io
import json
import os
import re
import uuid
import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlsplit

from flask import Blueprint, abort, flash, g, redirect, render_template_string, request, send_file, url_for
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import PageBreak, Spacer, Table, TableStyle
from werkzeug.utils import secure_filename

import auth
import db
import export_center
import pdf_engine
import profiles
import storage

bp = Blueprint("enterprise_pro", __name__)

MAX_COMPARE_MODELS = 6
MAX_SUBMITTAL_MODELS = 25
MAX_ATTACHMENTS = 10
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 24 * 1024 * 1024

# unit -> (other unit, multiplier, additive offset, decimals, system)
UNIT_CONVERSIONS = {
    "kw": ("HP", "1.34102", "0", 2, "metric"),
    "hp": ("kW", "0.74570", "0", 2, "imperial"),
    "bar": ("PSI", "14.5038", "0", 1, "metric"),
    "psi": ("bar", "0.06895", "0", 2, "imperial"),
    "mm": ("in", "0.03937", "0", 2, "metric"),
    "in": ("mm", "25.4000", "0", 1, "imperial"),
    "m": ("ft", "3.28084", "0", 2, "metric"),
    "ft": ("m", "0.30480", "0", 2, "imperial"),
    "kg": ("lbs", "2.20462", "0", 1, "metric"),
    "lb": ("kg", "0.45359237", "0", 2, "imperial"),
    "lbs": ("kg", "0.45359237", "0", 2, "imperial"),
    "l/min": ("GPM", "0.264172", "0", 2, "metric"),
    "gpm": ("L/min", "3.785412", "0", 1, "imperial"),
    "m3/h": ("GPM", "4.40287", "0", 2, "metric"),
    "c": ("°F", "1.8", "32", 1, "metric"),
    "°c": ("°F", "1.8", "32", 1, "metric"),
    "f": ("°C", "0.5555555556", "-17.77777778", 1, "imperial"),
    "°f": ("°C", "0.5555555556", "-17.77777778", 1, "imperial"),
}

_NUMERIC_VALUE = re.compile(
    r"^\s*([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:[.,]\d+)?|[+-]?\.\d+)\s*([a-zA-Z°³/]+)?\s*$"
)
_UNIT_ALIASES = {"m³/h": "m3/h", "lbm": "lb", "pound": "lb", "pounds": "lb"}
_METRIC_UNITS = {"kw", "bar", "mm", "m", "kg", "l/min", "m3/h", "c", "°c"}
_IMPERIAL_UNITS = {"hp", "psi", "in", "ft", "lb", "lbs", "gpm", "f", "°f"}


def _unit_key(unit):
    key = (unit or "").strip().lower().replace("³", "3").replace("℉", "°f").replace("℃", "°c")
    return _UNIT_ALIASES.get(key, key)


def _decimal_value(text):
    raw = (text or "").strip()
    if "," in raw and "." not in raw:
        if raw.count(",") == 1 and len(raw.rsplit(",", 1)[1]) != 3:
            raw = raw.replace(",", ".")
        else:
            raw = raw.replace(",", "")
    else:
        raw = raw.replace(",", "")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _number_text(value, decimals):
    quantum = Decimal("1").scaleb(-decimals)
    rounded = value.quantize(quantum, rounding=ROUND_HALF_UP)
    return format(rounded, "f").rstrip("0").rstrip(".") if decimals else format(rounded, "f")


def format_spec_with_units(val_str, unit_str="", unit_mode="original"):
    """Convert one scalar spec value without guessing at ranges or compound values."""
    if unit_mode not in {"original", "metric", "imperial", "dual"}:
        raise ValueError("نظام الوحدات غير صالح.")
    raw_value = str(val_str or "").strip()
    raw_unit = str(unit_str or "").strip()
    if len(raw_value) > 256 or len(raw_unit) > 32:
        return raw_value
    base_display = raw_value if raw_unit and raw_unit.lower() in raw_value.lower() else " ".join(x for x in (raw_value, raw_unit) if x)
    if unit_mode == "original" or not raw_value:
        return base_display

    match = _NUMERIC_VALUE.fullmatch(raw_value)
    if not match:
        return base_display
    number_text, embedded_unit = match.groups()
    selected_unit = embedded_unit or raw_unit
    if embedded_unit and raw_unit and _unit_key(embedded_unit) != _unit_key(raw_unit):
        return base_display
    key = _unit_key(selected_unit)
    conversion = UNIT_CONVERSIONS.get(key)
    if not conversion:
        return base_display
    if unit_mode == "metric" and key in _METRIC_UNITS:
        return base_display
    if unit_mode == "imperial" and key in _IMPERIAL_UNITS:
        return base_display

    number = _decimal_value(number_text)
    if number is None:
        return base_display
    target_unit, multiplier, offset, decimals, _system = conversion
    try:
        converted = number * Decimal(multiplier) + Decimal(offset)
        converted_display = f"{_number_text(converted, decimals)} {target_unit}"
    except (InvalidOperation, OverflowError):
        return base_display
    if unit_mode == "dual":
        return f"{base_display} ({converted_display})"
    if unit_mode == "metric" and key in _IMPERIAL_UNITS:
        return converted_display
    if unit_mode == "imperial" and key in _METRIC_UNITS:
        return converted_display
    return base_display


def _spec_value(spec, unit_mode):
    value, unit, full = db.compute_final_spec(spec)
    return format_spec_with_units(value, unit, unit_mode) if value else "—"


def _a3_inner_width(profile):
    margin = {"normal": 2.0, "narrow": 1.2, "wide": 2.8}.get(profile.get("margin", "normal"), 2.0) * cm
    margin += float(profile.get("bleed_mm") or 0) * 72 / 25.4
    return pdf_engine.PAPER["A3"][1] - 2 * margin


def _safe_model_ids(values, minimum=1, maximum=MAX_SUBMITTAL_MODELS):
    ids = []
    for value in values:
        if str(value).isdigit():
            candidate = int(value)
            if candidate > 0 and candidate not in ids:
                ids.append(candidate)
    if not minimum <= len(ids) <= maximum:
        raise ValueError(f"يرجى اختيار ما بين {minimum} و{maximum} طرازًا.")
    return ids


def _profile(profile_id=None):
    c = db.conn()
    try:
        return profiles.load(c, profile_id)
    finally:
        c.close()


def _persist_and_send(pdf_buf, filename, doc_type, profile, title, options):
    pdf_bytes = pdf_buf.getvalue()
    actual_pages = pdf_engine.page_count(pdf_bytes)
    safe_name = secure_filename(filename) or "enterprise_export.pdf"
    stem, ext = os.path.splitext(safe_name)
    stored_name = f"{stem[:100]}_{uuid.uuid4().hex[:10]}{ext or '.pdf'}"
    drive_id = None
    c = db.conn()
    try:
        drive_id = storage.save_export_pdf(stored_name, pdf_bytes)
        profile_id = (profile.get("id") if profile else None) or None
        c.execute("""INSERT INTO export_versions(doc_type,template_name,profile_id,title,file_name,options_json,page_count,drive_id)
                     VALUES(?,?,?,?,?,?,?,?)""",
                  (doc_type, "Enterprise Pro", profile_id, title, stored_name,
                   json.dumps(options or {}, ensure_ascii=False), actual_pages, drive_id))
        db.log(c, "export", 0, doc_type, f"{stored_name}: {actual_pages} pages")
        c.commit()
    except Exception:
        c.rollback()
        storage.discard_export_pdf(stored_name, drive_id)
        raise
    finally:
        c.close()
    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf", as_attachment=True,
                     download_name=safe_name)


def _approved_model(mid):
    c = db.conn()
    try:
        return c.execute("""SELECT m.*, p.name product_name, p.active product_active
                            FROM models m LEFT JOIN products p ON p.id=m.product_id
                            WHERE m.id=? AND m.active=1 AND m.visible=1 AND m.status='approved'
                              AND COALESCE(p.active,1)=1""", (mid,)).fetchone()
    finally:
        c.close()


def build_comparison_pdf(model_ids, profile, unit_mode="dual", title="Technical Comparison Matrix"):
    if unit_mode not in {"original", "metric", "imperial", "dual"}:
        raise ValueError("نظام الوحدات غير صالح.")
    ids = _safe_model_ids(model_ids, minimum=2, maximum=MAX_COMPARE_MODELS)
    items = []
    for mid in ids:
        model = _approved_model(mid)
        if not model:
            raise ValueError(f"الطراز رقم {mid} غير نشط أو غير ظاهر أو غير معتمد للمقارنة.")
        rows = export_center.collect_items("tds", model_id=mid)
        if rows:
            items.append(rows[0])
    if len(items) < 2:
        raise ValueError("لم يُعثر على طرازين صالحين للمقارنة.")

    attr_order, seen, model_values = [], set(), []
    for item in items:
        values = {}
        for spec in item.get("specs", []):
            if spec.get("hidden") or spec.get("deleted"):
                continue
            attr = (spec.get("attr") or "").strip()
            if not attr:
                continue
            key = attr.casefold()
            value = _spec_value(spec, unit_mode)
            values[key] = value
            if key not in seen:
                seen.add(key)
                attr_order.append((key, attr))
        model_values.append(values)

    header_style = colors.HexColor("#FFFFFF")
    primary = pdf_engine._col(profile.get("c_primary"), "#1F3864")
    accent = pdf_engine._col(profile.get("c_accent"), "#E8590C")
    zebra = pdf_engine._col(profile.get("c_zebra"), "#F2F4F8")
    attr_w = 150
    total_w = _a3_inner_width(profile)
    model_w = max(95, (total_w - attr_w) / len(items))
    rows = [[pdf_engine.text_flowable("Specification / المواصفة", profile, 9, 12, header_style, True)]]
    for item in items:
        model = item["m"]
        product_name = (item.get("product") or {}).get("name") or ""
        rows[0].append(pdf_engine.text_flowable(f"{model.get('code') or ''}\n{product_name}", profile, 8.5, 11, header_style, True))

    diff_rows = []
    for row_idx, (key, attr) in enumerate(attr_order, start=1):
        values = [mp.get(key, "—") for mp in model_values]
        if len(set(values)) > 1:
            diff_rows.append(row_idx)
        rows.append([pdf_engine.text_flowable(attr, profile, 8.5, 11, None, True)] +
                    [pdf_engine.text_flowable(value, profile, 8, 10) for value in values])

    table = Table(rows, colWidths=[attr_w] + [model_w] * len(items), repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), primary),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for row_idx in range(1, len(rows)):
        if row_idx in diff_rows:
            style.append(("BACKGROUND", (0, row_idx), (-1, row_idx), colors.HexColor("#FFF4E5")))
            style.append(("TEXTCOLOR", (0, row_idx), (0, row_idx), accent))
        elif row_idx % 2 == 0:
            style.append(("BACKGROUND", (0, row_idx), (-1, row_idx), zebra))
    table.setStyle(TableStyle(style))

    company = profile.get("company") or ""
    story = [
        pdf_engine.text_flowable(f"{company} — {title}".strip(" —"), profile, 20, 25,
                                 pdf_engine._col(profile.get("c_heading") or profile.get("c_primary"), "#1F3864"), True),
        pdf_engine.text_flowable(
            f"Models compared: {len(items)} | Units: {unit_mode.upper()} | Highlighted rows differ, including missing values.",
            profile, 9, 13, colors.HexColor("#475569")),
        Spacer(1, 0.35 * cm), table,
    ]
    return pdf_engine.build_custom_document(
        story, profile, doc_type="Technical Comparison Matrix", title=title,
        options={"paper": "A3", "orient": "landscape"})


def build_family_matrix_pdf(product_id, profile, unit_mode="original"):
    if unit_mode not in {"original", "metric", "imperial", "dual"}:
        raise ValueError("نظام الوحدات غير صالح.")
    c = db.conn()
    try:
        product = c.execute("SELECT * FROM products WHERE id=? AND active=1", (product_id,)).fetchone()
        if not product:
            raise ValueError("المنتج غير موجود أو غير نشط.")
        product = dict(product)
        product_images = [r["file"] for r in c.execute("""SELECT i.file FROM product_image_links pl
                            JOIN images i ON i.id=pl.image_id
                            WHERE pl.product_id=? AND i.disabled=0 AND pl.ok_catalog=1
                            ORDER BY pl.is_primary DESC, pl.sort_order, pl.id""", (product_id,))]
    finally:
        c.close()

    items = export_center.collect_items("product_catalog", product_id=product_id)
    if not items:
        raise ValueError("لا توجد طرازات نشطة وظاهرة لهذا المنتج.")
    all_attrs, seen = [], set()
    for item in items:
        for spec in item.get("specs", []):
            if spec.get("hidden") or spec.get("deleted"):
                continue
            attr = (spec.get("attr") or "").strip()
            key = attr.casefold()
            if attr and key not in seen:
                seen.add(key)
                all_attrs.append((key, attr))

    primary = pdf_engine._col(profile.get("c_primary"), "#1F3864")
    zebra = pdf_engine._col(profile.get("c_zebra"), "#F2F4F8")
    white = colors.white
    story = [pdf_engine.text_flowable(
        f"{product.get('name') or ''}\n{product.get('ar_name') or ''}".strip(),
        profile, 19, 24, pdf_engine._col(profile.get("c_heading") or profile.get("c_primary"), "#1F3864"), True)]
    if product.get("description"):
        story.append(pdf_engine.text_flowable(product["description"], profile, 9.5, 13, colors.HexColor("#475569")))
        story.append(Spacer(1, 0.2 * cm))

    hero = None
    candidate_images = list(product_images)
    if not candidate_images:
        candidate_images = [im.get("file") for item in items for im in item.get("images", [])
                           if im.get("ok_catalog") and im.get("file")]
    for image_name in candidate_images:
        hero = pdf_engine.image_flowable(image_name, 330, 150)
        if hero:
            break
    if hero:
        story.extend([hero, Spacer(1, 0.3 * cm)])

    chunk_size = 8
    attr_chunks = [all_attrs[i:i + chunk_size] for i in range(0, len(all_attrs), chunk_size)] or [[]]
    for chunk_idx, attrs in enumerate(attr_chunks):
        if chunk_idx:
            story.append(PageBreak())
            story.append(pdf_engine.text_flowable(
                f"{product.get('name') or ''} — Technical Matrix (continued {chunk_idx + 1})",
                profile, 14, 18, pdf_engine._col(profile.get("c_heading") or profile.get("c_primary"), "#1F3864"), True))
        headers = [
            pdf_engine.text_flowable("Model / الطراز", profile, 8.5, 11, white, True),
            pdf_engine.text_flowable("Approval status / حالة الاعتماد", profile, 8, 10, white, True),
        ]
        headers.extend(pdf_engine.text_flowable(name, profile, 8, 10, white, True) for _key, name in attrs)
        rows = [headers]
        for item in items:
            spec_map = {}
            for spec in item.get("specs", []):
                if not spec.get("hidden") and not spec.get("deleted"):
                    spec_map[(spec.get("attr") or "").strip().casefold()] = _spec_value(spec, unit_mode)
            row = [
                pdf_engine.text_flowable(item["m"].get("code") or "", profile, 8, 10, None, True),
                pdf_engine.text_flowable("Approved" if item["m"].get("status") == "approved" else "Draft", profile, 7.5, 9.5),
            ]
            row.extend(pdf_engine.text_flowable(spec_map.get(key, "—"), profile, 7.5, 9.5) for key, _name in attrs)
            rows.append(row)
        col_width = _a3_inner_width(profile) / max(1, len(headers))
        table = Table(rows, colWidths=[col_width] * len(headers), repeatRows=1, hAlign="LEFT")
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), primary),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
        for row_idx in range(2, len(rows), 2):
            style.append(("BACKGROUND", (0, row_idx), (-1, row_idx), zebra))
        table.setStyle(TableStyle(style))
        story.append(table)

    title = f"{product.get('name') or 'Product'} Family Matrix"
    return pdf_engine.build_custom_document(
        story, profile, doc_type="Family Spec Matrix", title=title,
        options={"paper": "A3", "orient": "landscape"})


def _read_attachments(file_storage_list):
    from pypdf import PdfReader

    uploads = [f for f in (file_storage_list or []) if f and f.filename]
    if len(uploads) > MAX_ATTACHMENTS:
        raise ValueError(f"الحد الأقصى للمرفقات هو {MAX_ATTACHMENTS} ملفات PDF.")
    total = 0
    attachments = []
    for upload in uploads:
        original_name = upload.filename or ""
        if not original_name.lower().endswith(".pdf"):
            raise ValueError("المرفقات يجب أن تكون ملفات PDF فقط.")
        name = secure_filename(original_name) or "attachment.pdf"
        data = upload.read(MAX_ATTACHMENT_BYTES + 1)
        if not data or len(data) > MAX_ATTACHMENT_BYTES:
            raise ValueError("حجم كل مرفق PDF يجب ألا يتجاوز 8 ميجابايت.")
        total += len(data)
        if total > MAX_TOTAL_ATTACHMENT_BYTES:
            raise ValueError("إجمالي المرفقات يجب ألا يتجاوز 24 ميجابايت.")
        try:
            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted or not reader.pages or len(reader.pages) > 1000:
                raise ValueError("ملف PDF فارغ أو مشفّر أو يتجاوز الحد المسموح لعدد الصفحات.")
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"المرفق {name} ليس ملف PDF صالحًا.") from exc
        attachments.append((name, data))
    return attachments


def build_submittal_package(project_name, client_name, model_ids, profile, attachments=None):
    from pypdf import PdfWriter

    ids = _safe_model_ids(model_ids, minimum=1, maximum=MAX_SUBMITTAL_MODELS)
    models = []
    c = db.conn()
    try:
        for mid in ids:
            row = c.execute("""SELECT m.id, m.code, m.status, p.name product_name
                               FROM models m LEFT JOIN products p ON p.id=m.product_id
                               WHERE m.id=? AND m.active=1 AND m.visible=1 AND m.status='approved'
                                 AND COALESCE(p.active,1)=1""", (mid,)).fetchone()
            if not row:
                raise ValueError(f"الطراز رقم {mid} غير معتمد أو غير متاح لحزمة التقديم.")
            models.append(dict(row))
    finally:
        c.close()

    project_name = (project_name or "").strip()[:160]
    client_name = (client_name or "").strip()[:160]
    if not project_name or not client_name:
        raise ValueError("يرجى إدخال اسم المشروع واسم العميل.")

    company = profile.get("company") or "Shams Al-Khaleej"
    primary = pdf_engine._col(profile.get("c_primary"), "#1F3864")
    cover_story = [
        Spacer(1, 2.2 * cm),
        pdf_engine.text_flowable("TECHNICAL SUBMITTAL PACKAGE\nحزمة التقديم الفني", profile, 24, 32, primary, True),
        Spacer(1, 0.6 * cm),
        pdf_engine.text_flowable(f"Project / المشروع: {project_name}", profile, 11, 16),
        pdf_engine.text_flowable(f"Client / Consultant / العميل أو الاستشاري: {client_name}", profile, 11, 16),
        pdf_engine.text_flowable(f"Submitted by / مقدّم من: {company}", profile, 11, 16),
        pdf_engine.text_flowable(f"Date / التاريخ: {datetime.date.today().isoformat()}", profile, 11, 16),
        Spacer(1, 1.2 * cm),
        pdf_engine.text_flowable(f"{len(models)} approved model(s) / طراز معتمد", profile, 14, 19, primary, True),
    ]
    cover = pdf_engine.build_custom_document(
        cover_story, profile, doc_type="Technical Submittal Cover", title=f"Submittal Cover - {project_name}")

    story = [
        pdf_engine.text_flowable("TRANSMITTAL LETTER / خطاب الإحالة", profile, 19, 25, primary, True),
        Spacer(1, 0.35 * cm),
        pdf_engine.text_flowable(f"Date / التاريخ: {datetime.date.today().isoformat()}", profile, 10, 14),
        pdf_engine.text_flowable(f"Project / المشروع: {project_name}", profile, 10, 14),
        pdf_engine.text_flowable(f"Client / Consultant / العميل أو الاستشاري: {client_name}", profile, 10, 14),
        Spacer(1, 0.3 * cm),
        pdf_engine.text_flowable(
            "Dear Project Team,\nPlease find the approved technical data sheets and supporting documents listed below for review.",
            profile, 10, 15),
        Spacer(1, 0.2 * cm),
        pdf_engine.text_flowable("Included equipment / المعدات المشمولة", profile, 12, 17, primary, True),
    ]
    for idx, model in enumerate(models, start=1):
        story.append(pdf_engine.text_flowable(
            f"{idx}. {model['code']} — {model.get('product_name') or ''}", profile, 9.5, 13))
    story.extend([
        Spacer(1, 0.35 * cm),
        pdf_engine.text_flowable("Attachments / المرفقات: approved technical data sheets for each model, followed by the PDFs supplied with this request.", profile, 9, 13),
        Spacer(1, 0.5 * cm),
        pdf_engine.text_flowable(f"Regards,\n{company}", profile, 10, 15),
    ])
    transmittal = pdf_engine.build_custom_document(
        story, profile, doc_type="Submittal Transmittal", title=f"Submittal - {project_name}")

    writer = PdfWriter()
    writer.append(cover)
    writer.append(transmittal)
    for mid in ids:
        items = export_center.collect_items("tds", model_id=mid)
        if not items:
            raise ValueError(f"لا تتوفر بيانات ورقة البيانات الفنية للطراز رقم {mid}.")
        tds = pdf_engine.build_document(
            items, profile, doc_type="tds", template_name="Corporate",
            options={"title": items[0]["m"].get("code") or "Technical Data Sheet"})
        writer.append(tds)
    for name, data in attachments or []:
        writer.append(io.BytesIO(data))
    writer.add_metadata({"/Title": f"Technical Submittal - {project_name}", "/Author": company})
    out = io.BytesIO()
    writer.write(out)
    out.seek(0)
    return out


def _public_model_by_code(code):
    c = db.conn()
    try:
        rows = c.execute("""SELECT m.*, p.name product_name, p.ar_name product_ar_name,
                                  p.description product_description, p.manufacturer, p.active product_active
                           FROM models m LEFT JOIN products p ON p.id=m.product_id
                           WHERE m.code=? AND m.active=1""", (code,)).fetchall()
        if len(rows) != 1:
            return None
        model = rows[0]
        if (not model["public_enabled"] or model["status"] != "approved" or not model["visible"]
                or (model["product_id"] and not model["product_active"])):
            return None
        if model["product_id"]:
            prod = c.execute("SELECT active FROM products WHERE id=?", (model["product_id"],)).fetchone()
            if not prod or not prod["active"]:
                return None

        spec_map = {}
        product_specs = c.execute("""SELECT * FROM specs WHERE product_id=? AND model_id IS NULL
                                     AND hidden=0 AND deleted=0 AND verified=1 ORDER BY sort, id""",
                                  (model["product_id"],)).fetchall() if model["product_id"] else []
        model_specs = c.execute("""SELECT * FROM specs WHERE model_id=? AND hidden=0 AND deleted=0 AND verified=1
                                   ORDER BY sort, id""", (model["id"],)).fetchall()
        for spec in list(product_specs) + list(model_specs):
            key = (spec["attr"] or "").strip().casefold()
            if not key:
                continue
            value = db.compute_final_spec(spec)[2]
            if value:
                spec_map[key] = {"attr": spec["attr"], "value": value}

        images = [dict(r) for r in c.execute("""SELECT i.file, l.image_role, l.is_primary
                    FROM image_links l JOIN images i ON i.id=l.image_id
                    WHERE l.model_id=? AND l.ok_catalog=1 AND i.disabled=0
                    ORDER BY l.is_primary DESC, l.sort_order, l.id""", (model["id"],))]
        if not images and model["product_id"]:
            images = [dict(r) for r in c.execute("""SELECT i.file, pl.image_role, pl.is_primary
                        FROM product_image_links pl JOIN images i ON i.id=pl.image_id
                        WHERE pl.product_id=? AND pl.ok_catalog=1 AND i.disabled=0
                        ORDER BY pl.is_primary DESC, pl.sort_order, pl.id""", (model["product_id"],))]
        return {"model": dict(model), "specs": list(spec_map.values()), "images": images[:4]}
    finally:
        c.close()


def _public_url(code):
    base = _configured_public_base()
    if not base:
        abort(503, "PUBLIC_BASE_URL must be configured as a valid HTTPS URL before publishing QR links.")
    path = url_for("enterprise_pro.public_model_portal", code=code)
    return base + path


def _configured_public_base():
    base = os.environ.get("PUBLIC_BASE_URL", "").strip()
    if not base:
        return None if auth.PROD else request.url_root.rstrip("/")
    parsed = urlsplit(base)
    try:
        has_host = bool(parsed.hostname)
        _port = parsed.port
    except ValueError:
        return None
    if (not parsed.scheme or not parsed.netloc or not has_host or parsed.username or parsed.password
            or parsed.query or parsed.fragment or (auth.PROD and parsed.scheme.lower() != "https")):
        return None
    return base.rstrip("/")


def _read_profile_from_form():
    c = db.conn()
    try:
        return profiles.load(c, request.form.get("profile_id") or None)
    finally:
        c.close()


@bp.route("/pro/")
def pro_hub():
    c = db.conn()
    try:
        products = c.execute("SELECT id, name, ar_name FROM products WHERE active=1 ORDER BY name").fetchall()
        models = c.execute("""SELECT m.id, m.code, m.status, m.visible, m.public_enabled,
                                    p.name product_name, p.active product_active
                             FROM models m LEFT JOIN products p ON p.id=m.product_id
                             WHERE m.active=1 ORDER BY m.code, m.id""").fetchall()
        profs = profiles.load_all(c)
    finally:
        c.close()
    can_publish = bool(getattr(g, "user", None) and g.user["is_owner"])
    return render_template_string(PRO_HUB_HTML, products=products, models=models,
                                  profs=profs, can_publish=can_publish)


@bp.route("/pro/convert", methods=["POST"])
def pro_convert():
    value = request.form.get("value", "")[:120]
    unit = request.form.get("unit", "")[:32]
    unit_mode = request.form.get("unit_mode", "dual")
    try:
        converted = format_spec_with_units(value, unit, unit_mode)
    except ValueError as exc:
        flash(str(exc))
        return redirect("/pro/")
    return render_template_string(CONVERT_RESULT_HTML, value=value, unit=unit,
                                  unit_mode=unit_mode, converted=converted)


@bp.route("/pro/compare.pdf", methods=["POST"])
def pro_compare():
    try:
        ids = _safe_model_ids(request.form.getlist("model_ids"), minimum=2, maximum=MAX_COMPARE_MODELS)
        profile = _read_profile_from_form()
        unit_mode = request.form.get("unit_mode", "dual")
        title = (request.form.get("title") or "Technical Comparison Matrix").strip()[:120]
        buf = build_comparison_pdf(ids, profile, unit_mode=unit_mode, title=title)
    except ValueError as exc:
        flash(str(exc))
        return redirect("/pro/")
    return _persist_and_send(buf, "Technical_Comparison_Matrix.pdf", "pro_comparison", profile,
                             title, {"model_ids": ids, "unit_mode": unit_mode})


@bp.route("/pro/family-matrix.pdf", methods=["POST"])
def pro_family_matrix():
    try:
        product_id = int(request.form.get("product_id", ""))
        if product_id <= 0:
            raise ValueError
        unit_mode = request.form.get("unit_mode", "original")
        profile = _read_profile_from_form()
        buf = build_family_matrix_pdf(product_id, profile, unit_mode=unit_mode)
    except ValueError as exc:
        flash(str(exc) or "اختر منتجًا صالحًا.")
        return redirect("/pro/")
    title = f"Family Matrix {product_id}"
    return _persist_and_send(buf, f"Family_Matrix_{product_id}.pdf", "pro_family_matrix", profile,
                             title, {"product_id": product_id, "unit_mode": unit_mode})


@bp.route("/pro/submittal.pdf", methods=["POST"])
def pro_submittal():
    try:
        ids = _safe_model_ids(request.form.getlist("model_ids"), minimum=1, maximum=MAX_SUBMITTAL_MODELS)
        attachments = _read_attachments(request.files.getlist("attachments"))
        profile = _read_profile_from_form()
        project = request.form.get("project_name", "").strip()[:160]
        client = request.form.get("client_name", "").strip()[:160]
        buf = build_submittal_package(project, client, ids, profile, attachments)
    except ValueError as exc:
        flash(str(exc))
        return redirect("/pro/")
    title = f"Technical Submittal - {project}"
    return _persist_and_send(buf, "Submittal_Package.pdf", "pro_submittal", profile,
                             title, {"project_name": project, "client_name": client,
                                     "model_ids": ids, "attachment_names": [n for n, _ in attachments]})


@bp.route("/pro/publication", methods=["POST"])
@auth.owner_required
def toggle_publication():
    try:
        model_id = int(request.form.get("model_id", ""))
        enabled = int(request.form.get("enabled", ""))
        if model_id <= 0 or enabled not in (0, 1):
            raise ValueError
    except (TypeError, ValueError):
        flash("بيانات النشر غير صالحة.")
        return redirect("/pro/")

    c = db.conn()
    try:
        model = c.execute("SELECT id, code, active, visible, status, product_id FROM models WHERE id=?", (model_id,)).fetchone()
        if not model:
            flash("الطراز غير موجود.")
            return redirect("/pro/")
        if enabled:
            if not model["active"] or not model["visible"] or model["status"] != "approved":
                flash("لا يمكن نشر الطراز إلا إذا كان نشطًا وظاهرًا ومعتمدًا.")
                return redirect("/pro/")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", model["code"] or ""):
                flash("يجب أن يبدأ رمز الطراز المنشور بحرف أو رقم، وأن يقتصر على الأحرف والأرقام والشرطة والنقطة.")
                return redirect("/pro/")
            if model["product_id"]:
                p = c.execute("SELECT active FROM products WHERE id=?", (model["product_id"],)).fetchone()
                if not p or not p["active"]:
                    flash("لا يمكن النشر لأن المنتج غير نشط.")
                    return redirect("/pro/")
            duplicates = c.execute("SELECT COUNT(*) n FROM models WHERE code=? AND active=1", (model["code"],)).fetchone()["n"]
            if duplicates != 1:
                flash("لا يمكن النشر لأن رمز الطراز مكرر؛ عالج التكرار أولًا.")
                return redirect("/pro/")
            if not _configured_public_base():
                flash("يرجى ضبط PUBLIC_BASE_URL على عنوان HTTPS صالح قبل تفعيل بوابة العملاء.")
                return redirect("/pro/")
        c.execute("UPDATE models SET public_enabled=? WHERE id=?", (enabled, model_id))
        db.touch(c, "models", [model_id])
        db.log(c, "model", model_id, "public_enable" if enabled else "public_disable", "")
        c.commit()
        flash("نُشر الطراز في البوابة العامة." if enabled else "أُوقف نشر الطراز.")
    finally:
        c.close()
    return redirect("/pro/")


@bp.route("/public/model/<string:code>")
def public_model_portal(code):
    public = _public_model_by_code(code)
    if not public:
        abort(404)
    model = public["model"]
    image_urls = []
    for image in public["images"]:
        try:
            storage.image_path(image["file"])
        except (FileNotFoundError, OSError):
            continue
        image_urls.append(url_for("enterprise_pro.public_image", code=code, filename=image["file"]))
    response = render_template_string(
        PUBLIC_PORTAL_HTML, model=model, specs=public["specs"], image_urls=image_urls)
    from flask import make_response
    resp = make_response(response)
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@bp.route("/public/image/<string:code>/<path:filename>")
def public_image(code, filename):
    public = _public_model_by_code(code)
    if not public:
        abort(404)
    file_key = os.path.basename(filename)
    if file_key != filename:
        abort(404)
    model = public["model"]
    if not any(im["file"] == file_key for im in public["images"]):
        abort(404)
    try:
        path = storage.image_path(file_key)
    except (FileNotFoundError, OSError):
        abort(404)
    response = send_file(path, conditional=True)
    response.headers["Cache-Control"] = "private, max-age=300"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@bp.route("/qr/<string:code>.png")
def qr_badge(code):
    if not _public_model_by_code(code):
        abort(404)
    import qrcode
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=6, border=4)
    qr.add_data(_public_url(code))
    qr.make(fit=True)
    image = qr.make_image(fill_color="#1F3864", back_color="white").convert("RGB")
    buf = io.BytesIO()
    image.save(buf, "PNG")
    buf.seek(0)
    response = send_file(buf, mimetype="image/png", download_name=f"{secure_filename(code)}.png")
    response.headers["Cache-Control"] = "private, max-age=300"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


PRO_HUB_HTML = """{% extends "base" %}{% block body %}
<div class="card"><h2>🚀 الأدوات الهندسية المتقدمة</h2>
<p class="muted">تحويل الوحدات، ومقارنة المواصفات، ومصفوفة الطرازات، وإعداد حزم التقديم الفنية. تُسجَّل ملفات PDF في مركز التصدير وتُحفظ في Google Drive عند تفعيل التخزين السحابي.</p></div>

<div class="card"><h3>1. تحويل الوحدات الهندسية</h3>
<form method="post" action="/pro/convert" style="display:flex;gap:8px;flex-wrap:wrap;align-items:end">
  <label>القيمة <input name="value" maxlength="120" placeholder="مثال: 3.5" required></label>
  <label>الوحدة <input name="unit" maxlength="32" placeholder="kW / bar / mm" required></label>
  <label>النظام <select name="unit_mode"><option value="dual">متري + إمبراطوري</option><option value="metric">متري</option><option value="imperial">إمبراطوري</option><option value="original">الأصلي</option></select></label>
  <button>تحويل</button>
</form><p class="muted">القيم المركبة والنطاقات تبقى بصيغتها الأصلية لتجنب التحويل التخمينـي.</p></div>

<div class="card"><h3>2. مقارنة المواصفات الفنية</h3>
<form method="post" action="/pro/compare.pdf">
  <label>الطرازات المعتمدة والظاهرة (حتى 6)<br><select name="model_ids" multiple size="7" required style="min-width:330px">
    {% for m in models if m.status=='approved' and m.visible and (m.product_active is none or m.product_active) %}<option value="{{ m.id }}">{{ m.code }} — {{ m.product_name or 'دون منتج' }}</option>{% endfor %}</select></label>
  <label>نظام الوحدات <select name="unit_mode"><option value="dual">متري + إمبراطوري</option><option value="metric">متري</option><option value="imperial">إمبراطوري</option><option value="original">الأصلي</option></select></label>
  <label>الهوية <select name="profile_id">{% for p in profs %}<option value="{{ p.id }}">{{ p.name }}</option>{% endfor %}</select></label>
  <button>تنزيل مقارنة PDF</button>
</form></div>

<div class="card"><h3>3. مصفوفة مواصفات عائلة المنتج</h3>
<form method="post" action="/pro/family-matrix.pdf">
  <label>المنتج <select name="product_id" required>{% for p in products %}<option value="{{ p.id }}">{{ p.name }} {{ '('+p.ar_name+')' if p.ar_name }}</option>{% endfor %}</select></label>
  <label>الوحدات <select name="unit_mode"><option value="original">الأصلي</option><option value="dual">متري + إمبراطوري</option><option value="metric">متري</option><option value="imperial">إمبراطوري</option></select></label>
  <label>الهوية <select name="profile_id">{% for p in profs %}<option value="{{ p.id }}">{{ p.name }}</option>{% endfor %}</select></label>
  <button>تنزيل المصفوفة PDF</button>
</form><p class="muted">تضم المصفوفة جميع الطرازات النشطة والظاهرة، وتوضح حالة اعتماد كل طراز. عند كثرة المواصفات، تُقسّم إلى صفحات مع تكرار عناوين الأعمدة.</p></div>

<div class="card"><h3>4. حزمة التقديم الفني</h3>
<form method="post" action="/pro/submittal.pdf" enctype="multipart/form-data">
  <p><label>اسم المشروع <input name="project_name" maxlength="160" required></label>
     <label>العميل أو الاستشاري <input name="client_name" maxlength="160" required></label></p>
  <p><label>الطرازات المعتمدة<br><select name="model_ids" multiple size="6" required style="min-width:330px">
    {% for m in models if m.status=='approved' and m.visible and (m.product_active is none or m.product_active) %}<option value="{{ m.id }}">{{ m.code }} — {{ m.product_name or '' }}</option>{% endfor %}</select></label></p>
  <label>مرفقات PDF اختيارية (حتى 10 ملفات، 8 ميجابايت للملف) <input type="file" name="attachments" accept="application/pdf,.pdf" multiple></label>
  <label>الهوية <select name="profile_id">{% for p in profs %}<option value="{{ p.id }}">{{ p.name }}</option>{% endfor %}</select></label>
  <button>إنشاء حزمة PDF</button>
</form></div>

<div class="card"><h3>5. بوابة العملاء العامة ورموز QR</h3>
{% if can_publish %}<p>يقتصر النشر على مالك النظام، ولا يُتاح إلا للطرازات المعتمدة والنشطة والظاهرة. تكون الطرازات خاصة افتراضيًا.</p>
<table><tr><th>الطراز</th><th>المنتج</th><th>الحالة</th><th>النشر</th><th>الرابط</th></tr>
{% for m in models %}<tr><td>{{ m.code }}</td><td>{{ m.product_name or '—' }}</td><td>{{ m.status }}{% if not m.visible %} / مخفي{% endif %}</td><td>
  {% set eligible = m.status=='approved' and m.visible and (m.product_active is none or m.product_active) %}
  <form method="post" action="/pro/publication"><input type="hidden" name="model_id" value="{{ m.id }}">
  {% if m.public_enabled %}<button class="red" name="enabled" value="0">إيقاف النشر</button>
  {% elif eligible %}<button name="enabled" value="1">نشر</button>
  {% else %}<button disabled>يتطلب الاعتماد والإظهار</button>{% endif %}</form>
</td><td>{% if m.public_enabled %}<a href="{{ url_for('enterprise_pro.public_model_portal', code=m.code) }}" target="_blank">فتح</a>
  <a href="{{ url_for('enterprise_pro.qr_badge', code=m.code) }}"><img alt="QR" src="{{ url_for('enterprise_pro.qr_badge', code=m.code) }}" style="width:54px;vertical-align:middle"></a>{% else %}خاص{% endif %}</td></tr>{% endfor %}</table>
{% else %}<p>تقتصر إدارة النشر العام على مالك النظام. ولا تعرض الروابط العامة إلا الطرازات المنشورة صراحةً ومواصفاتها وصورها المعتمدة.</p>{% endif %}
</div>
{% endblock %}"""

CONVERT_RESULT_HTML = """{% extends "base" %}{% block body %}
<div class="card"><h2>نتيجة تحويل الوحدة</h2>
<p>القيمة المدخلة: <b dir="ltr">{{ value }} {{ unit }}</b></p>
<p>النظام: {{ unit_mode }}</p><p>النتيجة: <b dir="ltr">{{ converted }}</b></p>
<a class="btn sec" href="/pro/">عودة للأدوات</a></div>{% endblock %}"""

PUBLIC_PORTAL_HTML = """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700&display=swap"><meta name="robots" content="noindex,nofollow">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{{ model.code }} — التحقق الفني</title>
<style>body{font-family:"Cairo",Tahoma,Arial,sans-serif;background:#f4f6fa;margin:0;padding:20px;color:#1d2433}.box{max-width:900px;margin:auto;background:#fff;padding:22px;border-radius:12px;box-shadow:0 2px 8px #0001}
.badge{background:#dcfce7;color:#166534;padding:5px 12px;border-radius:20px;font-size:13px;font-weight:bold}h1{color:#1F3864}table{width:100%;border-collapse:collapse;margin-top:16px}th,td{padding:9px;border-bottom:1px solid #e2e8f0;text-align:right}th{background:#1F3864;color:#fff}.images{display:flex;gap:10px;flex-wrap:wrap}.images img{max-height:220px;max-width:100%;object-fit:contain;border:1px solid #ddd;border-radius:8px}</style></head>
<body><main class="box"><span class="badge">✓ طراز منشور — مواصفات معتمدة</span><h1>{{ model.code }} — {{ model.product_name or model.name or '' }}</h1>
{% if model.product_description %}<p>{{ model.product_description }}</p>{% endif %}
{% if image_urls %}<div class="images">{% for src in image_urls %}<img src="{{ src }}" alt="{{ model.code }}">{% endfor %}</div>{% endif %}
<table><tr><th>المواصفة الفنية</th><th>القيمة المعتمدة</th></tr>{% for spec in specs %}<tr><td><b>{{ spec.attr }}</b></td><td dir="auto">{{ spec.value }}</td></tr>{% endfor %}</table>
<small>المعلومات المعروضة خاصة بالطراز المعتمد والمنشور.</small></main></body></html>"""
