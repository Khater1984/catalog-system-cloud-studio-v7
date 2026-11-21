import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import unittest
from common import *
import ui


class NavTests(unittest.TestCase):
    def test_pages_land_in_the_right_place_and_tab(self):
        cases = {"/": ("home", None), "/catalogs": ("catalogs", "/catalogs"), "/catalog/7": ("catalogs", "/catalogs"),
                 "/review": ("catalogs", "/review"), "/model/12": ("products", "/search"), "/product/3": ("products", "/products"),
                 "/products/merge": ("products", "/products"), "/studio/unlinked-images": ("products", "/studio/unlinked-images"), "/compare": ("products", "/compare"), "/videos": ("products", "/videos"), "/studio/variant/4": ("products", "/products"),
                 "/datasheet/5.pdf": ("export", "/export-center/"), "/catalog.pdf": ("export", "/export-center/"),
                 "/export-settings": ("export", "/export-settings"), "/sync": ("export", "/sync"), "/users": ("admin", "/users"),
                 "/trash": ("admin", "/trash")}
        for path, (g, tab) in cases.items():
            grp, t = ui.locate(path)
            self.assertEqual(grp["key"], g, path); self.assertEqual(t[1] if t else None, tab, path)
        self.assertEqual(ui.locate("/productsx"), (None, None))                 # prefix match respects path boundaries

    def test_every_page_in_the_menu_opens_and_is_highlighted(self):
        reset(); cl = client_for("owner")
        for g in ui.GROUPS[1:]:
            for label, url, _p, fl in g["tabs"]:
                if fl.get("download"): continue
                r = cl.get(url); self.assertEqual(r.status_code, 200, url)
                self.assertIn(f'href="{url}" aria-current="page"', r.get_data(as_text=True), url)

    def test_home_and_owner_only_tabs(self):
        reset(); owner, alice = client_for("owner"), client_for("alice")
        self.assertIn("ما ينتظر عملك", owner.get("/").get_data(as_text=True))
        self.assertIn('href="/backup"', owner.get("/dictionary").get_data(as_text=True))
        self.assertNotIn('href="/backup"', alice.get("/dictionary").get_data(as_text=True))

    def test_model_page_links_to_its_catalog_neighbours_and_export(self):
        import os
        reset(); cl = client_for("owner")
        upload(cl, os.path.join(ROOT, "tests/fixtures/real/ideal_rebar.pdf"), "Ideal")
        ids = [r["id"] for r in q("SELECT id FROM models WHERE page=1 OR page=2 ORDER BY page")]
        html = cl.get(f"/model/{ids[0]}").get_data(as_text=True)
        self.assertIn(f'/model/{ids[1]}', html)                                                    # next model in the same catalog
        self.assertIn(f"/export-center/?doc_type=tds&model_id={ids[0]}", html.replace("&amp;", "&"))     # one click to its data sheet
        cid = scalar("SELECT id FROM catalogs")
        self.assertIn(f"/review?catalog={cid}", cl.get(f"/catalog/{cid}").get_data(as_text=True))
        self.assertEqual(cl.get(f"/review?catalog={cid}").status_code, 200)
        self.assertEqual(cl.get("/review?catalog=999999").status_code, 200)


class IntegrationTests(unittest.TestCase):
    """Pages lead into each other: lists -> export center (with the chosen models), export center -> warnings, review -> source page."""

    @classmethod
    def setUpClass(cls):
        import os
        reset(); cls.cl = client_for("owner")
        upload(cls.cl, os.path.join(ROOT, "tests/fixtures/real/ideal_rebar.pdf"), "Ideal")
        cls.cid = scalar("SELECT id FROM catalogs")

    def get(self, url):
        r = self.cl.get(url); self.assertEqual(r.status_code, 200, url); return r.get_data(as_text=True)

    def test_font_is_cairo_everywhere(self):
        self.assertIn("family=Cairo", self.get("/")); self.assertIn('font-family:"Cairo"', self.get("/"))
        self.assertIn("Cairo", self.cl.get("/login").get_data(as_text=True)) if self.cl.get("/login").status_code == 200 else None

    def test_export_center_prefills_the_models_chosen_in_a_list(self):
        r = self.cl.post("/export-center/", data={"model_ids": "1,2,3"}); html = r.get_data(as_text=True)
        self.assertIn("المحدد من القائمة", html); self.assertIn('name="model_ids" value="1,2,3"', html)
        self.assertRegex(html, r'value="product_catalog"\s+checked')                      # several models -> a catalog by default
        self.assertRegex(self.get("/export-center/?doc_type=tds&model_id=1"), r'value="tds"\s+checked')

    def test_scope_warns_before_export(self):
        s = self.cl.get("/export-center/scope?doc_type=product_catalog&only_approved=1").get_json()
        self.assertEqual(s["n"], 0)                                                         # nothing approved yet: the page disables export
        self.cl.post(f"/catalog/{self.cid}/approve-all")
        s = self.cl.get("/export-center/scope?doc_type=product_catalog&only_approved=1").get_json()
        self.assertEqual((s["n"], s["approved"], s["missing"]), (32, 32, 4)); self.assertIn("GWD32", s["missing_codes"])
        s = self.cl.get("/export-center/scope?doc_type=tds&model_id=" + str(scalar("SELECT id FROM models WHERE code='GQ40'"))).get_json()
        self.assertEqual((s["n"], s["missing"]), (1, 0))

    def test_lists_link_into_export_center_and_sources(self):
        self.assertIn("/export-center/?doc_type=tds&model_id=", self.get("/models"))
        self.assertIn('id="goForm"', self.get("/models")); self.assertIn('id="goForm"', self.get("/search?status=all"))
        self.assertIn(f"/catalog/{self.cid}/page/", self.get("/search?status=all"))        # card -> original catalog page
        self.assertIn("مسار العمل", self.get("/catalogs"))
        self.assertIn(f"/review?catalog={self.cid}", self.get("/catalogs"))

    def test_review_items_open_their_source_page(self):
        if scalar("SELECT COUNT(*) FROM review WHERE ref LIKE 'page %'"):
            self.assertIn("عرض الصفحة", self.get("/review?all=1"))

    def test_product_page_leads_to_its_documents(self):
        pid = scalar("SELECT id FROM products LIMIT 1")
        if pid:
            html = self.get(f"/product/{pid}")
            self.assertIn(f"/export-center/?doc_type=tds&product_id={pid}", html); self.assertIn(f"/models?product={pid}", html)


class WorkbenchTests(IntegrationTests):
    """Own database (this test changes approvals and links)."""
    test_font_is_cairo_everywhere = test_export_center_prefills_the_models_chosen_in_a_list = test_scope_warns_before_export = None
    test_lists_link_into_export_center_and_sources = test_review_items_open_their_source_page = test_product_page_leads_to_its_documents = None

    def test_image_workbench_links_shared_photos_in_one_click(self):
        import re
        orphans = [r["id"] for r in q("SELECT i.id FROM images i WHERE i.page IN (29, 30) ORDER BY i.id")]
        self.assertEqual(len(orphans), 4)                                                   # two photos per page, shared by two models
        self.assertEqual(scalar("SELECT COUNT(*) FROM image_links WHERE image_id IN (%s)" % ",".join(map(str, orphans))), 0)
        before = scalar("SELECT COUNT(*) FROM images i WHERE i.disabled=0 AND NOT EXISTS (SELECT 1 FROM image_links l WHERE l.image_id=i.id AND l.confidence IN ('high','medium','confirmed','manual')) AND NOT EXISTS (SELECT 1 FROM product_image_links p WHERE p.image_id=i.id)")
        self.assertGreater(before, 4)                                                       # + the weak second photos and small details
        html = self.get("/studio/unlinked-images")
        self.assertIn(f"({before})", html); self.assertIn("اربط بالكل (2)", html)
        self.assertIn(f'<span class="badge">{before}</span>', self.get("/products"))        # visible on the image workshop tab
        self.cl.post(f"/catalog/{self.cid}/approve-all")
        self.assertIn("4 معتمد بلا صورة معتمدة", self.get(f"/catalog/{self.cid}"))
        for img in orphans:
            page = scalar("SELECT page FROM images WHERE id=?", img)
            mids = [str(r["id"]) for r in q("SELECT id FROM models WHERE page=? ORDER BY id", page)]
            r = self.cl.post("/studio/image/link-many", data={"image_id": str(img), "model_id": mids}); self.assertEqual(r.status_code, 302)
        self.assertEqual(scalar("SELECT COUNT(*) FROM images i WHERE i.disabled=0 AND NOT EXISTS (SELECT 1 FROM image_links l WHERE l.image_id=i.id AND l.confidence IN ('high','medium','confirmed','manual')) AND NOT EXISTS (SELECT 1 FROM product_image_links p WHERE p.image_id=i.id)"), before - 4)
        s = self.cl.get("/export-center/scope?doc_type=product_catalog&only_approved=1").get_json()
        self.assertEqual((s["n"], s["approved"], s["missing"]), (32, 32, 0))                # every approved model now has an approved picture
        self.assertNotIn("معتمد بلا صورة معتمدة", self.get(f"/catalog/{self.cid}"))
        for code in ("GWD32", "GWD40", "GQD32", "GQD40"):
            self.assertEqual(scalar("SELECT COUNT(*) FROM image_links l JOIN models m ON m.id=l.model_id WHERE m.code=? AND l.is_primary=1 AND l.ok_sheet=1", code), 1, code)


if __name__ == "__main__":
    unittest.main()
