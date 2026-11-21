import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import os, re, sys, unittest
from common import *
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "real"))
from ideal_rebar_truth import TRUTH, GRID

N = lambda s: re.sub(r"\s+", " ", str(s or "")).strip()


class RealCatalogTests(unittest.TestCase):
    """Regression on a REAL supplier catalog (Henan Ideal, 31 pages). Every spec value was typed by hand from the PDF."""

    @classmethod
    def setUpClass(cls):
        reset(); cls.cl = client_for("alice")
        upload(cls.cl, os.path.join(ROOT, "tests/fixtures/real/ideal_rebar.pdf"), "Ideal")
        cls.models = q("SELECT * FROM models ORDER BY id")

    def test_every_model_and_spec_is_exact(self):
        by = {(m["page"], m["code"]): m for m in self.models}
        bad = []
        for page, code, specs in TRUTH:
            m = by.get((page, code))
            if not m: bad.append(("missing model", page, code)); continue
            got = {N(s["attr"]): N(s["supplier_value"]) for s in q("SELECT attr, supplier_value FROM specs WHERE model_id=?", m["id"])}
            for a, v in specs.items():
                if N(got.get(a)) != N(v): bad.append((page, code, a, v, got.get(a)))
            bad += [(page, code, "extra", a) for a in got if a not in specs]
        self.assertEqual(bad, [])
        self.assertEqual(scalar("SELECT COUNT(*) FROM specs WHERE verified=0"), 0)       # wrapped two-column cells are not false alarms

    def test_grid_page_models_and_suffix(self):
        grid = {m["code"]: m for m in self.models if m["page"] == 31}
        self.assertEqual(len(grid), len(GRID))
        for code in GRID: self.assertIn(code, grid, code)
        self.assertEqual({c for c, m in grid.items() if m["series"] == "Li-ion"}, {"RC-16B", "RC-20B", "RC-18B", "BC-300"})   # '(Li-ion)' is a note, not part of the code

    def test_jpx_pictures_are_rendered_not_decoded_raw(self):
        self.assertEqual(scalar("SELECT COUNT(*) FROM images WHERE page=31 AND source<>'rendered'"), 0)
        self.assertEqual(scalar("SELECT COUNT(*) FROM review WHERE kind='IMAGE_RENDERED'"), 0)

    def test_series_is_the_heading_descriptor_only(self):
        s = {(m["page"], m["code"]): m["series"] for m in self.models}
        self.assertEqual(s[(1, "GQ40")], "Standard Series"); self.assertEqual(s[(6, "GQ42")], "Signature Series")   # no 'S ignature', no category title
        self.assertEqual(s[(4, "GQ60")], "Heavy type")
        for v in s.values(): self.assertNotIn("Rebar", v)

    def test_same_code_in_two_series_stays_two_models(self):
        for code in ("GQ42", "GQ50", "GW50C"): self.assertEqual(sum(1 for m in self.models if m["code"] == code), 2, code)

    def test_approve_all_gives_every_model_with_specs_an_approved_image(self):
        cid = scalar("SELECT id FROM catalogs")
        r = self.cl.get(f"/catalog/{cid}"); html = r.get_data(as_text=True)
        self.assertIn("مسار العمل", html); self.assertIn("اعتمد الجاهز مع صوره", html)
        self.assertEqual(self.cl.post(f"/catalog/{cid}/approve-all").status_code, 302)
        spec_models = scalar("SELECT COUNT(*) FROM models m WHERE EXISTS (SELECT 1 FROM specs s WHERE s.model_id=m.id)")
        self.assertEqual(scalar("SELECT COUNT(*) FROM models WHERE status='approved'"), spec_models)               # models without specs stay for review
        no_img = q("""SELECT m.code, m.page FROM models m WHERE m.status='approved' AND NOT EXISTS
                      (SELECT 1 FROM image_links l WHERE l.model_id=m.id AND l.ok_sheet=1)""")
        self.assertEqual([(r["code"], r["page"]) for r in no_img], [("GWD32", 29), ("GWD40", 29), ("GQD32", 30), ("GQD40", 30)])   # shared photos: the engine leaves them to a person
        self.assertEqual(scalar("SELECT COUNT(*) FROM models WHERE page=31 AND status='approved'"), 0)               # grid models have no specs, so they are not approved blindly
        self.assertIn("طراز معتمد", self.cl.get(f"/catalog/{cid}").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
