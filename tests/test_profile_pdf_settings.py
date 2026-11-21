import os
import sys
import tempfile
import unittest
from pathlib import Path

_test_data = tempfile.TemporaryDirectory(prefix="catalog-profile-tests-")
os.environ.setdefault("DATA_DIR", os.path.join(_test_data.name, "data"))
os.environ.setdefault("DATABASE_URL", "")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import profiles
import pdf_engine


class ProfileNumericSettingsTests(unittest.TestCase):
    def test_accepts_boundary_values_including_zero_opacity(self):
        settings = profiles.validate_settings({
            "font_size_base": "6",
            "bg_opacity": "0",
            "wm_opacity": "1",
            "wm_angle": "-360",
            "bleed_mm": "20",
        })
        self.assertEqual(settings["bg_opacity"], "0")
        self.assertEqual(settings["font_size_base"], "6")
        self.assertEqual(settings["bleed_mm"], "20")

    def test_rejects_invalid_or_out_of_range_values(self):
        for key, value in (("bg_opacity", "nan"), ("wm_opacity", "1.1"),
                           ("font_size_base", "5"), ("bleed_mm", "-1"),
                           ("wm_angle", "north")):
            with self.subTest(key=key, value=value):
                with self.assertRaises(ValueError):
                    profiles.validate_settings({key: value})

    def test_pdf_opacity_preserves_zero_and_handles_bad_legacy_values(self):
        self.assertEqual(pdf_engine._opacity("0"), 0.0)
        self.assertEqual(pdf_engine._opacity("0.5"), 0.5)
        self.assertEqual(pdf_engine._opacity("bad", 0.12), 0.12)
        self.assertEqual(pdf_engine._opacity("2"), 1.0)


if __name__ == "__main__":
    unittest.main()
