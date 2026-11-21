import os, re, glob, shutil, unittest
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from common import *
import dedupe, integrity, dictionary


class DbTests(unittest.TestCase):
    def setUp(self): reset()

    def test_pg_translation(self):
        self.assertEqual(db._pg_sql("SELECT * FROM models WHERE code LIKE ? AND x=?", True)[0], "SELECT * FROM models WHERE code ILIKE %s AND x=%s")
        sql, ret = db._pg_sql("INSERT INTO models(code) VALUES(?)", True); self.assertTrue(ret and sql.endswith("RETURNING id"))
        sql, ret = db._pg_sql("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", True); self.assertFalse(ret)
        self.assertIn("'%speed%'", db._pg_sql("SELECT 1 WHERE a LIKE '%speed%'", False)[0])
        self.assertIn("'%%x%%'", db._pg_sql("SELECT 1 WHERE a='%x%' AND b=?", True)[0])

    def test_sql_portability_lint(self):
        bad = re.compile(r"INSERT OR (IGNORE|REPLACE)|AUTOINCREMENT|datetime\('now'\)|GROUP_CONCAT|IFNULL\(|\browid\b|PRAGMA ")
        for f in glob.glob(os.path.join(ROOT, "*.py")):
            if os.path.basename(f) in ("db.py", "migrate_to_cloud.py"): continue
            for n, line in enumerate(open(f, encoding="utf-8"), 1):
                self.assertFalse(bad.search(line), f"{os.path.basename(f)}:{n} SQLite-only syntax: {line.strip()}")

    def test_fresh_schema_fk_and_version(self):
        c = db.conn()
        try:
            self.assertEqual(db.get_version(c), db.SCHEMA_VERSION)
            with self.assertRaises(Exception):
                c.execute("INSERT INTO models(catalog_id,code) VALUES(999,'X')"); c.commit()
        finally:
            c.rollback(); c.close()

    @unittest.skipIf(db.IS_PG, "SQLite-only: upgrades a legacy SQLite file")
    def test_legacy_upgrade(self):
        p = os.path.join(TMP, "legacy_copy.db"); shutil.copy(os.path.join(ROOT, "tests/fixtures/legacy_data/catalog.db"), p)
        try:
            db.use_sqlite(p); db.init()
            c = db.conn()
            self.assertIn("source_page", db.table_columns(c, "specs")); self.assertIn("username", db.table_columns(c, "audit"))
            self.assertEqual(db.get_version(c), db.SCHEMA_VERSION); self.assertGreater(c.execute("SELECT COUNT(*) n FROM models").fetchone()["n"], 0); c.close()
        finally:
            db.use_url(MAIN_URL)

    def test_backup_restore_roundtrip_keeps_ids(self):
        c = db.conn(); db.set_actor("tests")
        cid = c.execute("INSERT INTO catalogs(name,supplier,file_sha,status) VALUES('a','S','sha1','done')").lastrowid
        fid = c.execute("INSERT INTO families(name) VALUES('F')").lastrowid
        pid = c.execute("INSERT INTO products(name,family_id) VALUES('P',?)", (fid,)).lastrowid
        mid = c.execute("INSERT INTO models(catalog_id,code,family_id,product_id) VALUES(?,?,?,?)", (cid, "GQ40", fid, pid)).lastrowid
        c.execute("INSERT INTO specs(model_id,attr,supplier_value) VALUES(?,?,?)", (mid, "Motor power", "3 kW"))
        c.execute("UPDATE catalogs SET previous_catalog_id=? WHERE id=?", (cid, cid)); c.commit()
        z = dataio.dump(c); dataio.verify(z)
        c.execute("DELETE FROM specs"); c.commit()
        counts = dataio.restore(c, z)
        self.assertEqual(counts["specs"], 1)
        self.assertEqual(c.execute("SELECT id FROM models").fetchone()["id"], mid)
        self.assertEqual(c.execute("SELECT previous_catalog_id p FROM catalogs").fetchone()["p"], cid)
        self.assertEqual(c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"], 3)
        c.close()
        bad = bytearray(z); bad[200] ^= 0xFF
        with self.assertRaises(Exception): dataio.verify(bytes(bad))


class AuthSecurityTests(unittest.TestCase):
    def setUp(self): reset()

    def test_login_required_and_logout(self):
        cl = A.app.test_client()
        r = cl.get("/models"); self.assertEqual(r.status_code, 302); self.assertIn("/login", r.headers["Location"])
        self.assertEqual(cl.get("/healthz").status_code, 200)
        cl = client_for("alice"); self.assertEqual(cl.get("/models").status_code, 200)
        cl.post("/logout"); self.assertEqual(cl.get("/models").status_code, 302)

    def test_lockout_after_failures(self):
        cl = A.app.test_client()
        for _ in range(5): cl.post("/login", data={"username": "bob", "password": "nope"})
        r = cl.post("/login", data={"username": "bob", "password": "bobpass123"})
        self.assertEqual(r.status_code, 200)                       # correct password refused while locked

    def test_owner_only_pages(self):
        a, o = client_for("alice"), client_for("owner")
        for u in ("/users", "/backup", "/backup/download"):
            self.assertEqual(a.get(u).status_code, 403, u); self.assertEqual(o.get(u).status_code, 200, u)
        self.assertEqual(a.post("/users", data={"username": "x1", "password": "password1"}).status_code, 403)

    def test_cross_site_post_rejected(self):
        cl = client_for("alice")
        c = db.conn(); c.execute("INSERT INTO catalogs(name,file_sha,status) VALUES('a','h','done')"); mid = None
        cid = c.execute("SELECT id FROM catalogs").fetchone()["id"]
        mid = c.execute("INSERT INTO models(catalog_id,code) VALUES(?,?)", (cid, "M1")).lastrowid; c.commit(); c.close()
        r = cl.post("/models/bulk", data={"action": "disable", "ids": [str(mid)]}, headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status_code, 403); self.assertEqual(scalar("SELECT active FROM models WHERE id=?", mid), 1)
        r = cl.post("/models/bulk", data={"action": "disable", "ids": [str(mid)]}, headers={"Origin": "http://localhost"})
        self.assertEqual(r.status_code, 302); self.assertEqual(scalar("SELECT active FROM models WHERE id=?", mid), 0)

    def test_html_in_pdf_text_is_escaped(self):
        cl = client_for("alice")
        c = db.conn(); cid = c.execute("INSERT INTO catalogs(name,file_sha,status) VALUES('<b>n</b>','h','done')").lastrowid
        mid = c.execute("INSERT INTO models(catalog_id,code,series) VALUES(?,?,?)", (cid, "M1", "<b id=xss>boom</b>")).lastrowid
        c.execute("INSERT INTO specs(model_id,attr,supplier_value) VALUES(?,?,?)", (mid, "<img src=x onerror=alert(1)>", "<script>alert(2)</script>")); c.commit(); c.close()
        for u in (f"/model/{mid}", "/models", "/search?status=all", "/dictionary", "/", "/tree"):
            body = cl.get(u).get_data(as_text=True)
            for raw in ("<script>alert(2)", "<img src=x onerror", "<b id=xss>"):
                self.assertNotIn(raw, body, f"{u} leaks raw HTML {raw}")

    def test_login_next_is_not_an_open_redirect(self):
        cl = A.app.test_client()
        r = cl.post("/login?next=//evil.example", data={"username": "alice", "password": "alicepass1"})
        self.assertEqual(r.headers["Location"], "/")

    def test_actor_recorded_in_audit(self):
        cl = client_for("bob")
        c = db.conn(); cid = c.execute("INSERT INTO catalogs(name,file_sha,status) VALUES('a','h','done')").lastrowid
        mid = c.execute("INSERT INTO models(catalog_id,code) VALUES(?,?)", (cid, "M1")).lastrowid; c.commit(); c.close()
        cl.post("/models/bulk", data={"action": "hide", "ids": [str(mid)]})
        self.assertEqual(scalar("SELECT username FROM audit WHERE entity='model' AND action='hide'"), "bob")
        self.assertIn("bob", client_for("alice").get("/activity").get_data(as_text=True))

    def test_add_user_and_password_change(self):
        o = client_for("owner")
        o.post("/users", data={"username": "carol", "password": "carolpass1", "display_name": "Carol"})
        c = client_for("carol", "carolpass1")
        c.post("/account", data={"old": "carolpass1", "new": "newpass123", "new2": "newpass123"})
        client_for("carol", "newpass123")


class ExtractorTests(unittest.TestCase):
    def setUp(self):
        reset(); self.cl = client_for("alice"); self.d = os.path.join(TMP, "pdf"); os.makedirs(self.d, exist_ok=True)
        for n, col in (("logo", (10, 10, 10)), ("p1", (200, 30, 30)), ("p2", (30, 200, 30)), ("p3", (30, 30, 200))):
            make_image(os.path.join(self.d, n + ".jpg"), col, (300, 200) if n == "logo" else (400, 300))

    def p(self, n): return os.path.join(self.d, n)

    def test_table_layouts_images_and_review_flags(self):
        logo = self.p("logo.jpg")
        pages = [dict(title="GW Heavy", models={"GW40": {"Motor power": "3 kW", "Bending speed": "18 rpm"}, "GW50": {"Motor power": "4 kW", "Bending speed": "14 rpm"}}, image=logo),
                 dict(title="TQ Series", transposed=True, models={"TQ10": {"Motor power": "1 kW", "Speed": "10 rpm"}, "TQ20": {"Motor power": "2 kW", "Speed": "20 rpm"}}, image=logo),
                 dict(title="GQ Standard", models={"GQ40": {"Motor power": "3 kW"}}, image=logo),
                 dict(title="GS", models={"GS10": {"Motor power": "1 kW"}}, image=logo)]
        make_pdf(self.p("rich.pdf"), pages)
        upload(self.cl, self.p("rich.pdf"), "SupA")
        self.assertEqual(scalar("SELECT status FROM catalogs"), "done")
        codes = {r["code"] for r in q("SELECT code FROM models")}
        self.assertEqual(codes, {"GW40", "GW50", "TQ10", "TQ20", "GQ40", "GS10"})
        self.assertEqual(scalar("SELECT supplier_value FROM specs s JOIN models m ON m.id=s.model_id WHERE m.code='TQ20' AND s.attr='Speed'"), "20 rpm")
        self.assertGreater(scalar("SELECT COUNT(*) FROM specs WHERE source_page IS NOT NULL AND verified=1"), 8)
        kinds = {r["kind"] for r in q("SELECT kind FROM review")}
        self.assertIn("REPEATED_IMAGE", kinds)
        self.assertEqual(scalar("SELECT COUNT(*) FROM image_links WHERE confidence='high'"), 0)      # the only image is a repeated logo

    def test_merged_cell_is_reported_not_silently_dropped(self):
        from reportlab.platypus import Table, TableStyle
        from reportlab.lib import colors
        path = self.p("merged.pdf"); c = canvas.Canvas(path, pagesize=A4, invariant=1); W, H = A4
        c.setFont("Helvetica", 12); c.drawString(50, H - 60, "GW Series " + FILLER * 3)
        t = Table([["Model", "GW40", "GW50"], ["Motor power", "3 kW", "4 kW"], ["Power supply", "380V 50Hz", ""]], colWidths=[110, 100, 100])
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black), ("SPAN", (1, 2), (2, 2))])); t.wrapOn(c, W, H); t.drawOn(c, 50, H - 250); c.showPage(); c.save()
        upload(self.cl, path, "S")
        self.assertIn("MERGED_CELL", {r["kind"] for r in q("SELECT kind FROM review")})

    def test_standards_are_not_model_labels(self):
        self.assertTrue(extractor.LABEL_BLACK.match("IP54")); self.assertTrue(extractor.LABEL_BLACK.match("ISO9001")); self.assertFalse(extractor.LABEL_BLACK.match("GQ-40"))

    def test_failed_extraction_leaves_nothing_and_can_be_retried(self):
        make_pdf(self.p("a.pdf"), [dict(models={"GQ40": {"Motor power": "3 kW"}})])
        orig = extractor._extract

        def boom(c, cid, pdf, pdfi):
            c.execute("INSERT INTO models(catalog_id,code) VALUES(?,?)", (cid, "HALF")); c.commit(); raise RuntimeError("simulated crash")
        extractor._extract = boom
        try: upload(self.cl, self.p("a.pdf"), "S")
        finally: extractor._extract = orig
        cid = scalar("SELECT id FROM catalogs")
        self.assertEqual(scalar("SELECT status FROM catalogs"), "error"); self.assertEqual(scalar("SELECT COUNT(*) FROM models"), 0)
        self.cl.post(f"/catalog/{cid}/retry")
        self.assertEqual(scalar("SELECT status FROM catalogs"), "done"); self.assertEqual(scalar("SELECT COUNT(*) FROM models"), 1)

    def test_stale_state_and_delete_rules(self):
        make_pdf(self.p("a.pdf"), [dict(models={"GQ40": {"Motor power": "3 kW"}})])
        upload(self.cl, self.p("a.pdf"), "S"); cid = scalar("SELECT id FROM catalogs")
        c = db.conn(); c.execute("UPDATE catalogs SET status='processing', heartbeat='2000-01-01 00:00:00' WHERE id=?", (cid,)); c.commit(); c.close()
        import catalogs
        self.assertEqual(catalogs.annotate(q("SELECT * FROM catalogs"))[0]["state"], "stale")
        self.cl.post(f"/catalog/{cid}/retry"); self.assertEqual(scalar("SELECT status FROM catalogs"), "done")
        mid = scalar("SELECT id FROM models"); c = db.conn(); c.execute("UPDATE models SET status='approved' WHERE id=?", (mid,)); c.commit(); c.close()
        self.cl.post(f"/catalog/{cid}/delete"); self.assertEqual(scalar("SELECT COUNT(*) FROM catalogs"), 1)          # approved model blocks deletion
        c = db.conn(); c.execute("UPDATE models SET status='staging' WHERE id=?", (mid,)); c.commit(); c.close()
        self.cl.post(f"/catalog/{cid}/delete")
        self.assertEqual(scalar("SELECT COUNT(*) FROM catalogs"), 0); self.assertEqual(scalar("SELECT COUNT(*) FROM models"), 0)

    def test_source_page_image(self):
        make_pdf(self.p("a.pdf"), [dict(models={"GQ40": {"Motor power": "3 kW"}})]); upload(self.cl, self.p("a.pdf"), "S")
        cid = scalar("SELECT id FROM catalogs"); r = self.cl.get(f"/catalog/{cid}/page/1.png")
        self.assertEqual(r.status_code, 200); self.assertTrue(r.data.startswith(b"\x89PNG")); self.assertEqual(self.cl.get(f"/catalog/{cid}/page/99.png").status_code, 404)


if __name__ == "__main__":
    unittest.main()
