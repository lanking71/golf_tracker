"""
24단계: 공 위치 기준 검색 영역(ROI) 테스트.

가짜 카메라 장면: 4500x2000mm 매트가 화면에서 약 900px (1mm ≈ 0.2px).
테스트 공통 설정(tests/fake_camera.py)의 검색 반경: idle 600mm(≈120px), track 500mm(≈100px).
'미끼 공'(decoys)은 공과 똑같이 생긴 다른 물체(현장의 흰 컵 역할)다.
"""

import math
import unittest

import cv2
import numpy as np

from src.detector import BallDetector
from src.engine import START_OK
from src.search import EMPTY_ROI, LOST_GROWTH, SearchPlanner
from src.tracker import TrackerState
from tests.base import EngineTestCase
from tests.fake_camera import BALL_COLOR_BGR, BALL_RADIUS

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


def frame_with_balls(*points):
    frame = np.full((720, 1280, 3), (40, 40, 40), np.uint8)
    for point in points:
        cv2.circle(frame, point, BALL_RADIUS, BALL_COLOR_BGR, -1)
    return frame


class DetectorRoiTests(unittest.TestCase):
    def setUp(self):
        self.detector = BallDetector(PROFILE)

    def test_only_ball_inside_roi_and_full_frame_coordinates(self):
        frame = frame_with_balls((300, 300), (900, 500))
        found = self.detector.detect(frame, roi=(800, 400, 200, 200))
        self.assertIsNotNone(found)
        # 잘라낸 영역 기준(100, 100)이 아니라 프레임 전체 기준 좌표
        self.assertAlmostEqual(found.x, 900, delta=1)
        self.assertAlmostEqual(found.y, 500, delta=1)

    def test_nothing_in_roi(self):
        frame = frame_with_balls((300, 300))
        self.assertIsNone(self.detector.detect(frame, roi=(800, 400, 200, 200)))

    def test_empty_roi_searches_nowhere(self):
        frame = frame_with_balls((300, 300))
        self.assertIsNone(self.detector.detect(frame, roi=EMPTY_ROI))

    def test_prefer_picks_nearest(self):
        frame = frame_with_balls((300, 300), (600, 300))
        found = self.detector.detect(frame, prefer=(580, 310))
        self.assertAlmostEqual(found.x, 600, delta=1)
        found = self.detector.detect(frame, prefer=(310, 290))
        self.assertAlmostEqual(found.x, 300, delta=1)


class PlannerTests(unittest.TestCase):
    """SearchPlanner 단독: 캘리브레이션 없이 픽셀 기본 반경으로."""

    FRAME = (1280, 720)

    @staticmethod
    def px(center, radius_mm, default_px):
        return default_px  # 캘리브레이션 없음

    def plan(self, planner, state, now, start=None):
        return planner.plan(state, start, None, self.FRAME, now, self.px)

    def test_idle_is_full_and_ready_is_around_start(self):
        planner = SearchPlanner({})
        self.assertIsNone(self.plan(planner, TrackerState.IDLE, 0).roi)
        self.assertIsNone(self.plan(planner, TrackerState.READY, 0).roi)  # 시작점 없음
        plan = self.plan(planner, TrackerState.READY, 0, start=(500, 400))
        self.assertEqual(plan.reason, "start")
        self.assertEqual(plan.roi, (350, 250, 300, 300))  # 기본 150px 반경

    def test_tracking_predicts_and_grows_when_lost(self):
        planner = SearchPlanner({"lost_frames_full": 3})
        self.plan(planner, TrackerState.ARMED, 0.0, start=(100, 400))
        planner.observe((100, 400), 0.0)
        self.plan(planner, TrackerState.TRACKING, 0.1)
        planner.observe((140, 400), 0.1)  # 400px/s (스무딩 0.6 -> 240px/s)
        plan = self.plan(planner, TrackerState.TRACKING, 0.2)
        self.assertEqual(plan.reason, "track")
        self.assertGreater(plan.center[0], 140)  # 진행 방향으로 앞선 예상 위치
        self.assertAlmostEqual(plan.center[1], 400)
        base = plan.radius

        radii = []
        for i in range(3):
            planner.observe(None, 0.2 + i * 0.05)  # 놓침
            lost_plan = self.plan(planner, TrackerState.TRACKING, 0.25 + i * 0.05)
            self.assertTrue(lost_plan.widened)
            radii.append(lost_plan.radius)
        self.assertGreater(radii[0], base * LOST_GROWTH * 0.99)
        self.assertGreater(radii[2], radii[1] > radii[0])
        planner.observe(None, 0.4)  # 4번째 놓침 > lost_frames_full(3)
        full = self.plan(planner, TrackerState.TRACKING, 0.45)
        self.assertIsNone(full.roi)
        self.assertEqual(full.reason, "lost")

    def test_prediction_outside_frame_searches_nowhere(self):
        planner = SearchPlanner({})
        self.plan(planner, TrackerState.ARMED, 0.0, start=(1200, 400))
        planner.observe((1200, 400), 0.0)
        self.plan(planner, TrackerState.TRACKING, 0.05)
        planner.observe((1260, 400), 0.05)
        plan = self.plan(planner, TrackerState.TRACKING, 2.0)  # 오래 지나 예상 위치가 화면 오른쪽 밖
        self.assertEqual(plan.roi, EMPTY_ROI)

    def test_disabled_is_always_full(self):
        planner = SearchPlanner({"enabled": False})
        self.assertIsNone(self.plan(planner, TrackerState.ARMED, 0, start=(500, 400)).roi)


class EngineSearchTests(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.step()
        self.assertEqual(self.engine.align_auto(4500, 2000), "OK")
        self.assertEqual(self.engine.register_start(), START_OK)  # 시작점 (500, 400)

    def last_plan(self):
        return self.frames[-1].search_plan

    def arm(self):
        self.assertTrue(self.engine.start_tracking())
        for _ in range(3):
            self.step()

    def test_mm_radius_uses_calibration(self):
        self.step()
        plan = self.last_plan()
        self.assertEqual(plan.reason, "start")
        # 600mm x 약 0.2px/mm ≈ 120px (원근 때문에 조금 다를 수 있음)
        self.assertGreater(plan.radius, 90)
        self.assertLess(plan.radius, 160)

    def test_far_decoy_ignored_around_start(self):
        """시작점 주변만 찾으므로, 멀리 있는 공 모양 물체(컵 역할)는 무시된다."""
        self.arm()
        self.camera.decoys = [(900, 300)]
        self.camera.ball = None  # 공이 잠깐 가려짐
        for _ in range(5):
            self.step()
        self.assertIsNone(self.engine.last_detection)
        self.assertEqual(self.engine.tracker.state, TrackerState.ARMED)  # 퍼팅 시작으로 오판하지 않음

    def test_tracking_follows_ball(self):
        self.arm()
        for i in range(1, 8):
            self.step(ball=(500 + i * 25, 400))
        self.assertEqual(self.engine.tracker.state, TrackerState.TRACKING)
        plan = self.last_plan()
        self.assertEqual(plan.reason, "track")
        # 이번 프레임의 예상 위치는 공 위치(675) 근처, 오른쪽으로 진행
        self.assertAlmostEqual(plan.center[0], 675, delta=30)
        self.assertAlmostEqual(self.engine.last_detection.x, 675, delta=2)

    def test_decoy_beside_path_not_chosen(self):
        """추적 중 검색 영역 안에 공 모양 물체가 같이 있어도 예상 위치에 가까운 공을 고른다."""
        self.arm()
        self.camera.decoys = [(650, 340)]
        for i in range(1, 8):
            self.step(ball=(500 + i * 25, 400))
            self.assertAlmostEqual(self.engine.last_detection.y, 400, delta=2)

    def test_lost_then_wrong_direction_candidate_rejected(self):
        self.arm()
        for i in range(1, 9):
            self.step(ball=(500 + i * 25, 400))  # 오른쪽으로 500px/s
        last_x = 700
        self.camera.ball = None
        self.step()  # 놓침 1
        self.assertTrue(self.engine.search.lost_frames >= 1)
        # 공이 오던 쪽(뒤)에 공 모양 물체가 나타남 -> 넓힌 영역 안이어도 방향이 맞지 않아 버린다
        self.camera.decoys = [(last_x - 60, 400)]
        self.step()
        self.assertIsNone(self.engine.last_detection)
        self.assertGreaterEqual(self.engine.rejected_candidates, 1)
        # 공이 진행 방향 앞에서 다시 보이면 받아들인다
        self.camera.decoys = []
        self.step(ball=(last_x + 75, 400))
        self.assertIsNotNone(self.engine.last_detection)
        self.assertAlmostEqual(self.engine.last_detection.x, last_x + 75, delta=2)

    def test_lost_switches_to_full_search(self):
        self.arm()
        for i in range(1, 5):
            self.step(ball=(500 + i * 10, 400))
        self.camera.ball = None
        for _ in range(12):  # lost_frames_full(10)보다 많이 놓침
            self.step()
        # '전체' 검색으로 넘어가되, 26단계부터 캘리브레이션이 있으면 매트 영역(모서리를 감싸는 사각형 + 여유)만 찾는다
        self.assertEqual(self.last_plan().roi, self.engine._mat_roi())
        self.assertIsNotNone(self.engine._mat_roi())
        self.assertEqual(self.last_plan().reason, "lost")
        self.assertEqual(self.engine.tracker.state, TrackerState.TRACKING)  # 0.6초 < lost_duration 2초

    def test_lost_ball_still_finishes_with_lost(self):
        """검색 영역을 써도 공이 정말 사라지면 기존과 같은 시간(lost_duration) 뒤 LOST로 끝난다."""
        self.arm()
        for i in range(1, 5):
            self.step(ball=(500 + i * 20, 400))
        self.camera.ball = None
        for _ in range(45):  # 2.25초
            self.step()
        self.assertEqual(self.engine.tracker.state, TrackerState.FINISHED)
        self.assertEqual(self.finished, ["화면 이탈"])

    def test_ready_falls_back_to_full_to_re_register(self):
        """READY에서 공을 멀리 옮기면 잠시 뒤 전체 검색으로 바뀌어 새 위치에서 다시 등록할 수 있다."""
        self.step(ball=(850, 300))
        self.assertIsNone(self.engine.last_detection)  # 처음에는 시작점 주변만 찾음
        for _ in range(11):
            self.step()
        self.assertIsNotNone(self.engine.last_detection)
        self.assertEqual(self.engine.register_start(), START_OK)
        self.assertEqual(self.engine.tracker.start_position, (850, 300))

    def test_stats_report_detect_time_and_roi(self):
        for _ in range(3):
            self.step()
        summary = self.engine.log_stats()
        self.assertIn("detect_avg_ms", summary)
        self.assertLess(summary["roi_avg_pct"], 100)  # 시작점 주변만 찾았으므로 화면 일부


class SearchDisabledTests(EngineTestCase):
    """search.enabled: false면 예전처럼 화면 전체에서 찾는다."""

    settings_overrides = {"search": {"enabled": False}}

    def test_full_search_finds_far_decoy(self):
        self.step()
        self.assertEqual(self.engine.align_auto(4500, 2000), "OK")
        self.assertEqual(self.engine.register_start(), START_OK)
        self.camera.decoys = [(900, 300)]
        self.camera.ball = None
        self.step()
        self.assertIsNone(self.frames[-1].search_plan.roi)
        self.assertIsNotNone(self.engine.last_detection)  # 멀리 있는 물체도 잡힌다 (예전 동작)
        self.assertAlmostEqual(math.hypot(self.engine.last_detection.x - 900, self.engine.last_detection.y - 300), 0, delta=2)
        summary = self.engine.log_stats()
        self.assertAlmostEqual(summary["roi_avg_pct"], 100)


if __name__ == "__main__":
    unittest.main()
