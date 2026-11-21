"""Build representative PDFs with isolated SQLite test data and render PNG previews.

Run from the package root with: python tests/run_acceptance.py [--output-dir PATH]
All synthetic database rows and source images use a temporary data directory.
"""
import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output-dir", default=str(ROOT / "tests" / "acceptance_output"),
                    help="directory for generated PDFs and PNG previews")
args = parser.parse_args()

# Keep the acceptance run away from a developer's database and Google Drive.
_temp_data = tempfile.TemporaryDirectory(prefix="catalog-acceptance-")
os.environ["DATA_DIR"] = os.path.join(_temp_data.name, "data")
os.environ["CACHE_DIR"] = os.path.join(_temp_data.name, "cache")
os.environ["DATABASE_URL"] = ""
os.environ["STORAGE_BACKEND"] = "local"
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw
import pypdfium2 as pdfium
import db
import export_center
import pdf_engine
import profiles
import products


def make_image(filename, color, label):
    image = Image.new("RGB", (600, 400), color)
    draw = ImageDraw.Draw(image)
    draw.rectangle([20, 20, 580, 380], outline="white", width=6)
    draw.text((60, 180), label, fill="white")
    image.save(os.path.join(db.IMG_DIR, filename), "PNG")
    return filename


def add_image(c, catalog_id, product_id, filename, sha, role, primary):
    image_id = c.execute("""INSERT INTO images(catalog_id,page,file,sha,w,h,dpi,source,image_type,link_status)
                            VALUES(?,0,?,?,600,400,300,'manual',?,'confirmed')""",
                         (catalog_id, filename, sha, role)).lastrowid
    c.execute("""INSERT INTO product_image_links(image_id,product_id,image_role,is_primary)
                 VALUES(?,?,?,?)""", (image_id, product_id, role, primary))
    return image_id


def main():
    db.init()
    c = db.conn()
    profiles.ensure_default(c)
    catalog_id = products.manual_catalog(c)
    family_pump = c.execute("INSERT INTO families(name) VALUES(?)", ("Hydraulic Pumps",)).lastrowid
    family_cutter = c.execute("INSERT INTO families(name) VALUES(?)", ("Rebar Cutters",)).lastrowid
    pump_id = c.execute("""INSERT INTO products(name,ar_name,description,supplier,manufacturer,family_id,status)
                            VALUES(?,?,?,?,?,?,?)""",
                         ("Industrial Pump", "مضخة صناعية", "Heavy-duty centrifugal pump.",
                          "FlowTech", "FlowTech GmbH", family_pump, "approved")).lastrowid
    cutter_id = c.execute("""INSERT INTO products(name,ar_name,description,supplier,manufacturer,family_id,status)
                              VALUES(?,?,?,?,?,?,?)""",
                           ("CNC Rebar Cutter", "ماكينة قص حديد التسليح", "High-speed rebar cutter.",
                            "SteelPro", "SteelPro Corp", family_cutter, "approved")).lastrowid

    pump_img = make_image("acceptance_pump.png", "#1F3864", "INDUSTRIAL PUMP A100")
    drawing_img = make_image("acceptance_drawing.png", "#334155", "TECHNICAL SCHEMATIC")
    cutter_img = make_image("acceptance_cutter.png", "#1B5E3B", "CNC CUTTER GQ50")
    add_image(c, catalog_id, pump_id, pump_img, "acceptance-sha-pump", "main_product", 1)
    add_image(c, catalog_id, pump_id, drawing_img, "acceptance-sha-drawing", "technical_drawing", 0)
    add_image(c, catalog_id, cutter_id, cutter_img, "acceptance-sha-cutter", "main_product", 1)

    pump_model = c.execute("""INSERT INTO models(catalog_id,code,name,series,family_id,product_id,status)
                              VALUES(?,?,?,?,?,?,?)""",
                           (catalog_id, "A100", "Standard 50Hz", "Series A", family_pump, pump_id, "approved")).lastrowid
    c.execute("""INSERT INTO models(catalog_id,code,name,series,family_id,product_id,status)
                 VALUES(?,?,?,?,?,?,?)""",
              (catalog_id, "A200", "Heavy 60Hz", "Series A", family_pump, pump_id, "approved"))
    c.execute("""INSERT INTO models(catalog_id,code,name,series,family_id,product_id,status)
                 VALUES(?,?,?,?,?,?,?)""",
              (catalog_id, "GQ50", "Pro Cutter", "Series G", family_cutter, cutter_id, "approved"))
    spec_id = c.execute("""INSERT INTO specs(model_id,product_id,attr,supplier_value,supplier_unit,sort)
                           VALUES(?,?,?,?,?,?)""",
                        (pump_model, pump_id, "Frequency", "50", "Hz", 1)).lastrowid
    c.execute("UPDATE specs SET company_value='60',company_unit='Hz',rule='MANUAL' WHERE id=?", (spec_id,))
    db.log_spec_rev(c, pump_id, pump_model, spec_id, "UPDATE", "50 Hz", "60 Hz")
    c.execute("""INSERT INTO specs(product_id,attr,supplier_value,supplier_unit,sort,source_tag)
                 VALUES(?,?,?,?,?,?)""", (pump_id, "Motor Power", "7.5", "kW", 2, "manual"))

    identity_a = profiles.create(c, "Corporate Blue", dict(profiles.DEFAULTS,
                                company="FlowTech Industrial", c_primary="#1F3864",
                                c_accent="#E8590C", texture="grid_tech", border_style="double"))
    identity_b = profiles.create(c, "Premium Green", dict(profiles.DEFAULTS,
                                company="Apex Heavy Machinery", c_primary="#1B5E3B",
                                c_accent="#D97706", texture="linen", decor_style="side_stripe"))
    c.commit()
    prof_a = profiles.load(c, identity_a)
    prof_b = profiles.load(c, identity_b)
    c.close()

    pdf_outputs = [
        ("acceptance_tds_identity_a.pdf", export_center.collect_items("tds", model_id=pump_model),
         prof_a, "tds", "Corporate", {"title": "A100 Technical Data Sheet", "cover": "0", "toc": "0"}),
        ("acceptance_tds_identity_b.pdf", export_center.collect_items("tds", model_id=pump_model),
         prof_b, "tds", "Premium", {"title": "A100 Technical Data Sheet", "cover": "0", "toc": "0"}),
        ("acceptance_product_catalog.pdf", export_center.collect_items("product_catalog", product_id=pump_id),
         prof_a, "product_catalog", "Corporate", {"title": "Industrial Pumps Catalog"}),
        ("acceptance_master_catalog.pdf", export_center.collect_items("master_catalog"),
         prof_b, "master_catalog", "Editorial", {"title": "2026 Master Catalog"}),
    ]
    if pdf_engine.ARABIC_OK:
        arabic_profile = dict(prof_a, lang="both")
        pdf_outputs.append(("acceptance_tds_arabic.pdf",
                            export_center.collect_items("tds", model_id=pump_model),
                            arabic_profile, "tds", "Corporate",
                            {"title": "A100 — مضخة صناعية 50 Hz", "lang": "both", "cover": "0", "toc": "0"}))
    else:
        print("SKIP Arabic PDF case: install arabic-reshaper and python-bidi to verify shaping and direction.")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir).expanduser().resolve() / f"run_{stamp}_{os.getpid()}"
    pdf_dir, preview_dir = output_dir / "pdf", output_dir / "png"
    pdf_dir.mkdir(parents=True, exist_ok=False)
    preview_dir.mkdir(parents=True, exist_ok=False)
    for filename, items, profile, kind, template, options in pdf_outputs:
        if not items:
            raise AssertionError(f"No items returned for {filename}")
        buffer = pdf_engine.build_document(items, profile, doc_type=kind,
                                           template_name=template, options=options)
        pdf_path = pdf_dir / filename
        pdf_path.write_bytes(buffer.getvalue())
        document = pdfium.PdfDocument(str(pdf_path))
        if len(document) < 1:
            raise AssertionError(f"No pages rendered for {filename}")
        for page_num in range(len(document)):
            png_path = preview_dir / f"{pdf_path.stem}_p{page_num + 1}.png"
            document[page_num].render(scale=1.5).to_pil().save(png_path)
        print(f"PASS {filename}: {len(document)} page(s)")
    print(f"Acceptance PDFs and PNG previews saved under: {output_dir}")


if __name__ == "__main__":
    main()
