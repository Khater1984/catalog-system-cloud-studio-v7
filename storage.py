"""File storage for catalog PDFs and extracted images.

STORAGE_BACKEND=local  (default)  files live under data/ (development / tests / the old single-PC mode)
STORAGE_BACKEND=drive             files live in Google Drive (folder "Catalog System"); a local cache directory is only a cache
                                  and can be wiped at any time (Cloud Run instances are stateless).
The database remembers the Drive file id: images.drive_id and catalogs.drive_id.
"""
import os, io, time, threading, re, hashlib, shutil
import db

BACKEND = os.environ.get("STORAGE_BACKEND", "local").strip().lower()
IS_DRIVE = BACKEND == "drive"
_tl = threading.local()
_folder_lock = threading.Lock()


def _service():
    if getattr(_tl, "svc", None) is None:
        from googleapiclient.discovery import build
        import gauth
        _tl.svc = build("drive", "v3", credentials=gauth.credentials(), cache_discovery=False)
    return _tl.svc


def _retry(fn, tries=5):
    from googleapiclient.errors import HttpError
    for i in range(tries):
        try:
            return fn()
        except HttpError as e:
            status = getattr(e.resp, "status", 0)
            if status in (429, 500, 502, 503, 504) and i < tries - 1:
                time.sleep(min(2 ** i, 16)); continue
            raise
        except (ConnectionError, TimeoutError, OSError):
            if i < tries - 1:
                time.sleep(min(2 ** i, 16)); continue
            raise


def _find_or_create_folder(name, parent):
    svc = _service()
    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false" + (f" and '{parent}' in parents" if parent else " and 'root' in parents")
    res = _retry(lambda: svc.files().list(q=q, fields="files(id)", supportsAllDrives=True, includeItemsFromAllDrives=True, pageSize=1).execute())
    if res.get("files"):
        return res["files"][0]["id"]
    body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent:
        body["parents"] = [parent]
    return _retry(lambda: svc.files().create(body=body, fields="id", supportsAllDrives=True).execute())["id"]


def folder(kind):
    """kind: 'images' | 'catalogs'.  Folder ids are remembered in the settings table."""
    with _folder_lock:
        c = db.conn()
        try:
            fid = db.get_setting(c, f"drive_folder_{kind}")
            if fid:
                return fid
            root = os.environ.get("DRIVE_ROOT_FOLDER_ID") or db.get_setting(c, "drive_root")
            if not root:
                root = _find_or_create_folder("Catalog System", None)
                db.set_setting(c, "drive_root", root)
            fid = _find_or_create_folder(kind, root)
            db.set_setting(c, f"drive_folder_{kind}", fid); c.commit()
            return fid
        finally:
            c.close()


def _upload(kind, name, data, mime):
    from googleapiclient.http import MediaIoBaseUpload
    svc = _service(); parent = folder(kind)
    return _retry(lambda: svc.files().create(
        body={"name": name, "parents": [parent]},
        media_body=MediaIoBaseUpload(io.BytesIO(data), mimetype=mime, resumable=len(data) > 5 * 1024 * 1024),
        fields="id", supportsAllDrives=True).execute())["id"]


def _download(file_id, dest):
    from googleapiclient.http import MediaIoBaseDownload
    svc = _service()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + f".{threading.get_ident()}.tmp"

    def go():
        with open(tmp, "wb") as f:
            dl = MediaIoBaseDownload(f, svc.files().get_media(fileId=file_id, supportsAllDrives=True), chunksize=8 * 1024 * 1024)
            done = False
            while not done:
                _, done = dl.next_chunk()
    try:
        _retry(go)
        os.replace(tmp, dest)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def delete(drive_id):
    if IS_DRIVE and drive_id:
        try:
            _retry(lambda: _service().files().update(fileId=drive_id, body={"trashed": True}, supportsAllDrives=True).execute())
        except Exception:
            pass


def exists_in_drive(drive_id):
    try:
        _retry(lambda: _service().files().get(fileId=drive_id, fields="id,trashed", supportsAllDrives=True).execute())
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ images
def _img_cache(fname):
    d = os.path.join(db.CACHE_DIR, "images"); os.makedirs(d, exist_ok=True)
    return os.path.join(d, fname)


def save_image(fname, data):
    """Persist an extracted image. Returns the Drive file id (None in local mode)."""
    mime = "image/png" if fname.endswith(".png") else "image/jpeg"
    if not IS_DRIVE:
        with open(os.path.join(db.IMG_DIR, fname), "wb") as f:
            f.write(data)
        return None
    did = _upload("images", fname, data, mime)
    with open(_img_cache(fname), "wb") as f:
        f.write(data)
    return did


def image_path(fname):
    """Local path of an image file (downloads it from Drive into the cache when needed)."""
    if not IS_DRIVE:
        return os.path.join(db.IMG_DIR, fname)
    p = _img_cache(fname)
    if os.path.exists(p):
        return p
    c = db.conn()
    try:
        r = c.execute("SELECT drive_id FROM images WHERE file=? AND drive_id IS NOT NULL LIMIT 1", (fname,)).fetchone()
    finally:
        c.close()
    if not r:
        raise FileNotFoundError(fname)
    try:
        _download(r["drive_id"], p)
    except Exception as exc:
        # A Drive file removed outside the app is a missing image, not a failed
        # catalog export. Preserve other API/network errors so they remain visible.
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status in (404, 410):
            raise FileNotFoundError(fname) from exc
        raise
    return p


# ------------------------------------------------------------------ original catalog PDFs
def _pdf_cache(sha):
    d = os.path.join(db.CACHE_DIR, "catalogs"); os.makedirs(d, exist_ok=True)
    return os.path.join(d, sha[:16] + ".pdf")


def save_catalog_pdf(sha, filename, data):
    """Returns the Drive file id (None in local mode)."""
    if not IS_DRIVE:
        with open(os.path.join(db.UPLOAD_DIR, sha[:16] + ".pdf"), "wb") as f:
            f.write(data)
        return None
    with open(_pdf_cache(sha), "wb") as f:
        f.write(data)
    return _upload("catalogs", f"{sha[:12]}_{filename}", data, "application/pdf")


def catalog_path(sha, drive_id=None):
    if not IS_DRIVE:
        return os.path.join(db.UPLOAD_DIR, sha[:16] + ".pdf")
    p = _pdf_cache(sha)
    if not os.path.exists(p):
        if not drive_id:
            raise FileNotFoundError(sha)
        _download(drive_id, p)
    return p


def forget_catalog_file(sha, drive_id):
    for p in (_pdf_cache(sha), os.path.join(db.UPLOAD_DIR, sha[:16] + ".pdf")):
        try:
            os.remove(p)
        except OSError:
            pass
    delete(drive_id)


# ------------------------------------------------------------------ generated exports
def _safe_export_name(filename):
    name = os.path.basename(filename or "")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,180}", name):
        raise ValueError("Invalid export filename")
    return name


def _export_cache(filename):
    d = os.path.join(db.CACHE_DIR, "exports"); os.makedirs(d, exist_ok=True)
    key = hashlib.sha256(filename.encode("utf-8")).hexdigest()[:24]
    return os.path.join(d, key + ".pdf")


def save_export_pdf(filename, data):
    """Persist an export in the configured store; Drive mode survives Cloud Run restarts."""
    filename = _safe_export_name(filename)
    if not IS_DRIVE:
        os.makedirs(db.EXPORT_DIR, exist_ok=True)
        with open(os.path.join(db.EXPORT_DIR, filename), "wb") as f:
            f.write(data)
        return None
    p = _export_cache(filename)
    with open(p, "wb") as f:
        f.write(data)
    return _upload("exports", filename, data, "application/pdf")


def discard_export_pdf(filename, drive_id=None):
    """Remove a generated export and its disposable cache after a failed DB commit."""
    try:
        filename = _safe_export_name(filename)
    except ValueError:
        return
    if drive_id:
        delete(drive_id)
    for path in (_export_cache(filename), os.path.join(db.EXPORT_DIR, filename)):
        try:
            os.remove(path)
        except OSError:
            pass


def export_path(filename, drive_id=None):
    """Local path for a saved export; downloads the Drive copy into the disposable cache if needed."""
    filename = _safe_export_name(filename)
    if not IS_DRIVE:
        p = os.path.join(db.EXPORT_DIR, filename)
        if not os.path.isfile(p):
            raise FileNotFoundError(filename)
        return p
    p = _export_cache(filename)
    if not os.path.exists(p):
        if not drive_id:
            raise FileNotFoundError(filename)
        _download(drive_id, p)
    return p


# ------------------------------------------------------------------ marketing videos
def _safe_video_name(filename):
    name = os.path.basename(filename or "")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", name):
        raise ValueError("Invalid video filename")
    return name


def _video_cache(name):
    d = os.path.join(db.CACHE_DIR, "videos"); os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def save_video(filename, src_path, mime):
    """Store a video file from disk (streamed, resumable upload to Drive). Returns the Drive file id (None in local mode)."""
    name = _safe_video_name(filename)
    if not IS_DRIVE:
        os.makedirs(db.VIDEO_DIR, exist_ok=True)
        shutil.copyfile(src_path, os.path.join(db.VIDEO_DIR, name))
        return None
    from googleapiclient.http import MediaFileUpload
    svc = _service(); parent = folder("videos")
    did = _retry(lambda: svc.files().create(
        body={"name": name, "parents": [parent]},
        media_body=MediaFileUpload(src_path, mimetype=mime, resumable=True, chunksize=8 * 1024 * 1024),
        fields="id", supportsAllDrives=True).execute())["id"]
    shutil.copyfile(src_path, _video_cache(name))
    return did


def video_path(filename, drive_id=None):
    name = _safe_video_name(filename)
    if not IS_DRIVE:
        p = os.path.join(db.VIDEO_DIR, name)
        if not os.path.isfile(p):
            raise FileNotFoundError(name)
        return p
    p = _video_cache(name)
    if not os.path.exists(p):
        if not drive_id:
            raise FileNotFoundError(name)
        _download(drive_id, p)
    return p
