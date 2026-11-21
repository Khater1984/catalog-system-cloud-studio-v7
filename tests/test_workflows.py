import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import os, re, io, json, threading, unittest
from common import *
import dedupe, integrity, catalogs

BASE_MODELS = {f"GQ{n}": {"Motor power": f"{n // 10} kW", "Cutting rate": f"{n} cuts/min", "Net weight": f"{n * 5} kg"} for n in (40, 50, 60, 70, 80, 90, 100, 110, 120, 130)}


def pages_for(models, tag="", per=3, title="GQ Standard Series"):
    items = list(models.items()); out = []
    for i in range(0, len(items), per):
        out.append(dict(title=f"{title} {tag}", models=dict(items[i:i + per]), filler=FILLER + f"{tag} section {i}. "))
    return out


class DedupeTests(unittest.TestCase):
    def setUp(self):
        reset(); self.cl = client_for("alice"); self.d = os.path.join(TMP, "dd"); os.makedirs(self.d, exist_ok=True)

    def mk(self, name, models, tag="", title="catalog", **kw):
        p = os.path.join(self.d, name); make_pdf(p, pages_for(models, tag, **kw), title=title, tag=tag); return p

    def cat(self, name): return q("SELECT * FROM catalogs WHERE name=?", name)[0]

    def test_level1_exact_copy_blocked(self):
        p = self.mk("a.pdf", BASE_MODELS, "A"); upload(self.cl, p, "SupA", "1.0")
        r = upload(self.cl, p, "SupA", "1.0", name="renamed_copy.pdf")
        self.assertEqual(scalar("SELECT COUNT(*) FROM catalogs"), 1)
        self.assertEqual(r.status_code, 302)
        self.assertIn("سبق رفع هذا الكتالوج", self.cl.get("/").get_data(as_text=True))

    def test_level2_same_text_different_file_asks_user(self):
        upload(self.cl, self.mk("a.pdf", BASE_MODELS, "A", title="orig"), "SupA", "1.0")
        upload(self.cl, self.mk("b_renamed.pdf", BASE_MODELS, "A", title="other metadata"), "SupA", "1.0")
        b = self.cat("b_renamed.pdf"); self.assertEqual(b["status"], "needs_decision")
        self.assertEqual(json.loads(b["dup_info"])["kind"], "same_content"); self.assertEqual(scalar("SELECT COUNT(*) FROM models WHERE catalog_id=?", b["id"]), 0)
        self.cl.post(f"/catalog/{b['id']}/continue"); self.assertEqual(self.cat("b_renamed.pdf")["status"], "done")     # user decided to keep it
        self.assertEqual(scalar("SELECT COUNT(*) FROM audit WHERE action='duplicate_accepted' AND username='alice'"), 1)

    def test_level2_cancel_removes_catalog(self):
        upload(self.cl, self.mk("a.pdf", BASE_MODELS, "A", title="orig"), "S")
        upload(self.cl, self.mk("b.pdf", BASE_MODELS, "A", title="x"), "S")
        b = self.cat("b.pdf"); self.cl.post(f"/catalog/{b['id']}/delete")
        self.assertEqual(scalar("SELECT COUNT(*) FROM catalogs"), 1)

    def test_level3_same_supplier_same_release_asks_user(self):
        upload(self.cl, self.mk("a.pdf", BASE_MODELS, "A"), "SupA", "2.0", "2025-03")
        other = {k + "X": v for k, v in BASE_MODELS.items()}
        upload(self.cl, self.mk("c.pdf", other, "C"), "supa", "2.0", "2025-03")
        self.assertEqual(json.loads(self.cat("c.pdf")["dup_info"])["kind"], "same_release")
        self.assertEqual(self.cat("c.pdf")["status"], "needs_decision")

    def test_level3_newer_release_allowed_and_linked(self):
        upload(self.cl, self.mk("v1.pdf", BASE_MODELS, "V1"), "SupA", "1.0", "2024-05")
        v2 = dict(BASE_MODELS); v2["GQ40"] = dict(v2["GQ40"], **{"Motor power": "9 kW"}); v2["GQ140"] = {"Motor power": "9 kW", "Cutting rate": "140 cuts/min", "Net weight": "700 kg"}
        upload(self.cl, self.mk("v2.pdf", v2, "V2"), "SupA", "2.0", "2025-06")
        c2, c1 = self.cat("v2.pdf"), self.cat("v1.pdf")
        self.assertEqual(c2["status"], "done"); self.assertEqual(c2["previous_catalog_id"], c1["id"])
        kinds = {r["kind"] for r in q("SELECT kind FROM review WHERE catalog_id=?", c2["id"])}
        self.assertIn("SUPERSEDES", kinds); self.assertNotIn("SIMILAR_CATALOG", kinds)
        self.assertEqual(scalar("SELECT COUNT(*) FROM models WHERE catalog_id=?", c1["id"]), 10)         # the older catalog is untouched
        cmp_ = dedupe.compare(db.conn(), c2["id"], c1["id"]); self.assertEqual(cmp_["added"], ["GQ140"]); self.assertEqual([c for c, _ in cmp_["changed"]], ["GQ40"])
        self.assertEqual(self.cl.get(f"/catalog/{c2['id']}").status_code, 200); self.assertEqual(self.cl.get(f"/catalog/{c2['id']}/compare/{c1['id']}").status_code, 200)

    def test_level3_older_release_asks_user(self):
        upload(self.cl, self.mk("new.pdf", BASE_MODELS, "N"), "SupA", "3.0", "2026-01")
        upload(self.cl, self.mk("old.pdf", {k + "Z": v for k, v in BASE_MODELS.items()}, "O"), "SupA", "1.0", "2023-01")
        self.assertEqual(json.loads(self.cat("old.pdf")["dup_info"])["kind"], "older_release")

    def test_level4_updated_catalog_is_not_a_duplicate(self):
        upload(self.cl, self.mk("a.pdf", BASE_MODELS, "A"), "")                         # no supplier / version information at all
        upd = {k: dict(v) for k, v in BASE_MODELS.items()}
        for k in ("GQ40", "GQ50", "GQ60"): upd[k]["Motor power"] = "7.5 kW"             # updated specs on 3 of 10 models
        upd["GQ140"] = {"Motor power": "9 kW", "Cutting rate": "140 cuts/min", "Net weight": "700 kg"}
        upload(self.cl, self.mk("b.pdf", upd, "B"), "")
        kinds = {r["kind"] for r in q("SELECT kind FROM review WHERE catalog_id=?", self.cat("b.pdf")["id"])}
        self.assertIn("LIKELY_UPDATE", kinds); self.assertNotIn("SIMILAR_CATALOG", kinds)

    def test_level4_near_identical_content_is_flagged_for_decision(self):
        upload(self.cl, self.mk("a.pdf", BASE_MODELS, "A"), "")
        upload(self.cl, self.mk("b.pdf", BASE_MODELS, "completely different filler text number two"), "")
        b = self.cat("b.pdf"); self.assertEqual(b["status"], "done")                      # not blocked: the user decides
        self.assertIn("SIMILAR_CATALOG", {r["kind"] for r in q("SELECT kind FROM review WHERE catalog_id=?", b["id"])})
        self.cl.post(f"/catalog/{b['id']}/not-duplicate")
        self.assertEqual(scalar("SELECT COUNT(*) FROM review WHERE kind='SIMILAR_CATALOG' AND resolved=0"), 0)

    def test_unrelated_catalog_raises_nothing(self):
        upload(self.cl, self.mk("a.pdf", BASE_MODELS, "A"), "SupA", "1.0")
        upload(self.cl, self.mk("z.pdf", {"ZZ10": {"Motor power": "1 kW"}, "ZZ20": {"Motor power": "2 kW"}}, "Z", title="zz"), "SupB", "1.0")
        self.assertEqual(self.cat("z.pdf")["status"], "done")
        self.assertEqual(scalar("SELECT COUNT(*) FROM review WHERE kind IN ('SIMILAR_CATALOG','LIKELY_UPDATE','SUPERSEDES')"), 0)


class ConcurrencyTests(unittest.TestCase):
    def setUp(self):
        reset(); self.d = os.path.join(TMP, "cc"); os.makedirs(self.d, exist_ok=True)
        self.users = {u: client_for(u) for u in ("owner", "alice", "bob")}

    def test_two_uploads_at_the_same_time(self):
        a = os.path.join(self.d, "a.pdf"); b = os.path.join(self.d, "b.pdf")
        make_pdf(a, pages_for(BASE_MODELS, "A"), tag="A"); make_pdf(b, pages_for({k + "B": v for k, v in BASE_MODELS.items()}, "B"), tag="B")
        errs = []

        def go(cl, p, sup):
            try: upload(cl, p, sup)
            except Exception as e: errs.append(e)
        ts = [threading.Thread(target=go, args=(self.users["alice"], a, "SA")), threading.Thread(target=go, args=(self.users["bob"], b, "SB"))]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(errs, [])
        self.assertEqual(sorted(r["status"] for r in q("SELECT status FROM catalogs")), ["done", "done"])
        self.assertEqual(scalar("SELECT COUNT(*) FROM models"), 20)
        self.assertEqual({r["uploaded_by"] for r in q("SELECT uploaded_by FROM catalogs")}, {"alice", "bob"})

    def test_same_catalog_uploaded_twice_at_once(self):
        p = os.path.join(self.d, "a.pdf"); make_pdf(p, pages_for(BASE_MODELS, "A"), tag="A"); errs = []

        def go(cl):
            try: upload(cl, p, "S")
            except Exception as e: errs.append(e)
        ts = [threading.Thread(target=go, args=(self.users[u],)) for u in ("alice", "bob")]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(errs, []); self.assertEqual(scalar("SELECT COUNT(*) FROM catalogs"), 1); self.assertEqual(scalar("SELECT COUNT(*) FROM models"), 10)

    def _seed_model(self):
        c = db.conn(); cid = c.execute("INSERT INTO catalogs(name,file_sha,status) VALUES('a','h','done')").lastrowid
        mid = c.execute("INSERT INTO models(catalog_id,code) VALUES(?,?)", (cid, "GQ40")).lastrowid
        c.execute("INSERT INTO specs(model_id,attr,supplier_value) VALUES(?,?,?)", (mid, "Motor power", "3 kW")); c.commit(); c.close(); return mid

    def test_two_users_edit_the_same_model(self):
        mid = self._seed_model()
        a, b = self.users["alice"], self.users["bob"]
        va = re.search(r'name="version" value="(\d+)"', a.get(f"/model/{mid}").get_data(as_text=True)).group(1)
        vb = re.search(r'name="version" value="(\d+)"', b.get(f"/model/{mid}").get_data(as_text=True)).group(1)
        sid = scalar("SELECT id FROM specs"); self.assertEqual(va, vb)
        a.post(f"/model/{mid}/save", data={"version": va, "ar_name": "قاطع أ", "visible": "on", f"cv_{sid}": "4 kW"})
        r = b.post(f"/model/{mid}/save", data={"version": vb, "ar_name": "قاطع ب", "visible": "on", f"cv_{sid}": "5 kW"}, follow_redirects=True)
        self.assertIn("alice", r.get_data(as_text=True))                                 # bob is told who changed it
        self.assertEqual(scalar("SELECT ar_name FROM models WHERE id=?", mid), "قاطع أ"); self.assertEqual(scalar("SELECT company_value FROM specs WHERE id=?", sid), "4 kW")
        vb2 = re.search(r'name="version" value="(\d+)"', b.get(f"/model/{mid}").get_data(as_text=True)).group(1)
        b.post(f"/model/{mid}/save", data={"version": vb2, "ar_name": "قاطع ب", "visible": "on", f"cv_{sid}": "5 kW"})
        self.assertEqual(scalar("SELECT company_value FROM specs WHERE id=?", sid), "5 kW")
        hist = q("SELECT username, action, source_value, old_value, new_value FROM spec_changes ORDER BY id")
        self.assertEqual([(h["username"], h["old_value"], h["new_value"], h["source_value"]) for h in hist], [("alice", "", "4 kW", "3 kW"), ("bob", "4 kW", "5 kW", "3 kW")])
        self.assertEqual(scalar("SELECT supplier_value FROM specs WHERE id=?", sid), "3 kW")       # the source value is never touched

    def test_model_added_by_one_user_visible_to_another(self):
        mid = self._seed_model()
        self.assertIn("GQ40", self.users["bob"].get("/models").get_data(as_text=True)); self.assertEqual(self.users["owner"].get(f"/model/{mid}").status_code, 200)


class RestoreTests(unittest.TestCase):
    def setUp(self):
        reset(); self.cl = client_for("alice"); self.d = os.path.join(TMP, "rs"); os.makedirs(self.d, exist_ok=True)
        make_image(os.path.join(self.d, "i.jpg"), (50, 90, 200))

    def test_deleted_catalog_can_be_restored_with_same_ids(self):
        p = os.path.join(self.d, "a.pdf"); make_pdf(p, [dict(models={"GQ40": {"Motor power": "3 kW"}, "GQ50": {"Motor power": "4 kW"}}, image=os.path.join(self.d, "i.jpg"))], tag="A")
        upload(self.cl, p, "S"); cid = scalar("SELECT id FROM catalogs")
        before = {t: [dict(r) for r in q(f"SELECT * FROM {t} ORDER BY id")] for t in ("models", "specs", "images", "image_links", "review")}
        self.cl.post(f"/catalog/{cid}/delete"); self.assertEqual(scalar("SELECT COUNT(*) FROM models"), 0)
        self.assertIn("alice", self.cl.get("/trash").get_data(as_text=True))
        rid = scalar("SELECT id FROM deleted_records"); self.cl.post(f"/trash/{rid}/restore")
        after = {t: [dict(r) for r in q(f"SELECT * FROM {t} ORDER BY id")] for t in before}
        self.assertEqual(before, after)                                                      # identical rows, identical ids
        self.assertEqual(scalar("SELECT status FROM catalogs"), "done"); self.assertEqual(scalar("SELECT restored FROM deleted_records"), 1)
        self.assertEqual(scalar("SELECT COUNT(*) FROM audit WHERE action='restore' AND username='alice'"), 1)
        self.cl.post(f"/trash/{rid}/restore"); self.assertEqual(scalar("SELECT COUNT(*) FROM catalogs"), 1)         # second restore does nothing

    def test_deleting_a_catalog_keeps_links_of_other_catalogs(self):
        c = db.conn()
        c1 = c.execute("INSERT INTO catalogs(name,file_sha,status) VALUES('a','h1','done')").lastrowid; c2 = c.execute("INSERT INTO catalogs(name,file_sha,status) VALUES('b','h2','done')").lastrowid
        m2 = c.execute("INSERT INTO models(catalog_id,code) VALUES(?,?)", (c2, "M2")).lastrowid
        img = c.execute("INSERT INTO images(catalog_id,page,file,sha) VALUES(?,?,?,?)", (c1, 1, "f.jpg", "s")).lastrowid
        c.execute("INSERT INTO image_links(image_id,model_id,confidence) VALUES(?,?,'high')", (img, m2)); c.commit(); c.close()
        self.cl.post(f"/catalog/{c1}/delete")
        self.assertEqual(scalar("SELECT COUNT(*) FROM catalogs WHERE id=?", c1), 0)
        self.assertEqual(scalar("SELECT COUNT(*) FROM image_links"), 1); self.assertEqual(scalar("SELECT COUNT(*) FROM images"), 1)
        self.assertIsNone(scalar("SELECT catalog_id FROM images"))
        self.cl.post(f"/trash/{scalar('SELECT id FROM deleted_records')}/restore")
        self.assertEqual(scalar("SELECT catalog_id FROM images"), c1)


class WorkflowTests(unittest.TestCase):
    def setUp(self): reset(); self.cl = client_for("alice")

    def seed(self, supplier="S1", code="GQ40", series="GQ Standard Series", cat_sha="h1", specs=None):
        c = db.conn(); row = c.execute("SELECT id FROM catalogs WHERE file_sha=?", (cat_sha,)).fetchone()
        cid = row["id"] if row else c.execute("INSERT INTO catalogs(name,supplier,file_sha,status) VALUES(?,?,?,'done')", (cat_sha, supplier, cat_sha)).lastrowid
        mid = c.execute("INSERT INTO models(catalog_id,code,series,page,suggested_group) VALUES(?,?,?,1,?)", (cid, code, series, re.match(r"[A-Za-z]+", code).group(0))).lastrowid
        for i, (a, v) in enumerate((specs or {"Motor power": "3 kW", "Power supply": "380V 50Hz", "Cutting rate": "32 cuts/min"}).items()):
            c.execute("INSERT INTO specs(model_id,attr,supplier_value,sort) VALUES(?,?,?,?)", (mid, a, v, i))
        c.commit(); c.close(); return mid

    def test_frequency_rule_covers_dictionary_names_and_is_reversible(self):
        m1 = self.seed(); m2 = self.seed(code="IT10", cat_sha="h2", specs={"alimentazione": "400V 50Hz"})
        self.cl.post("/rules", data={"hz": "60"})
        self.assertEqual(scalar("SELECT company_value FROM specs WHERE model_id=? AND attr='Power supply'", m1), "380V 60 Hz")
        self.assertEqual(scalar("SELECT company_value FROM specs WHERE model_id=? AND attr='alimentazione'", m2), "400V 60 Hz")
        self.assertEqual(scalar("SELECT supplier_value FROM specs WHERE model_id=? AND attr='Power supply'", m1), "380V 50Hz")
        self.assertEqual(scalar("SELECT catalog_id FROM review WHERE kind='RULE_SIDE_EFFECT'"), None)
        self.assertGreaterEqual(scalar("SELECT COUNT(*) FROM spec_changes WHERE action='UPDATE' AND username='alice'"), 2)
        self.cl.post("/rules", data={"do": "clear"}); self.assertEqual(scalar("SELECT COUNT(*) FROM specs WHERE rule='FREQ'"), 0)
        self.cl.post("/rules", data={"hz": "abc"}); self.assertEqual(self.cl.post("/rules", data={"hz": "abc"}).status_code, 302)

    def test_bulk_family_keeps_product_and_family_consistent(self):
        m1, m2 = self.seed(), self.seed(code="GS10", cat_sha="h1")
        c = db.conn(); f1 = c.execute("INSERT INTO families(name) VALUES('Cutters')").lastrowid; f2 = c.execute("INSERT INTO families(name) VALUES('Benders')").lastrowid
        p = c.execute("INSERT INTO products(name,family_id) VALUES('P',?)", (f1,)).lastrowid; c.commit(); c.close()
        self.cl.post("/models/bulk", data={"action": "product", "product_id": str(p), "ids": [str(m1)]})
        self.cl.post("/models/bulk", data={"action": "family", "family_id": str(f2), "ids": [str(m1), str(m2)]})
        self.assertEqual(scalar("SELECT family_id FROM models WHERE id=?", m1), f1)          # follows its product
        self.assertEqual(scalar("SELECT family_id FROM models WHERE id=?", m2), f2)
        self.assertEqual(integrity.run(db.conn()), [])
        c = db.conn(); c.execute("UPDATE models SET family_id=? WHERE id=?", (f2, m1)); c.commit()
        self.assertEqual(integrity.run(c)[0]["kind"], "MODEL_FAMILY_MISMATCH"); self.assertEqual(integrity.fix_family(c), 1); c.commit(); c.close()
        self.assertEqual(scalar("SELECT family_id FROM models WHERE id=?", m1), f1)

    def test_products_of_different_suppliers_are_not_merged_automatically(self):
        a = self.seed("SupA", "GQ40", "GQ Standard Series", "h1"); b = self.seed("SupB", "XQ40", "GQ Standard Series", "h2")
        self.cl.post("/products/suggest")
        names = [r["name"] for r in q("SELECT name FROM products ORDER BY id")]
        pa, pb = scalar("SELECT product_id FROM models WHERE id=?", a), scalar("SELECT product_id FROM models WHERE id=?", b)
        self.assertNotEqual(pa, pb); self.assertEqual(len(names), 2)
        body = self.cl.get("/products/merge").get_data(as_text=True)                          # same series + same attributes: proposed to the user
        self.assertIn("SupA".lower(), body.lower()); self.assertIn(">", body)
        self.cl.post("/products/merge-do", data={"keep": str(pa), "drop": str(pb)})
        self.assertEqual(scalar("SELECT product_id FROM models WHERE id=?", b), pa); self.assertEqual(scalar("SELECT active FROM products WHERE id=?", pb), 0)
        self.assertEqual(scalar("SELECT COUNT(*) FROM audit WHERE entity='product' AND action IN ('merge_in','merged_into')"), 2)

    def test_same_supplier_series_reuses_product(self):
        self.seed("SupA", "GQ40", "GQ Standard Series", "h1"); self.seed("SupA", "GQ50", "GQ Standard Series", "h1")
        self.cl.post("/products/suggest"); self.assertEqual(scalar("SELECT COUNT(*) FROM products"), 1)

    def test_approve_with_images_only_trusts_high_confidence(self):
        m = self.seed(); c = db.conn(); cid = scalar("SELECT id FROM catalogs")
        for i, conf in enumerate(("high", "medium", "review")):
            img = c.execute("INSERT INTO images(catalog_id,page,file,sha) VALUES(?,?,?,?)", (cid, 1, f"f{i}.jpg", f"s{i}")).lastrowid
            c.execute("INSERT INTO image_links(image_id,model_id,confidence) VALUES(?,?,?)", (img, m, conf))
        c.commit(); c.close()
        self.cl.post("/models/bulk", data={"action": "approve", "with_images": "1", "ids": [str(m)]})
        self.assertEqual([r["confidence"] for r in q("SELECT confidence FROM image_links WHERE ok_sheet=1")], ["high"])

    def test_exports_and_error_paths(self):
        self.seed()
        r = self.cl.get("/export.xlsx"); self.assertEqual(r.status_code, 200)
        import openpyxl; wb = openpyxl.load_workbook(io.BytesIO(r.data)); ws = wb["Specifications"]
        self.assertEqual([c.value for c in ws[1]][:4], ["Model ID", "Model", "Attribute", "Attribute (as written by supplier)"])
        self.assertEqual(self.cl.get("/catalog.pdf?family=999").status_code, 404); self.assertEqual(self.cl.get("/catalog.pdf").status_code, 404)

    def test_every_page_renders_for_every_user_kind(self):
        self.seed()
        for u in ("alice", "owner"):
            cl = client_for(u)
            for url in ("/", "/models", "/tree", "/products", "/products/merge", "/gaps", "/review", "/rules", "/dictionary", "/search", "/export-settings", "/sync", "/activity", "/trash", "/account"):
                self.assertEqual(cl.get(url).status_code, 200, f"{u} {url}")


if __name__ == "__main__":
    unittest.main()
