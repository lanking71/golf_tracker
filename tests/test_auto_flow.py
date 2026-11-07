"""
26단계: 자동 흐름 테스트 (버튼 없이 공 놓기 → 퍼팅 → 결과 → 공 다시 놓기).

가짜 영상의 매트는 1mm = 약 0.2px라서 시작 구역 반경 300mm = 약 60~75px,
'자리 고쳐 놓기' 기준 100mm = 약 20~25px다. 추적 시작 기준은 테스트 설정의 px 값(8px)을 쓴다.
"""

import json
import unittest

import src.config
from src.auto_flow import ACTION_ARM, ACTION_DROP, ACTION_READY, AutoFlow
from src.engine import VisionEngine
from src.tracker import HOLE_REASON, STOP_REASON, TrackerState
from tests.base import EngineTestCase
from tests.fake_camera import MAT_CORNERS

START = (300, 400)
SCAN_STEPS = 65
AUTO_ON = {
    "enabled": True,
    "start_zone": None,
    "start_zone_radius_mm": 300,
    "anywhere": False,
    "settle_duration": 1.0,
    "settle_tolerance_mm": 5,
    "min_putt_distance_mm": 100,
    "readdress_timeout": 1.5,
    "ball_gone_duration": 3.0,
    "save_results": True,
}


class AutoFlowUnitTests(unittest.TestCase):
    """AutoFlow만 따로 (mm 좌표로 직접 넣어 본다)."""

    def setUp(self):
        self.flow = AutoFlow(AUTO_ON)
        self.t = 0.0

    def feed(self, state, ball_mm, zone=(1000, 500), has_start=False, holes=(), inside=True, dt=0.05):
        self.t += dt
        ball_px = None if ball_mm is None else ball_mm
        return self.flow.step(state, True, has_start, ball_px, ball_mm, self.t, zone_mm=zone, inside_mat=inside, holes_mm=holes)

    def test_idle_goes_ready_only_when_calibrated(self):
        self.assertEqual(self.feed(TrackerState.IDLE, None).kind, ACTION_READY)
        self.assertIsNone(self.flow.step(TrackerState.IDLE, False, False, None, None, 0.0))
        self.flow.enabled = False
        self.assertIsNone(self.feed(TrackerState.IDLE, None))

    def test_settle_in_zone_arms_after_one_second(self):
        actions = [self.feed(TrackerState.READY, (1010, 505)) for _ in range(19)]
        self.assertTrue(all(a is None for a in actions))  # 0.95초: 아직
        actions = [self.feed(TrackerState.READY, (1011, 505)) for _ in range(3)]
        arm = [a for a in actions if a is not None]
        self.assertEqual(len(arm), 1)
        self.assertEqual(arm[0].kind, ACTION_ARM)
        self.assertFalse(arm[0].keep_previous)

    def test_outside_zone_does_nothing(self):
        actions = [self.feed(TrackerState.READY, (2000, 500)) for _ in range(40)]
        self.assertTrue(all(a is None for a in actions))

    def test_anywhere_but_not_near_hole(self):
        self.flow.anywhere = True
        actions = [self.feed(TrackerState.READY, (2000, 500), zone=None) for _ in range(25)]
        self.assertTrue(any(a is not None and a.kind == ACTION_ARM for a in actions))
        actions = [self.feed(TrackerState.READY, (2900, 480), zone=None, holes=[(2895, 486)]) for _ in range(40)]
        self.assertTrue(all(a is None for a in actions))  # 홀컵 근처는 시작점으로 보지 않음
        actions = [self.feed(TrackerState.READY, (2000, 500), zone=None, inside=False) for _ in range(40)]
        self.assertTrue(all(a is None for a in actions))  # 매트 밖도 안 됨

    def test_moving_ball_is_not_registered(self):
        actions = [self.feed(TrackerState.READY, (1000 + i * 8, 500)) for i in range(40)]
        self.assertTrue(all(a is None for a in actions))

    def test_one_missing_frame_restarts_settle(self):
        for _ in range(15):
            self.assertIsNone(self.feed(TrackerState.READY, (1000, 500)))
        self.assertIsNone(self.feed(TrackerState.READY, None))  # 손이 가려서 한 프레임 못 찾음
        for _ in range(15):
            self.assertIsNone(self.feed(TrackerState.READY, (1000, 500)))  # 처음부터 다시 셈 (0.75초)

    def test_hold_after_finish_until_released(self):
        self.flow.hold_until_released((1000, 500))
        actions = [self.feed(TrackerState.FINISHED, (1000, 500)) for _ in range(60)]
        self.assertTrue(all(a is None for a in actions))  # 그 자리에 그대로 있으면 다음 준비 안 함
        for _ in range(8):  # 공을 집어 감 (0.4초)
            self.feed(TrackerState.FINISHED, None)
        actions = [self.feed(TrackerState.FINISHED, (1005, 500)) for _ in range(25)]
        arm = [a for a in actions if a is not None]
        self.assertEqual(len(arm), 1)
        self.assertTrue(arm[0].keep_previous)

    def test_armed_ball_gone(self):
        for _ in range(20):  # 1초 가려짐: 유지
            self.assertIsNone(self.feed(TrackerState.ARMED, None))
        self.assertIsNone(self.feed(TrackerState.ARMED, (1000, 500)))
        actions = [self.feed(TrackerState.ARMED, None) for _ in range(70)]  # 3.5초
        self.assertEqual([a.kind for a in actions if a is not None], [ACTION_DROP])

    def test_is_readdress(self):
        self.assertTrue(self.flow.is_readdress(STOP_REASON, 60, 10.0, 10.8))
        self.assertFalse(self.flow.is_readdress(STOP_REASON, 150, 10.0, 10.8))  # 충분히 굴러감
        self.assertFalse(self.flow.is_readdress(STOP_REASON, 60, 10.0, 12.0))  # 오래 굴러감
        self.assertFalse(self.flow.is_readdress(HOLE_REASON, 60, 10.0, 10.8))


class AutoEngineTestCase(EngineTestCase):
    """자동 흐름을 켠 엔진. setUp에서 캘리브레이션을 저장한 뒤 엔진을 다시 켠다 (저장된 캘리브레이션 사용)."""

    settings_overrides = {"auto": dict(AUTO_ON)}

    def setUp(self):
        super().setUp()
        self.camera.ball = START
        self.step()
        self.assertEqual(self.engine.align_corners([list(c) for c in MAT_CORNERS]), "OK")
        for _ in range(SCAN_STEPS):
            self.step()
        self.restart()

    def restart(self):
        self.engine.shutdown()
        self.engine = VisionEngine(camera=self.camera)
        self.engine.camera_retry_interval = 0
        self.frames, self.finished = [], []
        self.engine.frame_processed.connect(self.frames.append)
        self.engine.tracking_finished.connect(self.finished.append)
        self.sent = []
        self.engine.protocol_handler._send = self.sent.append
        self.step()

    def events(self, cmd, event=None):
        return [m for m in self.sent if m["cmd"] == cmd and (event is None or m.get("event") == event)]

    def register_zone(self):
        """처음 한 번 '시작 위치 등록' (시작 구역이 저장된다)."""
        self.assertEqual(self.engine.register_start(), "OK")
        self.assertIsNotNone(self.engine.auto.start_zone)

    def wait(self, seconds):
        for _ in range(int(round(seconds / 0.05))):
            self.step()

    def saved_auto(self):
        return json.loads(src.config.SETTINGS_PATH.read_text(encoding="utf-8"))["auto"]


class AutoEngineTests(AutoEngineTestCase):
    def test_startup_with_valid_calibration_is_ready(self):
        self.assertEqual(self.engine.tracker.state, TrackerState.READY)
        self.assertIsNone(self.engine.tracker.start_position)
        self.assertEqual(self.engine.auto_guide_text(self.clock), "시작 위치 등록을 한 번 해 주세요 (자동 모드는 그 자리를 시작 구역으로 기억합니다)")

    def test_first_register_saves_zone_then_auto_arms(self):
        self.register_zone()
        zone = self.saved_auto()["start_zone"]
        start_mm = self.engine.calibration.pixel_to_real(*START)
        self.assertAlmostEqual(zone["x_mm"], start_mm[0], delta=1)
        self.wait(1.2)
        self.assertEqual(self.engine.tracker.state, TrackerState.ARMED)  # '추적 준비' 없이

    def test_saved_zone_used_after_restart(self):
        self.register_zone()
        self.camera.ball = None
        self.restart()
        self.assertEqual(self.engine.tracker.state, TrackerState.READY)
        self.wait(0.5)
        self.camera.ball = (START[0] + 10, START[1] + 5)  # 구역 안에 공을 놓음
        self.wait(0.6)
        self.assertEqual(self.engine.tracker.state, TrackerState.READY)  # 아직 1초 안 됨
        self.wait(0.6)
        self.assertEqual(self.engine.tracker.state, TrackerState.ARMED)
        init = self.events("INIT", "AUTO")
        self.assertEqual(len(init), 1)
        self.assertAlmostEqual(init[0]["x"], self.engine.calibration.pixel_to_real(310, 405)[0], delta=2)

    def test_ball_outside_zone_is_ignored(self):
        self.register_zone()
        self.camera.ball = None
        self.restart()
        self.camera.ball = (700, 300)  # 시작 구역(반경 약 60~75px) 밖
        self.wait(2.0)
        self.assertEqual(self.engine.tracker.state, TrackerState.READY)
        self.assertIsNone(self.engine.last_detection)  # 시작 구역만 찾으므로 보이지도 않음
        self.assertEqual(self.frames[-1].search_plan.reason, "zone")

    def test_moving_ball_is_not_registered(self):
        self.register_zone()
        self.engine.tracker.reset_trajectory()
        self.engine.auto.reset()
        for i in range(40):
            self.step(ball=(START[0] + (i % 2) * 6, START[1]))  # 계속 흔들림
        self.assertEqual(self.engine.tracker.state, TrackerState.READY)

    def arm(self):
        self.register_zone()
        self.wait(1.2)
        self.assertEqual(self.engine.tracker.state, TrackerState.ARMED)

    def test_readdress_cancels_without_result(self):
        self.arm()
        for x in (305, 310, 315):  # 15px = 약 70mm (< 100mm) 움직이고 멈춤
            self.step(ball=(x, 400))
        self.wait(1.0)
        self.assertEqual(self.events("TRACK", "START")[-1]["event"], "START")
        self.assertEqual(len(self.events("TRACK", "CANCEL")), 1)
        self.assertEqual(self.events("TRACK", "CANCEL")[0]["reason"], "READDRESS")
        self.assertEqual(self.events("RESULT"), [])
        self.assertEqual(self.finished, [])
        self.assertEqual(self.engine.tracker.state, TrackerState.ARMED)  # 멈춘 자리에서 다시 준비
        self.assertEqual(self.engine.tracker.start_position, (315, 400))
        self.assertIn("자리 고쳐 놓기", self.engine.auto_guide_text(self.clock))

    def putt(self, end=(700, 400)):
        x = START[0]
        while x < end[0]:
            x = min(end[0], x + 40)
            self.step(ball=(x, end[1]))
        self.wait(1.0)
        self.assertEqual(self.engine.tracker.state, TrackerState.FINISHED)

    def test_normal_putt_sends_result_and_saves(self):
        self.arm()
        self.putt()
        self.assertEqual(len(self.events("RESULT")), 1)
        self.assertEqual(self.events("RESULT")[0]["reason"], "STOP")
        self.assertEqual(len(self.engine.storage.list_results()), 1)  # 자동 저장

    def test_next_putt_after_replacing_ball(self):
        self.arm()
        self.putt()
        old_points = len(self.engine.tracker.trajectory)
        self.camera.ball = None  # 공을 집어서
        self.wait(0.5)
        self.camera.ball = START  # 시작 구역에 다시 놓음
        self.wait(1.2)
        tracker = self.engine.tracker
        self.assertEqual(tracker.state, TrackerState.ARMED)
        self.assertEqual(len(tracker.trajectory), old_points)  # 이전 궤적은 아직 화면에 남아 있음
        self.assertEqual(tracker.finish_reason, STOP_REASON)
        self.step(ball=(START[0] + 30, START[1]))  # 다음 퍼팅 시작 -> 이전 궤적은 지워짐
        self.assertEqual(tracker.state, TrackerState.TRACKING)
        self.assertEqual(len(tracker.trajectory), 2)
        self.assertIsNone(tracker.finish_reason)

    def test_short_putt_ending_in_zone_does_not_rearm_by_itself(self):
        self.arm()
        self.putt(end=(340, 400))  # 40px = 약 180mm: 퍼팅이지만 시작 구역 안에서 멈춤
        self.assertEqual(len(self.events("RESULT")), 1)
        self.wait(3.0)
        self.assertEqual(self.engine.tracker.state, TrackerState.FINISHED)  # 그대로 두면 다음 준비 안 함
        self.camera.ball = None
        self.wait(0.5)
        self.camera.ball = START
        self.wait(1.2)
        self.assertEqual(self.engine.tracker.state, TrackerState.ARMED)

    def test_armed_short_occlusion_kept_and_long_absence_resets(self):
        self.arm()
        self.camera.ball = None
        self.wait(1.0)
        self.camera.ball = START
        self.step()
        self.assertEqual(self.engine.tracker.state, TrackerState.ARMED)  # 퍼터가 잠깐 가림: 유지
        self.camera.ball = None
        self.wait(3.3)
        self.assertEqual(self.engine.tracker.state, TrackerState.READY)  # 공을 집어 감
        self.assertIsNone(self.engine.tracker.start_position)

    def test_abort_button_has_priority(self):
        self.arm()
        self.assertTrue(self.engine.abort())
        self.wait(2.0)
        self.assertEqual(self.engine.tracker.state, TrackerState.READY)  # 공이 그대로면 다시 준비하지 않음

    def test_clear_zone(self):
        self.register_zone()
        self.engine.set_start_zone(None)
        self.assertIsNone(self.saved_auto()["start_zone"])
        self.engine.tracker.reset_trajectory()
        self.assertTrue(self.engine.register_start() == "OK")  # 다시 등록하면 새로 저장
        self.assertIsNotNone(self.saved_auto()["start_zone"])

    def test_protocol_auto_command_and_hello(self):
        self.register_zone()
        handler = self.engine.protocol_handler
        replies = []
        handler._send_to = lambda client, message: replies.append(message)
        handler.handle({"cmd": "AUTO", "enabled": False}, "A")
        self.assertEqual(replies[-1], {"cmd": "AUTO", "result": "OK", "enabled": False, "start_zone": handler._zone_dict()})
        self.assertFalse(self.saved_auto()["enabled"])
        handler.handle({"cmd": "AUTO", "enabled": "yes"}, "A")
        self.assertEqual(replies[-1]["result"], "NG")
        handler.handle({"cmd": "AUTO", "enabled": True}, "A")
        replies.clear()
        handler.on_client_connected("A")
        hello = replies[0]
        self.assertEqual(hello["version"], "2.5")
        self.assertTrue(hello["auto"])
        self.assertIsNotNone(hello["start_zone"]["x_mm"])

    def test_auto_disabled_keeps_old_behavior(self):
        self.engine.set_auto_enabled(False)
        self.engine.tracker.state = TrackerState.IDLE
        self.wait(2.0)
        self.assertEqual(self.engine.tracker.state, TrackerState.IDLE)


class RimAndMatSearchTests(EngineTestCase):
    """0절 보완: 컵에 붙어 멈춘 공 / 매트 영역만 검색."""

    settings_overrides = {
        "hole": {"enabled": True, "capture_radius_mm": 400, "confirm_duration": 0.5, "min_area_ratio": 1.3,
                 "stuck_ball_area_fraction": 0.5}
    }

    def setUp(self):
        super().setUp()
        self.camera.ball = START
        self.camera.cups = [(800, 400)]
        self.step()
        self.assertEqual(self.engine.align_corners([list(c) for c in MAT_CORNERS]), "OK")
        for _ in range(SCAN_STEPS):
            self.step()
        self.assertEqual(self.engine.register_start(), "OK")
        for _ in range(SCAN_STEPS):
            self.step()
        self.engine.start_tracking()
        for _ in range(3):
            self.step()

    def test_ball_stuck_to_cup_is_stop_not_hole_in(self):
        for x in range(340, 741, 40):
            self.step(ball=(x, 400))
        self.step(ball=(758, 400))  # 컵(반지름 26)에 붙음: 중심 거리 42 -> 한 덩어리로 보임
        for _ in range(20):
            self.step()
        tracker = self.engine.tracker
        self.assertEqual(tracker.finish_reason, STOP_REASON)
        self.assertIsNone(tracker.finish_hole)
        self.assertAlmostEqual(tracker.stop_position[0], 758, delta=6)  # 덩어리의 바깥쪽 끝에서 공 반경만큼 안쪽
        self.assertAlmostEqual(tracker.stop_position[1], 400, delta=6)

    def test_ball_into_cup_is_still_hole_in(self):
        for x in range(340, 741, 40):
            self.step(ball=(x, 400))
        self.step(ball=(758, 400))  # 컵에 닿았다가
        self.camera.ball = None  # 컵에 들어감
        for _ in range(20):
            self.step()
        self.assertEqual(self.engine.tracker.finish_reason, HOLE_REASON)

    def test_full_search_is_limited_to_mat(self):
        self.engine.reset()
        self.camera.ball = None
        self.step()
        plan = self.frames[-1].search_plan
        self.assertEqual(plan.reason, "mat")
        x, y, w, h = plan.roi
        self.assertLess(w * h, 1280 * 720 * 0.7)  # 화면 전체보다 확실히 작다
        self.assertLessEqual(x, min(c[0] for c in MAT_CORNERS))
        self.assertGreaterEqual(x + w, max(c[0] for c in MAT_CORNERS))


if __name__ == "__main__":
    unittest.main()
