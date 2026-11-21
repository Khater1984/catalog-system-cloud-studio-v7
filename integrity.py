"""Consistency checks that go beyond foreign keys."""
import db

CHECKS = [
    ("MODEL_FAMILY_MISMATCH", "طرازات يختلف تصنيفها عن تصنيف منتجاتها",
     "SELECT m.id FROM models m JOIN products p ON p.id=m.product_id WHERE p.family_id IS NOT NULL AND (m.family_id IS NULL OR m.family_id<>p.family_id)"),
    ("MODEL_WITHOUT_CATALOG", "طرازات غير مرتبطة بكتالوج", "SELECT id FROM models WHERE catalog_id IS NULL"),
    ("APPROVED_NO_SPECS", "طرازات معتمدة دون مواصفات", "SELECT id FROM models WHERE status='approved' AND active=1 AND id NOT IN (SELECT model_id FROM specs)"),
    ("PRIMARY_NOT_LINKED", "صور رئيسية غير معتمدة للكتالوج",
     "SELECT id FROM image_links WHERE is_primary=1 AND ok_catalog=0"),
    ("MULTI_PRIMARY", "طرازات لها أكثر من صورة رئيسية",
     "SELECT model_id id FROM image_links WHERE is_primary=1 GROUP BY model_id HAVING COUNT(*)>1"),
    ("ACTIVE_PRODUCT_NO_MODELS", "منتجات نشطة دون طرازات",
     "SELECT id FROM products WHERE active=1 AND id NOT IN (SELECT product_id FROM models WHERE product_id IS NOT NULL)"),
    ("IMAGE_NO_FILE_ID", "صور دون معرّف Drive (في وضع تخزين Drive فقط)", None),
]


def run(c):
    import storage
    out = []
    for kind, label, sql in CHECKS:
        if sql is None:
            if not storage.IS_DRIVE:
                continue
            sql = "SELECT id FROM images WHERE drive_id IS NULL OR drive_id=''"
        ids = [r["id"] for r in c.execute(sql).fetchall()]
        if ids:
            out.append(dict(kind=kind, label=label, count=len(ids), sample=ids[:10]))
    return out


def fix_family(c):
    """Models that belong to a product take the product's family."""
    n = c.execute("""SELECT COUNT(*) n FROM models m JOIN products p ON p.id=m.product_id
                     WHERE p.family_id IS NOT NULL AND (m.family_id IS NULL OR m.family_id<>p.family_id)""").fetchone()["n"]
    c.execute("""UPDATE models SET family_id=(SELECT p.family_id FROM products p WHERE p.id=models.product_id)
                 WHERE product_id IS NOT NULL AND EXISTS (SELECT 1 FROM products p WHERE p.id=models.product_id AND p.family_id IS NOT NULL
                       AND (models.family_id IS NULL OR models.family_id<>p.family_id))""")
    if n:
        db.log(c, "system", 0, "fix_family", f"{n} models aligned to their product's family")
    return n
