"""Compatibility facade for the unified PDF engine.

New application code should use :mod:`pdf_engine` directly. This module keeps
the small legacy API still imported by older routes and extensions without
maintaining a second PDF implementation.
"""
import io

import pdf_engine

SHEET_IMG_W_CM = 10.2  # used by the legacy low-resolution image check
ARABIC_OK = pdf_engine.ARABIC_OK


def build(items, profile, kind="datasheet", title="", force_final=False):
    """Adapt the old input shape to the central document builder."""
    doc_types = {
        "datasheet": "tds",
        "tds": "tds",
        "catalog": "product_catalog",
        "product_catalog": "product_catalog",
        "master": "master_catalog",
        "master_catalog": "master_catalog",
    }
    doc_type = doc_types.get(kind, "tds")
    adapted = []
    approval_field = "ok_sheet" if doc_type == "tds" else "ok_catalog"
    for item in items:
        images = []
        for image in item.get("links") or item.get("images") or []:
            image = dict(image)
            if image.get("file") and int(image.get(approval_field) or 0) == 1:
                images.append({
                    "file": image["file"],
                    "image_role": image.get("image_role") or "model_image",
                    "is_primary": image.get("is_primary", 0),
                    "ok_sheet": image.get("ok_sheet", 0),
                    "ok_catalog": image.get("ok_catalog", 0),
                })
        adapted.append({
            "m": dict(item["m"]),
            "product": dict(item.get("product") or {}),
            "specs": [dict(spec) for spec in item.get("specs", [])],
            "images": images,
            "family_name": item.get("family_name") or item.get("family") or "General",
        })
    return pdf_engine.build_document(
        adapted,
        profile,
        doc_type=doc_type,
        options={"title": title, "force_final": "1" if force_final else "0"},
    )


def render_png(buf, page=0, scale=1.25):
    """Render one PDF page as PNG for the legacy preview endpoint."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(buf.getvalue())
    out = io.BytesIO()
    pdf[page].render(scale=scale).to_pil().convert("RGB").save(out, "PNG")
    return out.getvalue()
