import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import os, shutil, subprocess, tempfile, unittest
from common import *

MP4_HEAD = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"


def fake_mp4(n):
    return MP4_HEAD + bytes(range(256)) * (n // 256 + 1)


class HubTests(unittest.TestCase):
    """Product workspace, comparison and marketing videos on the REAL catalog (products created by the system's own suggestion step)."""

    @classmethod
    def setUpClass(cls):
        reset(); cls.cl = client_for("owner")
        upload(cls.cl, os.path.join(ROOT, "tests/fixtures/real/ideal_rebar.pdf"), "Ideal")
        cls.cl.post("/products/suggest")
        cls.cid = scalar("SELECT id FROM catalogs")
        cls.cl.post(f"/catalog/{cls.cid}/approve-all")
        cls.pid = scalar("SELECT product_id FROM models WHERE code='GQ40'")
        cls.mids = [r["id"] for r in q("SELECT id FROM models WHERE product_id=? AND active=1 ORDER BY id", cls.pid)]

    def get(self, url, **kw):
        r = self.cl.get(url, **kw); self.assertEqual(r.status_code, 200, url); return r.get_data(as_text=True)

    # ---- the product page
    def test_every_tab_opens(self):
        self.assertTrue(self.pid and len(self.mids) >= 2)
        for tab in ("overview", "models", "specs", "images", "video", "export", "history"):
            html = self.get(f"/product/{self.pid}?tab={tab}")
            self.assertIn(f'?tab={tab}" aria-current="page"', html, tab)
        self.assertEqual(self.cl.get("/product/999999").status_code, 404)
        self.assertEqual(self.cl.get(f"/studio/product/{self.pid}").status_code, 302)      # old address -> the workspace
        self.assertEqual(self.cl.get("/studio/").status_code, 302)

    def test_specs_matrix_and_inline_add(self):
        html = self.get(f"/product/{self.pid}?tab=specs")
        self.assertIn("مصفوفة المواصفات", html); self.assertIn('class="d"', html)            # at least one differing row is highlighted
        back = f"/product/{self.pid}?tab=specs"
        r = self.cl.post("/studio/spec/mutate", data={"op": "ADD", "product_id": str(self.pid), "attr": "Warranty", "val": "12", "unit": "months"}, headers={"Referer": "http://localhost" + back})
        self.assertEqual(r.status_code, 302); self.assertTrue(r.headers["Location"].endswith(back))   # lands on the same tab
        self.assertIn("Warranty", self.get(back))
        self.assertIn("Warranty", self.get(f"/product/{self.pid}?tab=history"))             # recorded in the change log

    def test_product_save_keeps_the_page_and_merges_fields(self):
        v = scalar("SELECT COALESCE(version,1) FROM products WHERE id=?", self.pid)
        r = self.cl.post(f"/product/{self.pid}/save", data={"name": "Rebar Cutter", "ar_name": "قاطع حديد", "supplier": "Henan Ideal", "manufacturer": "Ideal", "status": "approved",
                                                              "version": str(v), "next": f"/product/{self.pid}?tab=overview"})
        self.assertEqual(r.status_code, 302); self.assertTrue(r.headers["Location"].endswith("tab=overview"))
        row = q("SELECT name, supplier, manufacturer, status FROM products WHERE id=?", self.pid)[0]
        self.assertEqual((row["name"], row["supplier"], row["status"]), ("Rebar Cutter", "Henan Ideal", "approved"))
        self.assertIn("Rebar Cutter", self.get(f"/products"))

    # ---- comparison
    def test_compare_page_and_pdf(self):
        a, b = self.mids[:2]
        html = self.get(f"/compare?ids={a},{b}")
        for code in [r["code"] for r in q("SELECT code FROM models WHERE id IN (?,?)", a, b)]:
            self.assertIn(code, html)
        self.assertIn('class="d"', html); self.assertIn("الفروق فقط", html)
        self.assertIn("الفروق فقط", self.get(f"/compare?product={self.pid}"))
        self.assertIn("اختر طرازين على الأقل", self.get(f"/compare?ids={a}"))
        r = self.cl.post("/pro/compare.pdf", data={"model_ids": [str(a), str(b)]}); self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data.startswith(b"%PDF"))

    # ---- videos
    def put_video(self, data, name="demo.mp4", cut=4096, **meta):
        j = self.cl.post("/video/begin", json={"filename": name, "size": len(data), "product_id": self.pid}); 
        if j.status_code != 200: return j
        uid = j.get_json()["id"]
        for i, off in enumerate(range(0, len(data), cut)):
            self.assertEqual(self.cl.put(f"/video/upload/{uid}/{i}", data=data[off:off + cut]).status_code, 200)
        return self.cl.post(f"/video/upload/{uid}/complete", data=dict(meta))

    def test_video_upload_player_range_update_delete(self):
        data = fake_mp4(10000); staged = scalar("SELECT COUNT(*) FROM upload_chunks")
        r = self.put_video(data, title="GQ40 demo", caption="قص الحديد", hashtags="#GQ40", platforms=["instagram", "tiktok"], status="ready")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)); vid = r.get_json()["id"]
        row = q("SELECT * FROM product_videos WHERE id=?", vid)[0]
        self.assertEqual((row["size"], row["mime"], row["status"], row["platforms"]), (len(data), "video/mp4", "ready", "instagram,tiktok"))
        html = self.get(f"/product/{self.pid}?tab=video")
        self.assertIn("GQ40 demo", html); self.assertIn(f"/video/{vid}/file", html)
        self.assertEqual(self.cl.get(f"/video/{vid}/file").data, data)                                         # byte-exact after chunking
        part = self.cl.get(f"/video/{vid}/file", headers={"Range": "bytes=100-199"})
        self.assertEqual((part.status_code, len(part.data), part.data), (206, 100, data[100:200]))            # seeking works
        self.assertIn("attachment", self.cl.get(f"/video/{vid}/file?dl=1").headers["Content-Disposition"])
        self.assertIn("GQ40 demo", self.get("/videos")); self.assertIn("GQ40 demo", self.get("/videos?status=ready")); self.assertNotIn("GQ40 demo", self.get("/videos?status=published"))
        r = self.cl.post(f"/video/{vid}/update", data={"title": "GQ40 final", "status": "published", "caption": "x", "platforms": "youtube"}); self.assertEqual(r.status_code, 302)
        self.assertEqual(q("SELECT status, platforms FROM product_videos WHERE id=?", vid)[0]["status"], "published")
        self.assertEqual(self.cl.post(f"/video/{vid}/delete").status_code, 302)
        self.assertNotIn("GQ40 final", self.get(f"/product/{self.pid}?tab=video"))
        self.assertEqual(scalar("SELECT COUNT(*) FROM upload_chunks"), staged)                                 # this upload's staging rows were removed

    def test_video_is_validated(self):
        self.assertEqual(self.cl.post("/video/begin", json={"filename": "x.exe", "size": 10, "product_id": self.pid}).status_code, 400)
        self.assertEqual(self.cl.post("/video/begin", json={"filename": "x.mp4", "size": 10 ** 12, "product_id": self.pid}).status_code, 400)
        self.assertEqual(self.put_video(b"not a video at all" * 100, "fake.mp4").status_code, 400)             # content is checked, not the name
        bad = self.cl.post("/video/begin", json={"filename": "y.mp4", "size": 5000, "product_id": self.pid}).get_json()["id"]
        self.cl.put(f"/video/upload/{bad}/0", data=fake_mp4(100))
        self.assertEqual(self.cl.post(f"/video/upload/{bad}/complete", data={}).status_code, 400)             # size mismatch -> refuse
        self.assertEqual(self.cl.post("/video/upload/" + "0" * 32 + "/complete", data={}).status_code, 404)
        d = fake_mp4(3000); first = self.put_video(d, "same.mp4"); self.assertEqual(first.status_code, 200)
        self.assertEqual(self.put_video(d, "same-again.mp4").status_code, 409)                                 # same file twice

    def test_video_link_and_caption_from_specs(self):
        r = self.cl.post(f"/product/{self.pid}/video/link", data={"url": "https://youtu.be/abc", "title": "رابط"}); self.assertEqual(r.status_code, 302)
        self.assertIn("https://youtu.be/abc", self.get(f"/product/{self.pid}?tab=video"))
        self.cl.post(f"/product/{self.pid}/video/link", data={"url": "javascript:alert(1)"})
        self.assertEqual(scalar("SELECT COUNT(*) FROM product_videos WHERE url LIKE 'javascript%'"), 0)
        cap = self.cl.get(f"/product/{self.pid}/video/caption?model_id={self.mids[0]}").get_json()
        self.assertIn("•", cap["caption"]); self.assertIn("#", cap["hashtags"])

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
    def test_real_video_gets_duration_size_and_thumbnail(self):
        d = tempfile.mkdtemp(); p = os.path.join(d, "t.mp4")
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x568:rate=10", "-pix_fmt", "yuv420p", p], capture_output=True, check=True)
        r = self.put_video(open(p, "rb").read(), "real.mp4", cut=20000, title="vertical"); self.assertEqual(r.status_code, 200)
        row = q("SELECT * FROM product_videos WHERE id=?", r.get_json()["id"])[0]
        self.assertEqual((row["width"], row["height"]), (320, 568)); self.assertGreater(row["duration"], 1.5)
        th = self.cl.get(f"/video/{row['id']}/thumb"); self.assertEqual(th.status_code, 200); self.assertTrue(th.data[:2] == b"\xff\xd8")
        self.assertIn("عمودي", self.get(f"/product/{self.pid}?tab=video"))                                   # vertical = right shape for Reels/TikTok

    def test_videos_survive_backup_and_restore(self):
        import dataio
        r = self.put_video(fake_mp4(2500), "bk.mp4", title="backup me", status="ready"); self.assertEqual(r.status_code, 200)
        c = db.conn(); before = dataio.read_table(c, "product_videos"); z = dataio.dump(c)
        dataio.restore(c, z); c.commit(); after = dataio.read_table(c, "product_videos"); c.close()
        self.assertTrue(any(v["title"] == "backup me" for v in before)); self.assertEqual(before, after)

    # ---- integration
    def test_pages_lead_into_the_workspace(self):
        self.assertIn(f'href="/product/{self.pid}"', self.get("/products")); self.assertIn(f"/compare?product={self.pid}", self.get("/products"))
        m = self.get(f"/model/{self.mids[0]}")
        self.assertIn(f'href="/product/{self.pid}"', m); self.assertIn(f"/compare?ids={self.mids[0]}", m); self.assertIn("tab=video", m)
        self.assertIn('id="go-cmp"', self.get("/search?status=all")); self.assertIn("/compare?ids=", self.get("/models"))


if __name__ == "__main__":
    unittest.main()
