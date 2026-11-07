"""
22단계: 처리 조건(처리 프레임 크기·왜곡 보정 k1)이 바뀌면 저장된 캘리브레이션을 무효로 보는지 테스트.

가짜 카메라 영상은 1280x720이고, 테스트 설정은 process_scale 1.0 / k1 0.0이라
처리 프레임 크기도 1280x720이다.
"""

import json
import shutil
import tempfile
import unittest

import cv2
import numpy as np

import src.config
from src.calibration import Calibration
from src.engine import CALIBRATION_STALE_MESSAGE, VisionEngine
from tests.base import qt_app  # noqa: F401  (Qt 애플리케이션 준비)
from tests.fake_camera import MAT_CORNERS, FakeCamera, make_test_settings


def saved_calibration(**stamp) -> dict:
    """MAT_CORNERS로 만든 캘리브레이션 설정 (stamp로 frame_size·undistort_k1을 붙인다)."""
    src_pts = np.array(MAT_CORNERS, np.float32)
    dst_pts = np.array([[0, 0], [4500, 0], [4500, 2000], [0, 2000]], np.float32)
    matrix = cv2.getPerspectiveTransform(src_pts, dst_pts)
    calibration = {
        "mat_width_mm": 4500,
        "mat_height_mm": 2000,
        "filter_outside_mat": True,
        "corners": [list(c) for c in MAT_CORNERS],
        "perspective_matrix": matrix.tolist(),
    }
    calibration.update(stamp)
    return calibration


class StaleCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.patch = None
        self.engine = None

    def tearDown(self):
        if self.engine is not None:
            self.engine.shutdown()
        if self.patch is not None:
            self.patch.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def start_engine(self, calibration: dict, camera=None) -> VisionEngine:
        self.patch = make_test_settings(self.tmp_dir, calibration=calibration)
        self.patch.start()
        self.engine = VisionEngine(camera=camera or FakeCamera())
        self.engine.camera_retry_interval = 0
        self.invalidated = []
        self.engine.calibration_invalidated.connect(self.invalidated.append)
        return self.engine

    def step(self):
        self.engine.process_frame(now=100.0)

    def saved_file(self) -> dict:
        return json.loads(src.config.SETTINGS_PATH.read_text(encoding="utf-8"))

    def test_old_calibration_without_stamp_is_invalid(self):
        """21단계까지의 설정 파일(크기·k1 기록 없음, 예: 1280x720 시절 값)은 자동으로 무효."""
        engine = self.start_engine(saved_calibration())
        self.assertFalse(engine.calibration.is_calibrated())
        self.assertEqual(engine.calibration_warning, CALIBRATION_STALE_MESSAGE)
        self.step()
        self.assertFalse(engine.calibration.is_calibrated())
        self.assertEqual(engine.align_saved(), "not_calibrated")

    def test_matching_stamp_stays_calibrated(self):
        engine = self.start_engine(saved_calibration(frame_size=[1280, 720], undistort_k1=0.0))
        self.assertTrue(engine.calibration.is_calibrated())
        self.step()
        self.assertTrue(engine.calibration.is_calibrated())
        self.assertIsNone(engine.calibration_warning)
        self.assertEqual(self.invalidated, [])

    def test_different_frame_size_is_invalid_after_first_frame(self):
        """크기는 첫 프레임을 받아야 알 수 있으므로, 첫 프레임에서 무효로 바뀐다."""
        engine = self.start_engine(saved_calibration(frame_size=[1920, 1080], undistort_k1=0.0))
        self.assertTrue(engine.calibration.is_calibrated())  # 아직 크기를 모름 (k1은 같음)
        self.step()
        self.assertFalse(engine.calibration.is_calibrated())
        self.assertEqual(self.invalidated, [CALIBRATION_STALE_MESSAGE])

    def test_different_k1_is_invalid_at_start(self):
        engine = self.start_engine(saved_calibration(frame_size=[1280, 720], undistort_k1=-0.1))
        self.assertFalse(engine.calibration.is_calibrated())

    def test_k1_change_invalidates_and_revert_restores(self):
        """슬라이더로 k1을 바꾸면 무효, 원래 값으로 되돌리면 저장된 캘리브레이션이 다시 살아난다."""
        engine = self.start_engine(saved_calibration(frame_size=[1280, 720], undistort_k1=0.0))
        self.step()
        engine.set_undistort_k1(-0.1)
        self.step()
        self.assertFalse(engine.calibration.is_calibrated())
        self.assertEqual(len(self.invalidated), 1)

        engine.set_undistort_k1(-0.2)  # 이미 무효인 동안에는 다시 알리지 않는다
        self.step()
        self.assertEqual(len(self.invalidated), 1)

        engine.set_undistort_k1(0.0)
        self.step()
        self.assertTrue(engine.calibration.is_calibrated())
        self.assertIsNone(engine.calibration_warning)

    def test_new_calibration_saves_stamp_and_clears_warning(self):
        engine = self.start_engine(saved_calibration())  # 무효인 예전 값으로 시작
        engine.set_undistort_k1(-0.05)
        self.step()
        self.assertEqual(engine.align_auto(), "OK")  # 녹색 매트 자동 검출로 다시 캘리브레이션
        self.assertTrue(engine.calibration.is_calibrated())
        self.assertIsNone(engine.calibration_warning)

        saved = self.saved_file()["calibration"]
        self.assertEqual(saved["frame_size"], [1280, 720])
        self.assertAlmostEqual(saved["undistort_k1"], -0.05)
        # 새로 불러와도 같은 조건이면 그대로 쓸 수 있다
        self.assertTrue(Calibration().matches_processing((1280, 720), -0.05))

    def test_hello_reports_frame_size(self):
        engine = self.start_engine(saved_calibration(frame_size=[1280, 720], undistort_k1=0.0))
        hello = []
        engine.protocol_handler._send_to = lambda client, message: hello.append(message)
        engine.protocol_handler.on_client_connected("A")
        self.assertIsNone(hello[0]["frame_w"])  # 첫 프레임 전
        self.step()
        hello.clear()
        engine.protocol_handler.on_client_connected("A")
        self.assertEqual((hello[0]["frame_w"], hello[0]["frame_h"]), (1280, 720))
        self.assertEqual(hello[0]["version"], "2.5")


class ScaledEngineTests(unittest.TestCase):
    """process_scale을 쓰면 검출 좌표가 축소된 프레임 기준으로 나오는지."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.patch = make_test_settings(self.tmp_dir, camera={"process_scale": 0.5})
        self.patch.start()
        self.engine = VisionEngine(camera=FakeCamera(ball=(500, 400)))

    def tearDown(self):
        self.engine.shutdown()
        self.patch.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_detection_in_scaled_coordinates(self):
        # 반지름 18px 공이 0.5배로 줄면 면적 약 254px² -> min_area 200은 통과
        self.engine.process_frame(now=1.0)
        self.assertEqual(self.engine.frame_size, (640, 360))
        detection = self.engine.last_detection
        self.assertIsNotNone(detection)
        self.assertAlmostEqual(detection.x, 250, delta=2)
        self.assertAlmostEqual(detection.y, 200, delta=2)
        self.assertGreater(detection.area, 150)

    def test_debug_candidates(self):
        self.engine.set_detection_debug(True)
        frames = []
        self.engine.frame_processed.connect(frames.append)
        self.engine.process_frame(now=1.0)
        candidates = frames[-1].candidates
        self.assertTrue(candidates)
        self.assertTrue(candidates[0].accepted)
        self.assertAlmostEqual(candidates[0].diameter, 18, delta=3)


if __name__ == "__main__":
    unittest.main()
