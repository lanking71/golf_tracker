"""
25단계: mm 기준 공 크기·이동 기준 테스트.

- 캘리브레이션이 있으면: 공(예상 면적의 약 1배)은 통과, 컵(약 2배)은 크기로 걸러진다
- 캘리브레이션이 없으면: 예전처럼 프로필의 min_area/max_area(픽셀)로 판정
- 이동 기준(추적 시작·궤적 기록·점프)은 캘리브레이션이 있으면 mm, 없으면 px
"""

import unittest

from src.calibration import Calibration
from src.detector import REJECT_SIZE, BallDetector
from src.tracker import Tracker
from tests.base import EngineTestCase
from tests.fake_camera import MAT_CORNERS, FakeCamera

PROFILE = {
    "active_profile": "test",
    "profiles": {
        "test": {
            "name": "테스트 공",
            "hsv_lower": [140, 80, 80],
            "hsv_upper": [170, 255, 255],
            "min_area": 200,
            "max_area": 8000,
            "min_circularity": 0.7,
        }
    },
}
BALL = {"diameter_mm": 165, "area_ratio_min": 0.6, "area_ratio_max": 1.5}


def fake_frame(ball=(500, 400), cups=()):
    camera = FakeCamera(ball=ball)
    camera.cups = list(cups)
    camera.open()
    return camera.read_frame()


def fake_calibration():
    calibration = Calibration({"mat_width_mm": 4500, "mat_height_mm": 2000})
    calibration.set_corners(MAT_CORNERS)
    return calibration


class DetectorSizeTests(unittest.TestCase):
    def setUp(self):
        self.detector = BallDetector(PROFILE, BALL)
        self.calibration = fake_calibration()

        def expected(x, y):
            return self.calibration.expected_ball_area_px((x, y), BALL["diameter_mm"])

        self.expected = expected

    def test_ball_passes_and_cup_is_filtered_by_size(self):
        frame = fake_frame(ball=(500, 400), cups=[(800, 400)])
        self.detector.debug_enabled = True
        # 컵 쪽을 더 선호하라고 해도(prefer) 컵은 크기 판정에서 빠지므로 공이 뽑힌다
        found = self.detector.detect(frame, prefer=(800, 400), expected_area=self.expected)
        self.assertIsNotNone(found)
        self.assertAlmostEqual(found.x, 500, delta=2)
        by_x = {round(c.x, -2): c for c in self.detector.last_candidates}
        ball, cup = by_x[500], by_x[800]
        self.assertTrue(ball.accepted)
        self.assertAlmostEqual(ball.area_ratio, 1.0, delta=0.25)  # 공은 예상 대비 약 x1.0
        self.assertFalse(cup.accepted)
        self.assertEqual(cup.reject, REJECT_SIZE)
        self.assertGreater(cup.area_ratio, 1.5)  # 컵은 약 x2
        self.assertEqual(self.detector.last_size_rejected, 1)

    def test_cup_only_scene_finds_nothing(self):
        frame = fake_frame(ball=None, cups=[(800, 400)])
        self.assertIsNone(self.detector.detect(frame, expected_area=self.expected))

    def test_without_calibration_pixel_limits_are_used(self):
        # 캘리브레이션이 없으면 min_area 200 ~ max_area 8000(px²) -> 컵(약 2100px²)도 통과한다
        frame = fake_frame(ball=None, cups=[(800, 400)])
        found = self.detector.detect(frame)
        self.assertIsNotNone(found)
        self.assertAlmostEqual(found.x, 800, delta=2)
        self.detector.debug_enabled = True
        self.detector.detect(frame)
        self.assertIsNone(self.detector.last_candidates[0].area_ratio)  # 비율 없음 (픽셀 기준)

    def test_expected_area_follows_perspective(self):
        # 원근 때문에 화면 아래쪽(카메라에 가까운 쪽)이 더 크게 보인다
        top = self.calibration.expected_ball_area_px((500, 200), 42.67)
        bottom = self.calibration.expected_ball_area_px((500, 580), 42.67)
        self.assertGreater(bottom, top)
        self.assertIsNone(Calibration({}).expected_ball_area_px((500, 400), 42.67))


class TrackerMmThresholdTests(unittest.TestCase):
    SETTINGS = {
        "arm_move_distance": 8,
        "min_move_distance": 6,
        "max_jump_distance": 200,
        "arm_move_distance_mm": 20,
        "min_move_distance_mm": 10,
        "max_jump_distance_mm": 500,
    }

    def test_pixel_values_without_calibration(self):
        tracker = Tracker(self.SETTINGS, {})
        self.assertEqual(tracker.arm_move_threshold((0, 0)), 8)
        tracker.mm_to_px = lambda point, mm: None  # 캘리브레이션 없음
        self.assertEqual(tracker.min_move_threshold((0, 0)), 6)
        self.assertEqual(tracker.max_jump_threshold((0, 0)), 200)

    def test_mm_values_with_calibration(self):
        tracker = Tracker(self.SETTINGS, {})
        tracker.mm_to_px = lambda point, mm: mm * 0.4  # 1mm = 0.4px 인 현장
        self.assertAlmostEqual(tracker.arm_move_threshold((0, 0)), 8.0)
        self.assertAlmostEqual(tracker.min_move_threshold((0, 0)), 4.0)
        self.assertAlmostEqual(tracker.max_jump_threshold((0, 0)), 200.0)

    def test_mm_disabled_with_null(self):
        settings = dict(self.SETTINGS, arm_move_distance_mm=None)
        tracker = Tracker(settings, {})
        tracker.mm_to_px = lambda point, mm: mm * 0.4
        self.assertEqual(tracker.arm_move_threshold((0, 0)), 8)

    def test_arm_uses_mm_threshold(self):
        tracker = Tracker(self.SETTINGS, {})
        tracker.mm_to_px = lambda point, mm: mm * 1.0  # 20mm = 20px
        tracker.register_start_position(100, 100)
        tracker.start_tracking()
        tracker.update_detection(112, 100, 1.0)  # 12px: px 기준(8)이면 시작했겠지만 mm 기준(20px)은 아님
        self.assertEqual(tracker.state.name, "ARMED")
        tracker.update_detection(125, 100, 1.1)
        self.assertEqual(tracker.state.name, "TRACKING")


class EngineMmTests(EngineTestCase):
    settings_overrides = {
        "tracking": {
            "min_move_distance": 6,
            "max_jump_distance": 200,
            "arm_move_distance": 8,
            "arm_move_distance_mm": 100,
            "min_move_distance_mm": 30,
            "max_jump_distance_mm": 1000,
            "max_consecutive_jumps": 10,
            "stop_threshold": 3,
            "stop_duration": 0.5,
            "lost_duration": 2.0,
        }
    }

    def test_thresholds_switch_with_calibration(self):
        self.step()
        start = (500, 400)
        self.assertEqual(self.engine.tracker.arm_move_threshold(start), 8)  # 캘리브레이션 전: px
        self.assertEqual(self.engine.align_corners([list(c) for c in MAT_CORNERS]), "OK")
        expected = self.engine.calibration.mm_radius_to_px(start, 100)
        self.assertAlmostEqual(self.engine.tracker.arm_move_threshold(start), expected)
        self.assertGreater(expected, 8)

    def test_cup_is_not_detected_after_calibration(self):
        self.camera.ball = None
        self.camera.cups = [(800, 400)]
        self.step()
        self.assertIsNotNone(self.engine.last_detection)  # 캘리브레이션 전: 픽셀 기준이라 컵도 잡힌다
        self.assertEqual(self.engine.align_corners([list(c) for c in MAT_CORNERS]), "OK")
        self.step()
        self.assertIsNone(self.engine.last_detection)  # mm 기준: 컵은 공보다 커서 걸러짐
        self.assertGreater(self.engine.log_stats()["size_rejected"], 0)


if __name__ == "__main__":
    unittest.main()
