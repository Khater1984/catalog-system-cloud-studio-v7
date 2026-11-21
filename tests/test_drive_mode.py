"""Drive-backed storage logic with an in-memory fake of the Google Drive calls (the real Google API is NOT exercised here)."""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import os, io, shutil, unittest
from common import *
import storage, gsync, pdfplumber


class DriveModeTests(unittest.TestCase):
    def setUp(self):
        reset(); self.store = {}; self.n = 0
        def up(kind, name, data, mime): self.n += 1; fid = f"fake{self.n}"; self.store[fid] = bytes(data); return fid
        def down(fid, dest):
            if fid not in self.store:                   # the real Drive API raises HttpError(404) for a deleted file; nothing is written
                class _Resp: status = 404
                class _Gone(Exception): resp = _Resp()
                raise _Gone(fid)
            data = self.store[fid]
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as fh: fh.write(data)
        self._saved = (storage.IS_DRIVE, storage._upload, storage._download, storage.delete)
        storage.IS_DRIVE, storage._upload, storage._download, storage.delete = True, up, down, lambda fid: None
        self.cl = client_for("alice"); self.d = os.path.join(TMP, "dm"); os.makedirs(self.d, exist_ok=True)
        make_image(self.d + "/i.jpg", (90, 120, 200))
        make_pdf(self.d + "/c.pdf", [dict(models={"GQ40": {"Motor power": "3 kW", "Power supply": "380V 50Hz"}}, image=self.d + "/i.jpg")])

    def tearDown(self):
        storage.IS_DRIVE, storage._upload, storage._download, storage.delete = self._saved

    def wipe_cache(self):
        for d in (db.CACHE_DIR, db.IMG_DIR, db.UPLOAD_DIR):
            shutil.rmtree(d, ignore_errors=True); os.makedirs(d, exist_ok=True)

    def test_files_live_in_drive_and_survive_a_restart(self):
        upload(self.cl, self.d + "/c.pdf", "S"); self.assertEqual(scalar("SELECT status FROM catalogs"), "done")
        img = q("SELECT file, drive_id FROM images")[0]; cat = q("SELECT drive_id, file_sha FROM catalogs")[0]
        self.assertTrue(img["drive_id"] and cat["drive_id"])
        self.assertEqual(os.listdir(db.IMG_DIR), []); self.assertEqual(os.listdir(db.UPLOAD_DIR), [])      # nothing is kept on the (ephemeral) local disk
        mid = scalar("SELECT id FROM models"); self.cl.post("/models/bulk", data={"action": "approve", "with_images": "1", "ids": [str(mid)]})
        self.wipe_cache()                                                                                   # "the Cloud Run instance was replaced"
        r = self.cl.get("/img/" + img["file"]); self.assertEqual(r.status_code, 200); self.assertEqual(r.data, self.store[img["drive_id"]])
        self.wipe_cache()
        t = pdfplumber.open(io.BytesIO(self.cl.get(f"/datasheet/{mid}.pdf").data)); self.assertEqual(len(t.pages[0].images), 1)       # export re-fetches the image
        self.wipe_cache()
        self.assertEqual(self.cl.get(f"/catalog/{scalar('SELECT id FROM catalogs')}/page/1.png").status_code, 200)                    # source page from the stored PDF
        self.wipe_cache()
        self.cl.post(f"/catalog/{scalar('SELECT id FROM catalogs')}/retry")                                                            # not allowed (done) - nothing breaks
        self.assertEqual(scalar("SELECT status FROM catalogs"), "done")
        self.cl.post("/models/bulk", data={"action": "unapprove", "ids": [str(mid)]})
        c = db.conn(); c.execute("UPDATE catalogs SET status='error'"); c.commit(); c.close(); self.wipe_cache()
        self.cl.post(f"/catalog/{scalar('SELECT id FROM catalogs')}/retry"); self.assertEqual(scalar("SELECT status FROM catalogs"), "done")  # re-extraction from Drive copy

    def test_sheet_uses_drive_ids_and_canonical_names(self):
        upload(self.cl, self.d + "/c.pdf", "S")
        mid = scalar("SELECT id FROM models"); self.cl.post("/models/bulk", data={"action": "approve", "with_images": "1", "ids": [str(mid)]})
        c = db.conn(); c.execute("UPDATE specs SET attr='alimentazione' WHERE attr='Power supply'"); c.commit(); c.close()
        self.cl.post("/dictionary/suggest")                                                                  # the dictionary only renames after the user accepts the suggestions
        c = db.conn()
        models, specs, imgs, fams, prods = gsync.build_data(c); c.close()
        self.assertEqual(imgs[0]["file"], q("SELECT file FROM images")[0]["file"])
        self.assertIn("Power supply", {s[2] for s in specs})                                                 # supplier's Italian label shown with the standard name

    def test_missing_file_in_drive_does_not_break_exports(self):
        upload(self.cl, self.d + "/c.pdf", "S"); mid = scalar("SELECT id FROM models"); self.cl.post("/models/bulk", data={"action": "approve", "with_images": "1", "ids": [str(mid)]})
        self.store.clear(); self.wipe_cache()
        r = self.cl.get(f"/datasheet/{mid}.pdf"); self.assertEqual(r.status_code, 200)                       # document still produced (without the image)
        self.assertEqual(self.cl.get("/img/" + q("SELECT file FROM images")[0]["file"]).status_code, 404)


if __name__ == "__main__":
    unittest.main()
