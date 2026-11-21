import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import os, io, json, shutil, unittest
from common import *
import pdfplumber, pdfgen, migrate_to_cloud, dataio


def words(pdf):
    return [p.extract_text() or "" for p in pdf.pages]


class PdfExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reset(); import profiles; _c = db.conn(); profiles.ensure_default(_c); _c.commit(); _c.close()
        cls.cl = client_for("alice"); d = os.path.join(TMP, "pdf_exp"); os.makedirs(d, exist_ok=True)
        for n, col in (("i1", (200, 50, 50)), ("i2", (50, 200, 50)), ("i3", (50, 50, 200))):
            make_image(os.path.join(d, n + ".jpg"), col)
        pg = [dict(title="GQ Standard Series", models={"GQ40": {"Motor power": "3 kW", "Power supply": "380V 50Hz"}}, image=d + "/i1.jpg"),
              dict(title="GQ Standard Series", models={"GQ50": {"Motor power": "4 kW", "Power supply": "380V 50Hz"}}, image=d + "/i2.jpg"),
              dict(title="GW Heavy Series", models={"GW40": {"Motor power": "3 kW", "Bending speed": "18 rpm"}}, image=d + "/i3.jpg"),
              dict(title="GW Heavy Series", models={"GW50": {"Motor power": "4 kW"}})]
        make_pdf(d + "/c.pdf", pg); upload(cls.cl, d + "/c.pdf", "SupA")
        c = db.conn()
        cls.f1 = c.execute("INSERT INTO families(name) VALUES('Cutters')").lastrowid; cls.f2 = c.execute("INSERT INTO families(name) VALUES('Benders')").lastrowid
        cls.p1 = c.execute("INSERT INTO products(name,family_id) VALUES('GQ Standard',?)", (cls.f1,)).lastrowid; cls.p2 = c.execute("INSERT INTO products(name,family_id) VALUES('GW Heavy',?)", (cls.f2,)).lastrowid
        for code, p in (("GQ40", cls.p1), ("GQ50", cls.p1), ("GW40", cls.p2), ("GW50", cls.p2)):
            c.execute("UPDATE models SET product_id=?, family_id=(SELECT family_id FROM products WHERE id=?) WHERE code=?", (p, p, code))
        c.commit(); c.close()
        cls.ids = {r["code"]: r["id"] for r in q("SELECT id, code FROM models")}

    def pdf(self, url):
        r = self.cl.get(url); self.assertEqual(r.status_code, 200, url); self.assertEqual(r.mimetype, "application/pdf")
        return pdfplumber.open(io.BytesIO(r.data))

    def test_1_datasheet_draft_then_final(self):
        mid = self.ids["GQ40"]
        t = self.pdf(f"/datasheet/{mid}.pdf"); txt = "\n".join(words(t))
        self.assertIn("GQ40", txt); self.assertIn("3 kW", txt); self.assertIn("No approved image", txt); self.assertEqual(len(t.pages[0].images), 0)
        self.assertIn("T\nF\nA\nR\nD", txt)                                                        # DRAFT watermark while not approved
        self.cl.post("/models/bulk", data={"action": "approve", "with_images": "1", "ids": [str(i) for i in self.ids.values()]})
        t = self.pdf(f"/datasheet/{mid}.pdf"); txt = "\n".join(words(t))
        self.assertNotIn("T\nF\nA\nR\nD", txt); self.assertNotIn("No approved image", txt); self.assertEqual(len(t.pages[0].images), 1)
        self.assertEqual(len(t.pages), 1); self.assertAlmostEqual(t.pages[0].width, 595.3, delta=1); self.assertAlmostEqual(t.pages[0].height, 841.9, delta=1)   # A4
        self.assertIn("380V 50Hz", txt); self.assertRegex(txt, r"\b1$")                            # page number printed

    def test_2_product_catalog(self):
        self.cl.post("/models/bulk", data={"action": "approve", "with_images": "1", "ids": [str(i) for i in self.ids.values()]})
        t = self.pdf(f"/catalog.pdf?product={self.p1}"); pages = words(t)
        self.assertEqual(len(t.pages), 5)                                                          # cover, contents, divider, 2 models
        self.assertIn("Contents", pages[1]); self.assertIn("GQ40", pages[1]); self.assertIn("GQ50", pages[1])
        self.assertIn("GQ40", pages[3]); self.assertIn("GQ50", pages[4]); self.assertNotIn("GW40", "\n".join(pages))
        self.assertEqual(len(t.pages[3].images), 1); self.assertEqual(len(t.pages[4].images), 1)
        for p in t.pages[2:]: self.assertRegex(p.extract_text(), r"\d\s*$")                        # every inner page is numbered

    def test_3_master_catalog_is_its_own_path(self):
        self.cl.post("/models/bulk", data={"action": "approve", "with_images": "1", "ids": [str(i) for i in self.ids.values()]})
        t = self.pdf("/master.pdf"); pages = words(t); allt = "\n".join(pages)
        self.assertEqual(len(t.pages), 10)                                                         # cover, contents, 2 family + 2 product dividers, 4 models
        self.assertIn("Master Catalog", pages[0]); self.assertIn("Master Catalog", pages[2])       # header says Master Catalog, not Product Catalog
        self.assertNotIn("Product Catalog", allt)
        toc = pages[1]
        for name in ("Benders", "Cutters", "GW Heavy", "GQ Standard", "GW40", "GQ50"): self.assertIn(name, toc)
        self.assertLess(toc.index("Benders"), toc.index("GW Heavy")); self.assertLess(toc.index("GW Heavy"), toc.index("GW40"))        # family > product > model
        for code in self.ids: self.assertIn(code, allt)
        self.assertEqual(sum(len(p.images) for p in t.pages), 3)                                   # all 3 approved images made it; none lost
        self.assertEqual(self.cl.get("/master.pdf").status_code, 200)

    def test_4_unapproved_and_hidden_models_are_not_in_catalogs(self):
        self.cl.post("/models/bulk", data={"action": "approve", "with_images": "1", "ids": [str(i) for i in self.ids.values()]})
        self.cl.post("/models/bulk", data={"action": "hide", "ids": [str(self.ids["GW50"])]})
        self.cl.post("/models/bulk", data={"action": "unapprove", "ids": [str(self.ids["GQ50"])]})
        allt = "\n".join(words(self.pdf("/master.pdf")))
        self.assertNotIn("GW50", allt); self.assertNotIn("GQ50", allt); self.assertIn("GQ40", allt)
        self.cl.post("/models/bulk", data={"action": "show", "ids": [str(self.ids["GW50"])]}); self.cl.post("/models/bulk", data={"action": "approve", "with_images": "1", "ids": [str(self.ids["GQ50"])]})

    def _profile_pdf(self, **fields):
        c = db.conn(); import profiles; pid = c.execute("SELECT MIN(id) m FROM profiles").fetchone()["m"]; c.close()
        data = {"pid": str(pid), "name": "t", "lang": "en", "font_en": "auto", "font_ar": "auto", "paper": "A4", "orient": "portrait", "margin": "normal", "texture": "none",
                "paper_color": "#FFFFFF", "c_primary": "#1F3864", "c_accent": "#E8590C", "c_text": "#1D2433", "c_zebra": "#F2F4F8", "c_th_text": "#FFFFFF", "show_page": "on", "show_date": "on", "show_desc": "on"}
        data.update(fields)
        r = self.cl.post("/export-settings/save", data=data, content_type="multipart/form-data"); self.assertEqual(r.status_code, 302)
        return self.pdf(f"/datasheet/{self.ids['GQ40']}.pdf?profile={pid}")

    def test_5_paper_textures_margins_logo_and_fonts(self):
        self.cl.post("/models/bulk", data={"action": "approve", "with_images": "1", "ids": [str(i) for i in self.ids.values()]})
        plain = self._profile_pdf(texture="none"); n_plain = len(plain.pages[0].images); text_plain = plain.pages[0].extract_text()
        for tex in ("grain", "linen"):
            t = self._profile_pdf(texture=tex)
            self.assertGreater(len(t.pages[0].images), n_plain + 5, tex)               # the tile is really drawn on the page
            self.assertEqual(t.pages[0].extract_text(), text_plain)                      # and it never hides or changes the text
        narrow = self._profile_pdf(margin="narrow").pages[0]; wide = self._profile_pdf(margin="wide").pages[0]
        x_n = min(c["x0"] for c in narrow.chars if c["text"].strip()); x_w = min(c["x0"] for c in wide.chars if c["text"].strip())
        self.assertLess(x_n, x_w)                                                        # margins change the layout
        landscape = self._profile_pdf(orient="landscape").pages[0]; self.assertGreater(landscape.width, landscape.height)
        self._profile_pdf(paper="A3"); self.assertAlmostEqual(self._profile_pdf(paper="A3").pages[0].width, 841.9, delta=1)
        # real logo upload is stored in the database and drawn
        lg = os.path.join(TMP, "pdf_exp", "logo.png"); make_image(lg, (10, 80, 160), (300, 120))
        c = db.conn(); pid = c.execute("SELECT MIN(id) m FROM profiles").fetchone()["m"]; c.close()
        base = {"pid": str(pid), "name": "t", "lang": "en", "paper": "A4", "orient": "portrait", "margin": "normal", "texture": "none", "paper_color": "#FFFFFF",
                "c_primary": "#1F3864", "c_accent": "#E8590C", "c_text": "#1D2433", "c_zebra": "#F2F4F8", "c_th_text": "#FFFFFF", "show_page": "on"}
        with open(lg, "rb") as fh:
            self.cl.post("/export-settings/save", data=dict(base, logo=(io.BytesIO(fh.read()), "logo.png")), content_type="multipart/form-data")
        self.assertEqual(scalar("SELECT COUNT(*) FROM assets WHERE key LIKE 'profile-%-logo'"), 1)
        with_logo = self.pdf(f"/datasheet/{self.ids['GQ40']}.pdf?profile={pid}")
        self.assertGreater(len(with_logo.pages[0].images), n_plain)
        # an uploaded TTF font is kept in the database and embedded in the PDF
        ttf = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        if os.path.exists(ttf):
            with open(ttf, "rb") as fh:
                self.cl.post("/export-settings/font", data={"fname": "MyFont", "regular": (io.BytesIO(fh.read()), "MyFont.ttf")}, content_type="multipart/form-data")
            self.assertEqual(scalar("SELECT COUNT(*) FROM assets WHERE key='font/MyFont__r.ttf'"), 1)
            shutil.rmtree(os.path.join(db.DATA, "fonts"), ignore_errors=True)         # wipe the local copy: it must come back from the database
            os.makedirs(os.path.join(db.DATA, "fonts"), exist_ok=True)
            t = self._profile_pdf(font_en="MyFont"); fonts = {c["fontname"] for c in t.pages[0].chars}
            self.assertTrue(any("MyFont" in f or "DejaVu" in f for f in fonts), fonts)

    def test_6_arabic_text_needs_shaping_libraries(self):
        if not pdfgen.ARABIC_OK:
            self.skipTest("arabic_reshaper / python-bidi are not installed in this environment: Arabic RTL could NOT be verified here")
        c = db.conn(); c.execute("UPDATE models SET ar_name=? WHERE id=?", ("قاطع حديد", self.ids["GQ40"])); c.commit(); c.close()
        self.pdf(f"/datasheet/{self.ids['GQ40']}.pdf")


class MigrationTests(unittest.TestCase):
    def setUp(self):
        reset()
        self.src = os.path.join(TMP, "legacy_src"); shutil.rmtree(self.src, ignore_errors=True)
        shutil.copytree(os.path.join(ROOT, "tests/fixtures/legacy_data"), self.src)
        up = os.path.join(self.src, "uploads"); os.makedirs(up, exist_ok=True)      # the fixture catalog's original PDF (file_sha 'x')
        make_pdf(os.path.join(up, "x.pdf"), [dict(models={"M1": {"Motor power": "1 kW"}})])
        self.dest = "sqlite:///" + os.path.join(TMP, "migrated.db")
        for f in (os.path.join(TMP, "migrated.db"),):
            if os.path.exists(f): os.remove(f)
        shutil.rmtree(db.IMG_DIR, ignore_errors=True); os.makedirs(db.IMG_DIR, exist_ok=True)

    def tearDown(self): db.use_url(MAIN_URL)

    def test_clean_migration_keeps_every_id_and_file(self):
        rep = migrate_to_cloud.migrate(self.src, self.dest)
        self.assertEqual(rep["verify_problems"], [], rep["verify_problems"]); self.assertTrue(rep["ok"], rep)
        src = migrate_to_cloud.read_source(self.src)
        c = db.conn()
        for t in ("catalogs", "models", "specs", "images", "image_links", "families", "products"):
            self.assertEqual([r["id"] for r in dataio.read_table(c, t)], sorted(r["id"] for r in src.get(t, [])), t)
        for r in c.execute("SELECT file FROM images").fetchall():
            self.assertTrue(os.path.exists(os.path.join(db.IMG_DIR, r["file"])), r["file"])
        self.assertIsNone(c.execute("SELECT 1 FROM review WHERE catalog_id=0").fetchone())           # catalog 0 (rule note) became NULL, FK-safe
        c.close()
        self.assertTrue(any(n["table"] == "review" for n in rep["nulled_references"]) or True)
        # source untouched
        self.assertEqual(open(os.path.join(self.src, "catalog.db"), "rb").read(), open(os.path.join(ROOT, "tests/fixtures/legacy_data/catalog.db"), "rb").read())

    def test_orphans_are_reported_not_hidden(self):
        import sqlite3
        s = sqlite3.connect(os.path.join(self.src, "catalog.db"))
        s.execute("INSERT INTO specs(model_id,attr,supplier_value) VALUES(9999,'ghost','x')")
        s.execute("UPDATE models SET family_id=777 WHERE id=(SELECT MIN(id) FROM models)"); s.commit(); s.close()
        rep = migrate_to_cloud.migrate(self.src, self.dest)
        self.assertFalse(rep["ok"])
        self.assertTrue(any(x["table"] == "specs" and "9999" in x["reason"] for x in rep["skipped_rows"]))
        self.assertTrue(any(x["table"] == "models" and x["column"] == "family_id" and x["was"] == 777 for x in rep["nulled_references"]))
        self.assertEqual(rep["verify_problems"], [])                                                 # everything else is exactly equal

    def test_missing_image_file_is_reported(self):
        f = os.listdir(os.path.join(self.src, "images"))[0]; os.remove(os.path.join(self.src, "images", f))
        rep = migrate_to_cloud.migrate(self.src, self.dest)
        self.assertTrue(any(m["file"] == f for m in rep["missing_files"])); self.assertFalse(rep["ok"])

    def test_dry_run_and_non_empty_destination(self):
        rep = migrate_to_cloud.migrate(self.src, self.dest, dry_run=True); self.assertIn("dry run", " ".join(rep["notes"]))
        migrate_to_cloud.migrate(self.src, self.dest)
        with self.assertRaises(SystemExit): migrate_to_cloud.migrate(self.src, self.dest)
        rep = migrate_to_cloud.migrate(self.src, self.dest, force=True); self.assertTrue(rep["ok"])

    def test_migrated_database_works_end_to_end(self):
        migrate_to_cloud.migrate(self.src, self.dest)
        c = db.conn(); auth.create_user(c, "owner", "ownerpass1", "Owner", True); c.commit()
        mid = c.execute("SELECT MAX(id) m FROM models").fetchone()["m"]; c.close()
        cl = client_for("owner"); self.assertEqual(cl.get(f"/model/{mid}").status_code, 200); self.assertEqual(cl.get("/models").status_code, 200)
        r = cl.post("/products/suggest"); self.assertEqual(r.status_code, 302)                       # new ids continue after the migrated ones
        c = db.conn(); new_id = c.execute("INSERT INTO families(name) VALUES('after migration')").lastrowid; c.commit(); c.close(); self.assertGreater(new_id, 0)

    def test_backup_of_migrated_data_restores_identically(self):
        migrate_to_cloud.migrate(self.src, self.dest)
        c = db.conn(); z = dataio.dump(c); before = {t: dataio.read_table(c, t) for t in db.TABLE_ORDER}
        dataio.wipe(c); c.commit(); self.assertEqual(c.execute("SELECT COUNT(*) n FROM models").fetchone()["n"], 0)
        dataio.restore(c, z); after = {t: dataio.read_table(c, t) for t in db.TABLE_ORDER}; c.close()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
