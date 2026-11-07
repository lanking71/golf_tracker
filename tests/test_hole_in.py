"""
25단계: 홀인 판정 테스트.

- 컵 근처에서 공이 사라지고 confirm_duration 동안 안 보이면 HOLE_IN (끝 위치 = 컵 중심)
- 컵 근처에서 잠깐 사라졌다가 다시 보이면 추적 계속 (립아웃)
- 컵 옆을 지나가면 컵을 공으로 잡지 않고 정지 위치에서 STOP
- 컵과 먼 곳에서 사라지면 기존처럼 LOST
- 프로토콜: RESULT.reason/hole, HELLO.holes / DB: finish_reason, hole (예전 DB도 열림)
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.storage import Storage
from src.tracker import HOLE_REASON, LOST_REASON, STOP_REASON, Tracker, TrackerState
from tests.base import EngineTestCase
from tests.fake_camera import MAT_CORNERS

SCAN_STEPS = 65
CUP = (800, 400)


class TrackerHoleTests(unittest.TestCase):
    """Tracker만으로 시험 (hole_finder를 직접 넣는다)."""

    def setUp(self):
        self.tracker = Tracker({"stop_duration": 1.0, "lost_duration": 2.0}, {"confirm_duration": 0.5})
        # (x, y)가 (800, 400)에서 50px 안이면 1번 컵 근처
        self.tracker.hole_finder = lambda x, y: (1, CUP) if abs(x - 800) <= 50 and abs(y - 400) <= 50 else None
        self.tracker.register_start_position(300, 400)
        self.tracker.start_tracking()
        self.t = 0.0
        self.feed(300, 400)
        self.feed(340, 400)  # 추적 시작
        self.assertEqual(self.tracker.state, TrackerState.TRACKING)

    def feed(self, x, y, dt=0.05):
        self.t += dt
        self.tracker.add_trajectory_point(x, y, self.t) if x is not None else None
        return self.tracker.update_detection(x, y, self.t)

    def test_lost_near_cup_is_hole_in(self):
        self.feed(760, 400)
        reasons = [self.feed(None, None) for _ in range(12)]
        self.assertIn(HOLE_REASON, reasons)
        self.assertEqual(self.tracker.finish_reason, HOLE_REASON)
        self.assertEqual(self.tracker.finish_hole, 1)
        self.assertEqual(self.tracker.stop_position, CUP)
        # 0.5초가 지나야 확정된다 (첫 프레임은 사라진 시각만 기록)
        self.assertGreaterEqual(reasons.index(HOLE_REASON), 10)

    def test_reappear_continues_tracking(self):
        self.feed(760, 400)
        for _ in range(4):  # 0.2초만 사라짐 (컵을 스침)
            self.assertIsNone(self.feed(None, None))
        self.feed(860, 400)
        self.assertEqual(self.tracker.state, TrackerState.TRACKING)
        self.assertIsNone(self.tracker.finish_hole)
        for _ in range(30):
            self.feed(900, 400)
        self.assertEqual(self.tracker.finish_reason, STOP_REASON)
        self.assertEqual(self.tracker.stop_position, (900, 400))

    def test_lost_far_from_cup_is_lost(self):
        self.feed(500, 400)
        reasons = [self.feed(None, None) for _ in range(45)]
        self.assertNotIn(HOLE_REASON, reasons)
        self.assertEqual(self.tracker.finish_reason, LOST_REASON)
        self.assertIsNone(self.tracker.finish_hole)


class EngineHoleTests(EngineTestCase):
    """가짜 영상: 시작점 (300, 400), 컵 (800, 400). 가짜 매트는 1mm = 약 0.2px라 포착 반경을 넉넉히 준다."""

    settings_overrides = {
        "hole": {"enabled": True, "capture_radius_mm": 400, "confirm_duration": 0.5, "min_area_ratio": 1.3}
    }

    def setUp(self):
        super().setUp()
        self.camera.ball = (300, 400)
        self.camera.cups = [CUP]
        self.step()
        self.assertEqual(self.engine.align_corners([list(c) for c in MAT_CORNERS]), "OK")
        for _ in range(SCAN_STEPS):  # 캘리브레이션 직후 자동 수집 (컵 기억)
            self.step()
        self.assertEqual([h.hole for h in self.engine.static_objects.holes()], [1])
        self.assertEqual(self.engine.register_start(), "OK")
        for _ in range(SCAN_STEPS):  # 시작점 등록 후 자동 수집
            self.step()
        self.handler = self.engine.protocol_handler
        self.sent = []
        self.handler._send = self.sent.append
        self.assertTrue(self.engine.start_tracking())
        for _ in range(5):
            self.step()

    def roll(self, xs, y=400):
        for x in xs:
            self.step(ball=(x, y))

    def result(self):
        results = [m for m in self.sent if m["cmd"] == "RESULT"]
        self.assertEqual(len(results), 1)
        return results[0]

    def test_putt_into_cup_is_hole_in(self):
        self.roll(range(340, 741, 40))  # 컵 앞 (중심 거리 60px)까지
        self.assertEqual(self.engine.tracker.state, TrackerState.TRACKING)
        self.camera.ball = None  # 컵에 들어감
        for _ in range(15):
            self.step()
        tracker = self.engine.tracker
        self.assertEqual(tracker.state, TrackerState.FINISHED)
        self.assertEqual(tracker.finish_reason, HOLE_REASON)
        self.assertEqual(tracker.finish_hole, 1)
        self.assertEqual(self.finished, [HOLE_REASON])
        result = self.result()
        self.assertEqual((result["reason"], result["hole"]), ("HOLE_IN", 1))
        cup_mm = self.engine.calibration.pixel_to_real(*CUP)
        self.assertAlmostEqual(result["stop"][0], cup_mm[0], delta=15)  # 끝 위치 = 컵 중심
        self.assertAlmostEqual(result["stop"][1], cup_mm[1], delta=15)
        # DB에도 홀인과 컵 번호가 남는다
        status, result_id = self.engine.save_result()
        self.assertEqual(status, "OK")
        saved = self.engine.storage.get_result(result_id)
        self.assertEqual((saved.finish_reason, saved.hole), (HOLE_REASON, 1))

    def test_putt_past_cup_stops_at_ball(self):
        # 컵 옆(중심에서 70px 아래)을 지나 멈춤: 컵을 공으로 잡지 않는다
        self.roll(range(340, 961, 40), y=470)
        for _ in range(15):
            self.step()
        tracker = self.engine.tracker
        self.assertEqual(tracker.finish_reason, STOP_REASON)
        self.assertIsNone(tracker.finish_hole)
        self.assertAlmostEqual(tracker.stop_position[0], 940, delta=3)
        self.assertAlmostEqual(tracker.stop_position[1], 470, delta=3)
        # 궤적에 컵 자리(800, 400) 근처 점이 없어야 한다
        self.assertFalse(any(abs(p.x - 800) < 20 and abs(p.y - 400) < 20 for p in tracker.trajectory))
        result = self.result()
        self.assertEqual((result["reason"], result["hole"]), ("STOP", None))

    def test_lip_out_continues(self):
        self.roll(range(340, 741, 40))
        self.camera.ball = None
        for _ in range(4):  # 0.2초 동안 안 보임
            self.step()
        self.roll([860, 900, 940])  # 컵 너머로 다시 나타남
        self.assertEqual(self.engine.tracker.state, TrackerState.TRACKING)
        for _ in range(15):
            self.step()
        self.assertEqual(self.engine.tracker.finish_reason, STOP_REASON)

    def test_no_hole_check_after_finish(self):
        # 홀인으로 끝난 뒤에는 홀인 확인 상태가 남지 않는다 (공을 꺼내는 손 때문에 검사·로그가 반복되던 문제)
        self.roll(range(340, 741, 40))
        self.camera.ball = None
        for _ in range(15):
            self.step()
        self.assertEqual(self.engine.tracker.finish_reason, HOLE_REASON)
        self.assertIsNone(self.engine.tracker.lost_hole)

    def test_lost_far_from_cup_is_lost(self):
        self.roll(range(340, 461, 40))  # 컵에서 먼 곳
        self.camera.ball = None
        for _ in range(45):
            self.step()
        self.assertEqual(self.engine.tracker.finish_reason, LOST_REASON)
        self.assertEqual(self.result()["reason"], "LOST")

    def test_hello_lists_holes(self):
        hello = []
        self.handler._send_to = lambda client, message: hello.append(message)
        self.handler.on_client_connected("A")
        holes = hello[0]["holes"]
        self.assertEqual(len(holes), 1)
        self.assertEqual(holes[0]["id"], 1)
        cup_mm = self.engine.calibration.pixel_to_real(*CUP)
        self.assertAlmostEqual(holes[0]["x_mm"], cup_mm[0], delta=15)
        self.assertAlmostEqual(holes[0]["y_mm"], cup_mm[1], delta=15)


class StorageMigrationTests(unittest.TestCase):
    def test_old_database_gets_new_columns(self):
        path = Path(tempfile.mkdtemp()) / "old.db"
        conn = sqlite3.connect(path)
        conn.execute(
            """CREATE TABLE results (
                id INTEGER PRIMARY KEY AUTOINCREMENT, recorded_at TEXT NOT NULL, profile_name TEXT,
                start_x_mm REAL, start_y_mm REAL, stop_x_mm REAL, stop_y_mm REAL,
                total_distance_cm REAL, duration_s REAL, average_speed_cm REAL, max_speed_cm REAL,
                straightness REAL, mat_width_mm REAL, mat_height_mm REAL, trajectory_json TEXT NOT NULL)"""
        )
        conn.execute(
            "INSERT INTO results (recorded_at, profile_name, trajectory_json) VALUES ('2026-01-01T00:00:00', 'p', '[]')"
        )
        conn.commit()
        conn.close()

        storage = Storage(str(path))
        old = storage.list_results()[0]
        self.assertIsNone(old.finish_reason)  # 예전 기록은 빈 값
        self.assertIsNone(old.hole)
        new_id = storage.save_result("p", None, None, 1, 1, 1, 1, 1, 3000, 1000, [], HOLE_REASON, 2)
        self.assertEqual(storage.get_result(new_id).hole, 2)


if __name__ == "__main__":
    unittest.main()
