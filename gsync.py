"""One-way sync: the central database -> Google Sheet (reports / sharing). The database stays the master.
Images are already in Google Drive (storage.py); the sheet only carries their Drive ids."""
import os, mimetypes, datetime
import db, storage, gauth, dictionary

CRED = gauth.CRED
TABS = ["Models", "Specs", "Images", "Families", "Products"]


def build_data(c):
    """Pure data step (no Google calls): approved models only, hidden ones carry Visible=FALSE."""
    models = c.execute("""SELECT m.*, c.supplier, f.name fam, p.name pname FROM models m LEFT JOIN catalogs c ON c.id=m.catalog_id
                          LEFT JOIN families f ON f.id=m.family_id LEFT JOIN products p ON p.id=m.product_id
                          WHERE m.status='approved' AND m.active=1 ORDER BY m.code""").fetchall()
    amap = dictionary.load_map(c)
    specs, imgs = [], []
    for m in models:
        for s in dictionary.apply(c.execute("SELECT * FROM specs WHERE model_id=? ORDER BY sort,id", (m["id"],)).fetchall(), amap):
            specs.append([m["id"], m["code"], s["attr"], s["supplier_value"], s["company_value"],
                          s["company_value"] or s["supplier_value"], "TRUE" if s["hidden"] else "FALSE", s["rule"]])
        for l in c.execute("""SELECT l.*, i.file, i.w, i.h FROM image_links l JOIN images i ON i.id=l.image_id
                              WHERE l.model_id=? AND (l.ok_catalog=1 OR l.ok_sheet=1) ORDER BY l.is_primary DESC, l.id""", (m["id"],)):
            imgs.append(dict(image_id=l["image_id"], model_id=m["id"], file=l["file"], w=l["w"], h=l["h"],
                             primary=l["is_primary"], okc=l["ok_catalog"], oks=l["ok_sheet"]))
    fams = c.execute("SELECT id,name,parent_id FROM families ORDER BY name").fetchall()
    prods = c.execute("""SELECT p.*, f.name fam FROM products p LEFT JOIN families f ON f.id=p.family_id WHERE p.active=1 ORDER BY p.name""").fetchall()
    return models, specs, imgs, fams, prods


def tables(models, specs, imgs, fams, prods, drive_ids):
    prim = {}
    for i in imgs:
        if i["primary"] or i["model_id"] not in prim:
            prim[i["model_id"]] = drive_ids.get(i["image_id"], "")
    t = {}
    t["Models"] = [["ID", "Model", "Series", "Family", "Supplier", "Arabic name", "Visible", "Primary image (Drive ID)", "Product"]] + [
        [m["id"], m["code"], m["series"], m["fam"] or "", m["supplier"] or "", m["ar_name"], "TRUE" if m["visible"] else "FALSE",
         prim.get(m["id"], ""), m["pname"] or ""] for m in models]
    t["Specs"] = [["Model ID", "Model", "Attribute", "Supplier value", "Company value", "Final value", "Hidden", "Rule"]] + specs
    t["Images"] = [["Image ID", "Model ID", "Drive ID", "Primary", "OK catalog", "OK data sheet", "Width", "Height"]] + [
        [i["image_id"], i["model_id"], drive_ids.get(i["image_id"], ""), "TRUE" if i["primary"] else "FALSE",
         "TRUE" if i["okc"] else "FALSE", "TRUE" if i["oks"] else "FALSE", i["w"], i["h"]] for i in imgs]
    t["Families"] = [["ID", "Name", "Parent ID"]] + [[f["id"], f["name"], f["parent_id"] or ""] for f in fams]
    t["Products"] = [["ID", "Name", "Arabic name", "Description", "Family"]] + [
        [p["id"], p["name"], p["ar_name"], p["description"], p["fam"] or ""] for p in prods]
    return t


def _services():
    from googleapiclient.discovery import build
    creds = gauth.credentials()
    return build("sheets", "v4", credentials=creds, cache_discovery=False), build("drive", "v3", credentials=creds, cache_discovery=False)


def sync():
    c = db.conn()
    try:
        sheets, drive = _services()
        models, specs, imgs, fams, prods = build_data(c)
        up = 0
        if storage.IS_DRIVE:                         # images already live in Drive: just use their ids
            drive_ids = {r["id"]: r["drive_id"] for r in c.execute("SELECT id, drive_id FROM images WHERE drive_id IS NOT NULL").fetchall()}
            folder = db.get_setting(c, "drive_folder_images")
        else:                                        # local mode (old single-PC behaviour): upload images to a "Catalog Images" folder
            from googleapiclient.http import MediaFileUpload
            folder = db.get_setting(c, "drive_folder")
            if not folder:
                folder = drive.files().create(body={"name": "Catalog Images", "mimeType": "application/vnd.google-apps.folder"}, fields="id").execute()["id"]
                db.set_setting(c, "drive_folder", folder)
            drive_ids = {r["image_id"]: r["drive_id"] for r in c.execute("SELECT * FROM drive_files")}
            for i in imgs:
                if i["image_id"] in drive_ids:
                    continue
                path = os.path.join(db.IMG_DIR, i["file"])
                mt = mimetypes.guess_type(path)[0] or "application/octet-stream"
                f = drive.files().create(body={"name": i["file"], "parents": [folder]}, media_body=MediaFileUpload(path, mimetype=mt), fields="id").execute()
                drive_ids[i["image_id"]] = f["id"]; up += 1
                c.execute("INSERT INTO drive_files(image_id,drive_id) VALUES(?,?) ON CONFLICT(image_id) DO UPDATE SET drive_id=excluded.drive_id", (i["image_id"], f["id"])); c.commit()
        sid = db.get_setting(c, "sheet_id")
        if not sid:
            sid = sheets.spreadsheets().create(body={"properties": {"title": "Catalog DB"},
                  "sheets": [{"properties": {"title": t}} for t in TABS]}, fields="spreadsheetId").execute()["spreadsheetId"]
            db.set_setting(c, "sheet_id", sid)
        have = {s["properties"]["title"] for s in sheets.spreadsheets().get(spreadsheetId=sid).execute()["sheets"]}
        missing = [t for t in TABS if t not in have]
        if missing:
            sheets.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": [
                {"addSheet": {"properties": {"title": t}}} for t in missing]}).execute()
        data = tables(models, specs, imgs, fams, prods, drive_ids)
        for t in TABS:                                # write the new rows first, then clear what is left below them
            sheets.spreadsheets().values().update(spreadsheetId=sid, range=f"{t}!A1", valueInputOption="RAW", body={"values": data[t]}).execute()
            sheets.spreadsheets().values().clear(spreadsheetId=sid, range=f"{t}!A{len(data[t]) + 1}:Z").execute()
        db.set_setting(c, "last_sync", datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
        db.log(c, "sync", 0, "sheets_sync", f"{len(models)} models, {len(specs)} specs, {len(imgs)} images")
        c.commit()
        return dict(models=len(models), specs=len(specs), images=len(imgs), uploaded=up, sheet=sid, folder=folder)
    finally:
        c.close()
