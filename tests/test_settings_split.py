"""
25단계: 설정 파일 분리 테스트 (기본값 settings.default.json + 현장 값 settings.json).

- 현장 파일이 없으면 기본값 파일을 복사해서 만든다
- 현장 파일에 없는 새 키는 기본값으로 채운다 (깊은 병합). 현장 값은 그대로
- 저장은 현장 파일에만 한다 (기본값 파일은 바뀌지 않는다)
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import src.config
from src.config import deep_merge_defaults, load_settings, save_settings

DEFAULTS = {
    "camera": {"width": 1920, "undistort_k1": 0.0},
    "tracking": {"stop_duration": 1.0, "lost_duration": 2.0},
    "ball": {"diameter_mm": 42.67},
    "detection": {"profiles": {"practice": {"hsv_lower": [0, 0, 180]}}},
}


class SettingsSplitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.site = self.tmp / "config" / "settings.json"
        self.default = self.tmp / "bundle" / "settings.default.json"
        self.default.parent.mkdir(parents=True)
        self.default.write_text(json.dumps(DEFAULTS), encoding="utf-8")
        self.patch = mock.patch.multiple(src.config, SETTINGS_PATH=self.site, DEFAULT_SETTINGS_PATH=self.default)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_site(self, data):
        self.site.parent.mkdir(parents=True, exist_ok=True)
        self.site.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def read_site(self):
        return json.loads(self.site.read_text(encoding="utf-8"))

    def test_missing_site_file_is_created_from_default(self):
        self.assertFalse(self.site.exists())
        settings = load_settings()
        self.assertTrue(self.site.exists())
        self.assertEqual(self.read_site(), DEFAULTS)
        self.assertEqual(settings["ball"]["diameter_mm"], 42.67)

    def test_new_keys_filled_from_default_and_site_values_kept(self):
        self.write_site({
            "camera": {"undistort_k1": -0.5},  # 현장에서 맞춘 값
            "tracking": {"stop_duration": 0.8},
            "calibration": {"corners": [[1, 2], [3, 4], [5, 6], [7, 8]]},
            "detection": {"profiles": {"practice": {"hsv_lower": [0, 0, 200]}}},
        })
        settings = load_settings()
        # 현장 값은 그대로
        self.assertEqual(settings["camera"]["undistort_k1"], -0.5)
        self.assertEqual(settings["tracking"]["stop_duration"], 0.8)
        self.assertEqual(settings["calibration"]["corners"], [[1, 2], [3, 4], [5, 6], [7, 8]])
        self.assertEqual(settings["detection"]["profiles"]["practice"]["hsv_lower"], [0, 0, 200])  # 목록은 섞지 않음
        # 현장 파일에 없던 키는 기본값으로 채워짐 (안쪽 키까지)
        self.assertEqual(settings["camera"]["width"], 1920)
        self.assertEqual(settings["tracking"]["lost_duration"], 2.0)
        self.assertEqual(settings["ball"], {"diameter_mm": 42.67})
        # 읽기만 해서는 현장 파일이 바뀌지 않는다
        self.assertNotIn("ball", self.read_site())

    def test_save_writes_only_site_file(self):
        self.write_site({"camera": {"undistort_k1": -0.5}})
        settings = load_settings()
        settings["calibration"] = {"corners": [[9, 9]]}
        save_settings(settings)
        self.assertEqual(self.read_site()["calibration"], {"corners": [[9, 9]]})
        self.assertEqual(self.read_site()["camera"]["undistort_k1"], -0.5)
        self.assertEqual(json.loads(self.default.read_text(encoding="utf-8")), DEFAULTS)  # 기본값 파일 그대로

    def test_null_site_value_is_kept(self):
        # 일부러 비워 둔 값(null)은 기본값으로 덮지 않는다
        merged = deep_merge_defaults({"tracking": {"stop_duration": None}}, DEFAULTS)
        self.assertIsNone(merged["tracking"]["stop_duration"])

    def test_missing_default_file_still_works(self):
        self.default.unlink()
        self.write_site({"camera": {"width": 640}})
        self.assertEqual(load_settings(), {"camera": {"width": 640}})

    def test_repository_default_file_has_no_site_values(self):
        # 저장소의 기본값 파일에 현장 값(모서리·행렬·고정 물체)이 들어가 있으면 안 된다
        path = Path(__file__).resolve().parent.parent / "config" / "settings.default.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        calibration = data.get("calibration", {})
        for key in ("corners", "perspective_matrix", "frame_size", "undistort_k1"):
            self.assertNotIn(key, calibration)
        self.assertNotIn("static_objects", data)
        self.assertIn("ball", data)
        self.assertIn("hole", data)


if __name__ == "__main__":
    unittest.main()
