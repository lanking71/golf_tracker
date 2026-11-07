"""
25단계: 고정 물체(컵·티) 기억 테스트.

- 수집: 계속 같은 자리에 있던 덩어리만 기억, 시작점의 공은 제외, 자동 수집은 공 크기 덩어리 제외
- 후보 제거: 고정 물체 자리의 후보는 공으로 보지 않음, 컵 테두리에 붙은 공은 인정
- 처리 조건(프레임 크기·k1)이 바뀌면 무효
"""

import json
import unittest

import src.config
from src.detector import DetectionCandidate
from src.static_objects import (
    SCAN_AUTO,
    SCAN_MANUAL,
    StaticObject,
    StaticObjectCollector,
    StaticObjectMap,
    number_holes,
)
from src.tracker import TrackerState
from tests.base import EngineTestCase
from tests.fake_camera import MAT_CORNERS

SCAN_STEPS = 65  # 3초 수집 (프레임 간격 0.05초) + 여유


def blob(x, y, area=400.0, diameter=22.0, ball_ok=False):
    return DetectionCandidate(x, y, area, diameter, 0.9, ball_ok, None, None if ball_ok else "size")


class CollectorTests(unittest.TestCase):
    def run_scan(self, collector, frames, kind=SCAN_AUTO, exclude=None):
        collector.start(0.0, kind, exclude)
        for i, blobs in enumerate(frames):
            collector.feed(blobs, 0.1 * (i + 1), min_area=20)
        return collector.finish(lambda x, y: 9.0)

    def test_still_blob_is_kept_and_moving_blob_is_not(self):
        frames = [[blob(100, 100), blob(200 + i * 10, 300)] for i in range(30)]
        objects = self.run_scan(StaticObjectCollector(3.0, 3), frames, kind=SCAN_MANUAL)
        self.assertEqual(len(objects), 1)
        self.assertAlmostEqual(objects[0].x, 100)
        self.assertAlmostEqual(objects[0].radius, 11 + 2)  # 반경 + 여유 2px

    def test_small_jitter_is_same_place(self):
        frames = [[blob(100 + (i % 3) - 1, 100)] for i in range(30)]
        objects = self.run_scan(StaticObjectCollector(3.0, 3), frames)
        self.assertEqual(len(objects), 1)

    def test_start_ball_is_excluded(self):
        frames = [[blob(100, 100), blob(300, 300, area=200, diameter=16, ball_ok=True)] for _ in range(30)]
        objects = self.run_scan(StaticObjectCollector(3.0, 3), frames, exclude=[(300, 300)])
        self.assertEqual([(o.x, o.y) for o in objects], [(100, 100)])

    def test_auto_scan_without_start_skips_ball_sized(self):
        frames = [[blob(100, 100), blob(300, 300, area=200, diameter=16, ball_ok=True)] for _ in range(30)]
        auto = self.run_scan(StaticObjectCollector(3.0, 3), frames, kind=SCAN_AUTO)
        self.assertEqual(len(auto), 1)  # 공일 수도 있는 공 크기 덩어리는 기억하지 않음
        # 수동 수집도 시작점이 없으면 공 크기 덩어리는 기억하지 않는다
        # (현장에서 공을 매트에 둔 채 '고정 물체 다시 찾기'를 눌러 공 자리가 고정 물체가 됐던 문제)
        manual = self.run_scan(StaticObjectCollector(3.0, 3), frames, kind=SCAN_MANUAL)
        self.assertEqual(len(manual), 1)

    def test_too_few_frames_gives_none(self):
        self.assertIsNone(self.run_scan(StaticObjectCollector(3.0, 3), [[blob(1, 1)]] * 2))

    def test_holes_numbered_top_to_bottom_then_left_to_right(self):
        objects = [
            StaticObject(500, 300, 12, 400),  # 아래
            StaticObject(900, 100, 12, 400),  # 위 오른쪽
            StaticObject(100, 110, 12, 400),  # 위 왼쪽 (y 차이 10mm -> 같은 줄)
            StaticObject(300, 200, 6, 100),  # 티 (작음) -> 홀 아님
        ]
        number_holes(objects, lambda x, y: 200.0, lambda x, y: (x, y), 1.3)
        holes = {(o.x, o.y): o.hole for o in objects}
        self.assertEqual(holes[(100, 110)], 1)
        self.assertEqual(holes[(900, 100)], 2)
        self.assertEqual(holes[(500, 300)], 3)
        self.assertIsNone(holes[(300, 200)])


class StaticMapTests(unittest.TestCase):
    def setUp(self):
        # 컵 반경 R = 12px, 공 반경 r = 9px -> 테두리에 닿는 거리 21px
        self.map = StaticObjectMap([StaticObject(100, 100, 12, 400)], (1280, 720), 0.0, 0.8)

    def test_candidate_on_object_is_removed(self):
        self.assertTrue(self.map.is_static_candidate(101, 100, True, 9))
        self.assertTrue(self.map.is_static_candidate(110, 100, True, 9))  # R 안쪽

    def test_ball_touching_rim_is_accepted(self):
        # 중심 거리 20px >= 21 x 0.8 = 16.8 이고 공 크기 통과 -> 공으로 인정
        self.assertFalse(self.map.is_static_candidate(120, 100, True, 9))
        # 같은 자리라도 크기·모양이 공이 아니면(컵 조각 등) 뺀다
        self.assertTrue(self.map.is_static_candidate(120, 100, False, 9))
        # 컵에 너무 붙은(겹친) 자리는 공 크기여도 뺀다
        self.assertTrue(self.map.is_static_candidate(115, 100, True, 9))

    def test_far_candidate_is_kept(self):
        self.assertFalse(self.map.is_static_candidate(200, 100, False, 9))

    def test_processing_match_and_round_trip(self):
        loaded = StaticObjectMap.from_settings(self.map.to_settings())
        self.assertEqual(len(loaded.objects), 1)
        self.assertTrue(loaded.matches_processing((1280, 720), 0.0))
        self.assertFalse(loaded.matches_processing((1440, 810), 0.0))
        self.assertFalse(loaded.matches_processing((1280, 720), -0.5))
        self.assertFalse(StaticObjectMap.from_settings(None).matches_processing((1280, 720), 0.0))


class EngineStaticTests(EngineTestCase):
    """가짜 영상: 시작점 공 (300, 400), 컵 (800, 400), 공 크기 흰 물체(티 역할) (600, 250)."""

    def setUp(self):
        super().setUp()
        self.camera.ball = (300, 400)
        self.camera.cups = [(800, 400)]

    def calibrate_and_scan(self):
        self.step()
        self.assertEqual(self.engine.align_corners([list(c) for c in MAT_CORNERS]), "OK")
        self.assertTrue(self.engine.static_scan_active)  # 캘리브레이션 직후 자동 수집
        for _ in range(SCAN_STEPS):
            self.step()
        self.assertFalse(self.engine.static_scan_active)

    def saved(self):
        return json.loads(src.config.SETTINGS_PATH.read_text(encoding="utf-8"))["static_objects"]

    def test_scan_after_calibration_finds_cup_but_not_ball(self):
        self.calibrate_and_scan()
        objects = self.engine.static_objects.objects
        self.assertEqual(len(objects), 1)  # 컵만 (공은 공 크기라 자동 수집에서 뺀다)
        cup = objects[0]
        self.assertAlmostEqual(cup.x, 800, delta=2)
        self.assertEqual(cup.hole, 1)
        self.assertEqual(len(self.saved()["objects"]), 1)
        self.assertEqual(self.saved()["frame_size"], [1280, 720])

    def test_register_start_scan_excludes_start_ball_and_keeps_tee(self):
        self.calibrate_and_scan()
        self.camera.decoys = [(600, 250)]  # 공 크기 물체 (티 역할)
        self.step()
        self.assertEqual(self.engine.register_start(), "OK")
        self.assertTrue(self.engine.static_scan_active)
        for _ in range(SCAN_STEPS):
            self.step()
        positions = sorted((round(o.x, -1), round(o.y, -1)) for o in self.engine.static_objects.objects)
        self.assertEqual(positions, [(600, 250), (800, 400)])  # 시작점의 공 (300, 400)은 빠짐
        tee = [o for o in self.engine.static_objects.objects if round(o.x, -1) == 600][0]
        self.assertIsNone(tee.hole)  # 공 크기 물체는 홀컵이 아님
        # 공을 치우면 화면 전체를 찾게 되는데, 티는 고정 물체라 공으로 잡지 않는다
        self.engine.reset()
        self.camera.ball = None
        for _ in range(3):
            self.step()
        self.assertIsNone(self.engine.last_detection)
        # 공은 여전히 찾는다 (고정 물체와 먼 자리)
        self.step(ball=(400, 450))
        self.assertIsNotNone(self.engine.last_detection)

    def test_manual_rescan_and_ball_next_to_cup(self):
        self.calibrate_and_scan()
        self.camera.ball = None
        self.assertTrue(self.engine.rescan_static_objects())
        for _ in range(SCAN_STEPS):
            self.step()
        self.assertEqual(len(self.engine.static_objects.objects), 1)
        # 컵 바로 옆(간격 8px)에 공을 놓아도 공으로 인정된다. (간격이 5px쯤보다 좁으면 블러·모폴로지
        # 때문에 컵과 공이 한 덩어리로 붙어 버려서 어떤 방법으로도 따로 볼 수 없다 - 테두리 판정 규칙
        # 자체는 StaticMapTests.test_ball_touching_rim_is_accepted에서 시험한다)
        self.step(ball=(800, 400 + 26 + 18 + 8))
        self.assertIsNotNone(self.engine.last_detection)
        self.assertAlmostEqual(self.engine.last_detection.y, 452, delta=2)

    def test_color_pick_click_forgets_static_at_ball(self):
        # 공 자리가 고정 물체로 잘못 기억된 상황 (예전 수동 수집에서 공을 둔 채 눌렀던 경우)
        self.calibrate_and_scan()
        self.engine.static_objects.objects.append(StaticObject(300, 400, 20, 1000))
        self.step()
        self.assertIsNone(self.engine.last_detection)  # 공을 못 찾음
        self.assertEqual(self.engine.forget_static_at(302, 398), 1)  # 색 잡기에서 공 클릭
        self.assertEqual(self.engine.forget_static_at(800, 400), 0)  # 홀컵은 지우지 않음
        self.assertEqual(len(self.saved()["objects"]), 1)
        self.step()
        self.assertIsNotNone(self.engine.last_detection)

    def test_rescan_refused_while_tracking(self):
        self.calibrate_and_scan()
        self.assertEqual(self.engine.register_start(), "OK")
        self.engine.start_tracking()
        self.assertEqual(self.engine.tracker.state, TrackerState.ARMED)
        self.assertFalse(self.engine.rescan_static_objects())

    def test_processing_change_invalidates_and_restores(self):
        self.calibrate_and_scan()
        self.assertEqual(len(self.engine.static_objects.objects), 1)
        self.engine.set_undistort_k1(0.1)
        self.step()
        self.assertEqual(self.engine.static_objects.objects, [])  # 조건이 바뀌면 쓰지 않음
        self.engine.set_undistort_k1(0.0)
        self.step()
        self.assertEqual(len(self.engine.static_objects.objects), 1)  # 되돌리면 다시 사용

    def test_saved_objects_used_after_restart(self):
        self.calibrate_and_scan()
        from src.engine import VisionEngine

        self.engine.shutdown()
        self.engine = VisionEngine(camera=self.camera)
        self.engine.camera_retry_interval = 0
        self.step()
        self.assertFalse(self.engine.static_scan_active)  # 저장된 값이 유효하면 다시 모으지 않음
        self.assertEqual(len(self.engine.static_objects.objects), 1)


if __name__ == "__main__":
    unittest.main()
