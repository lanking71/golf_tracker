"""
VisionEngine 테스트 (진짜 카메라 없이 가짜 카메라로 실행).

실행 (프로젝트 루트, 가상환경을 켠 상태에서):
    python -m unittest discover -s tests -v
"""

import unittest

from src.engine import (
    SAVE_NO_TRAJECTORY,
    SAVE_NOT_CALIBRATED,
    SAVE_OK,
    START_NO_BALL,
    START_OK,
    START_OUTSIDE_MAT,
)
from src.protocol_handler import ProtocolHandler
from src.tracker import STOP_REASON, TrackerState
from tests.base import EngineTestCase
from tests.fake_camera import MAT_CORNERS


class FrameProcessingTests(EngineTestCase):
    def test_detects_ball_and_emits_frame(self):
        self.step()
        detection = self.engine.last_detection
        self.assertIsNotNone(detection)
        self.assertAlmostEqual(detection.x, 500, delta=2)
        self.assertAlmostEqual(detection.y, 400, delta=2)
        self.assertEqual(len(self.frames), 1)
        self.assertIs(self.frames[0].detection, detection)
        self.assertFalse(self.frames[0].outside_mat)

    def test_no_ball_gives_none(self):
        self.camera.ball = None  # 화면에 공이 없다
        self.step()
        self.assertIsNone(self.engine.last_detection)
        self.assertIsNone(self.frames[-1].detection)

    def test_camera_loss_and_recovery(self):
        self.step()
        self.assertIsNotNone(self.engine.last_raw_frame)

        self.camera.available = False
        self.camera.release()
        self.step()
        self.assertEqual(len(self.camera_lost), 1)
        self.assertIsNone(self.engine.last_detection)
        # 오래된 프레임으로 캘리브레이션하지 않도록 원본도 비워진다
        self.assertIsNone(self.engine.last_raw_frame)

        self.camera.available = True
        self.step()
        self.assertIsNotNone(self.engine.last_detection)

    def test_detection_paused_skips_tracking(self):
        self.engine.detection_paused = True
        self.step()
        self.assertTrue(self.frames[-1].paused)
        self.assertIsNone(self.engine.last_detection)

        self.engine.detection_paused = False
        self.step()
        self.assertFalse(self.frames[-1].paused)
        self.assertIsNotNone(self.engine.last_detection)


class CalibrationTests(EngineTestCase):
    def test_align_auto_success(self):
        self.step()
        self.assertEqual(self.engine.align_auto(4500, 2000), "OK")
        self.assertTrue(self.engine.calibration.is_calibrated())
        self.assertEqual(self.engine.tracker.state, TrackerState.CALIBRATING)
        # 매트 우하단 모서리는 (4500, 2000)mm 근처여야 한다
        x_mm, y_mm = self.engine.calibration.pixel_to_real(*MAT_CORNERS[2])
        self.assertAlmostEqual(x_mm, 4500, delta=80)
        self.assertAlmostEqual(y_mm, 2000, delta=80)

    def test_align_auto_without_frame(self):
        self.assertEqual(self.engine.align_auto(), "no_frame")

    def test_align_auto_bad_size_keeps_state(self):
        self.step()
        self.assertEqual(self.engine.align_auto(-5, 2000), "bad_size")
        self.assertFalse(self.engine.calibration.is_calibrated())
        self.assertEqual(self.engine.tracker.state, TrackerState.IDLE)

    def test_align_auto_mat_not_found_keeps_existing_calibration(self):
        self.step()
        self.assertEqual(self.engine.align_auto(4500, 2000), "OK")
        before = self.engine.calibration.corners[:]

        self.camera.show_mat = False
        self.step()
        self.assertEqual(self.engine.align_auto(3000, 1500), "mat_not_found")
        # 실패했으니 기존 캘리브레이션과 크기는 그대로여야 한다
        self.assertEqual(self.engine.calibration.corners, before)
        self.assertEqual(self.engine.calibration.mat_width_mm, 4500)

    def test_start_calibration_button_flow(self):
        """23단계: 기본(auto_detect 꺼짐)은 자동 검출 없이 바로 수동 4점 클릭 모드다.

        자동 검출을 켠 경우의 흐름은 tests/test_manual_calibration.py에서 확인한다.
        """
        self.step()
        self.assertEqual(self.engine.start_calibration(), "manual")
        self.assertEqual(self.engine.tracker.state, TrackerState.CALIBRATING)
        self.assertEqual(self.engine.calibration.corners, [])
        self.assertFalse(self.engine.calibration.is_calibrated())
        self.assertIsNone(self.engine.calibration_proposal)

    def test_manual_corner_clicks(self):
        self.engine.tracker.calibrate()
        for x, y in MAT_CORNERS:
            self.assertTrue(self.engine.add_calibration_corner(x, y))
        self.assertTrue(self.engine.calibration.is_calibrated())
        # 4점을 다 찍은 뒤에는 더 등록되지 않는다
        self.assertFalse(self.engine.add_calibration_corner(1, 1))

    def test_corner_click_ignored_unless_calibrating(self):
        self.assertFalse(self.engine.add_calibration_corner(100, 100))

    def test_reload_settings_replaces_calibration(self):
        old = self.engine.calibration
        self.engine.reload_settings()
        self.assertIsNot(self.engine.calibration, old)


class StartPositionTests(EngineTestCase):
    def test_register_start_ok(self):
        self.step()
        self.assertEqual(self.engine.register_start(), START_OK)
        self.assertEqual(self.engine.tracker.state, TrackerState.READY)
        x, y = self.engine.tracker.start_position
        self.assertAlmostEqual(x, 500, delta=2)

    def test_register_start_no_ball(self):
        self.camera.ball = None  # 화면에 공이 없다
        self.step()
        self.assertEqual(self.engine.register_start(), START_NO_BALL)
        self.assertEqual(self.engine.tracker.state, TrackerState.IDLE)

    def test_register_start_outside_mat(self):
        self.step()
        self.engine.align_auto(4500, 2000)
        # 26단계: 캘리브레이션 후에는 매트 영역만 찾으므로, 매트에서 멀리 떨어진 공은 아예 안 보인다
        self.step(ball=(1200, 100))  # 매트 밖
        self.assertEqual(self.engine.register_start(), START_NO_BALL)
        # 검색 영역 제한을 끄면 예전처럼 화면 전체를 찾아서 '매트 밖이라 무시'로 알려 준다
        self.engine.search.enabled = False
        self.step()
        self.assertEqual(self.engine.register_start(), START_OUTSIDE_MAT)
        self.assertTrue(self.frames[-1].outside_mat)
        self.assertIsNotNone(self.frames[-1].raw_detection)


class TrackingFlowTests(EngineTestCase):
    def prepare_ready(self):
        self.step()
        self.assertEqual(self.engine.align_auto(4500, 2000), "OK")
        self.assertEqual(self.engine.register_start(), START_OK)
        self.assertTrue(self.engine.start_tracking())
        # '추적 준비' 직후에는 추적 대기(ARMED). 공이 움직여야 TRACKING이 된다.
        self.assertEqual(self.engine.tracker.state, TrackerState.ARMED)

    def roll_and_stop(self):
        """공을 오른쪽으로 굴린 뒤 가만히 두어 '정지 감지'까지 진행한다."""
        for i in range(1, 11):
            self.step(ball=(500 + i * 20, 400))
        for _ in range(20):  # 가만히 (정지 판정 0.5초 > 20프레임 x 0.05초)
            self.step()

    def test_rolling_ball_finishes_with_stop(self):
        self.prepare_ready()
        self.roll_and_stop()
        self.assertEqual(self.finished, [STOP_REASON])
        self.assertEqual(self.engine.tracker.state, TrackerState.FINISHED)
        self.assertGreaterEqual(len(self.engine.tracker.trajectory), 5)
        self.assertGreater(len(self.trajectory_updates), 0)

    def test_stats_and_save(self):
        self.prepare_ready()
        self.roll_and_stop()
        current = self.engine.current_stats()
        self.assertIsNotNone(current)
        stats = current[0]
        self.assertGreater(stats.total_distance, 100)  # mm

        status, result_id = self.engine.save_result()
        self.assertEqual(status, SAVE_OK)
        self.assertIsNotNone(self.engine.storage.get_result(result_id))

    def test_save_not_calibrated(self):
        self.assertEqual(self.engine.save_result(), (SAVE_NOT_CALIBRATED, None))

    def test_save_without_trajectory(self):
        self.step()
        self.engine.align_auto(4500, 2000)
        self.assertEqual(self.engine.save_result(), (SAVE_NO_TRAJECTORY, None))


class ArmedFlowTests(EngineTestCase):
    """ADD 후 추적 대기(ARMED) -> 공이 움직이면 자동 추적 시작."""

    def setUp(self):
        super().setUp()
        # 엔진에 연결된 실제 핸들러가 보내는 메시지를 모은다 (테스트용으로 보내는 함수만 교체)
        self.sent = []
        self.engine.protocol_handler._send = self.sent.append
        self.started = []
        self.engine.tracking_started.connect(lambda: self.started.append(1))

        self.step()
        self.assertEqual(self.engine.align_auto(4500, 2000), "OK")
        self.assertEqual(self.engine.register_start(), START_OK)
        self.assertTrue(self.engine.start_tracking())
        self.sent.clear()

    def test_waiting_ball_does_not_finish(self):
        # 퍼팅 전에 공이 2초 동안 가만히 있다 (정지 판정 기준 0.5초의 4배)
        for _ in range(40):
            self.step()
        self.assertEqual(self.engine.tracker.state, TrackerState.ARMED)
        self.assertEqual(self.finished, [])
        self.assertEqual(self.started, [])
        self.assertEqual([m for m in self.sent if m["cmd"] == "TRACK"], [])

    def test_ball_lost_while_armed_is_ignored(self):
        self.camera.ball = None  # 퍼터·손이 공을 가려서 안 보인다
        for _ in range(60):  # 이탈 판정 기준(2초)을 넘는 3초
            self.step()
        self.assertEqual(self.engine.tracker.state, TrackerState.ARMED)
        self.assertEqual(self.finished, [])

    def test_small_jitter_does_not_start(self):
        self.step(ball=(503, 401))
        self.step(ball=(497, 399))
        self.assertEqual(self.engine.tracker.state, TrackerState.ARMED)

    def test_move_starts_tracking_with_event(self):
        for _ in range(10):
            self.step()
        t_still = self.clock  # 공이 마지막으로 가만히 있던 시각

        self.step(ball=(540, 400))  # 40px 이동

        self.assertEqual(self.engine.tracker.state, TrackerState.TRACKING)
        self.assertEqual(self.started, [1])
        self.assertEqual(self.engine.tracker.tracking_started_at, t_still)
        # 궤적은 출발 위치(마지막으로 가만히 있던 시각)와 현재 위치로 시작한다
        trajectory = self.engine.tracker.trajectory
        self.assertEqual(len(trajectory), 2)
        self.assertEqual(trajectory[0].timestamp, t_still)
        self.assertEqual((trajectory[0].x, trajectory[0].y), self.engine.tracker.start_position)

        commands = [m["cmd"] for m in self.sent]
        # ARMED로 들어간 직후 STATUS(ARMED)가 먼저 한 번 나간다 (상태가 바뀌었으므로)
        self.assertEqual(self.sent[0]["state"], "ARMED")
        track_index = commands.index("TRACK")
        track = self.sent[track_index]
        self.assertEqual(track, {"cmd": "TRACK", "event": "START", "ts": int(t_still * 1000)})
        # TRACK START 이벤트가 STATUS(TRACKING)보다 먼저 간다
        tracking_statuses = [
            i for i, m in enumerate(self.sent) if m["cmd"] == "STATUS" and m["state"] == "TRACKING"
        ]
        self.assertTrue(tracking_statuses)
        self.assertLess(track_index, tracking_statuses[0])
        status = self.sent[tracking_statuses[0]]
        self.assertAlmostEqual(status["t"], 0.05, places=3)  # 출발 시각을 0으로 센 경과 시간

    def test_full_putt_after_waiting(self):
        for _ in range(40):
            self.step()
        for i in range(1, 11):
            self.step(ball=(500 + i * 20, 400))
        for _ in range(20):
            self.step()
        self.assertEqual(self.finished, [STOP_REASON])
        self.assertEqual(self.engine.save_result()[0], SAVE_OK)
        results = [m for m in self.sent if m["cmd"] == "RESULT"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["reason"], "STOP")

    def test_abort_from_armed_returns_to_ready(self):
        self.assertTrue(self.engine.tracker.stop_tracking())
        self.assertEqual(self.engine.tracker.state, TrackerState.READY)
        # 다시 준비할 수 있다
        self.assertTrue(self.engine.start_tracking())
        self.assertEqual(self.engine.tracker.state, TrackerState.ARMED)


class ProtocolIntegrationTests(EngineTestCase):
    """앱 명령(핸들러)이 엔진과 같은 규칙으로 동작하는지 확인한다."""

    def setUp(self):
        super().setUp()
        self.sent = []
        self.changes = []
        self.handler = ProtocolHandler(self.engine, send=self.sent.append, settings={"status_hz": 30})
        self.engine.changed.connect(lambda: self.changes.append(1))

    def last(self):
        return self.sent[-1]

    def test_command_sequence(self):
        self.step()
        self.handler.handle({"cmd": "ALIGN", "type": "REQ", "width_mm": 4500, "height_mm": 2000})
        align = self.last()
        self.assertEqual((align["cmd"], align["result"], align["method"]), ("ALIGN", "OK", "auto"))
        self.assertEqual((align["width_mm"], align["height_mm"]), (4500, 2000))
        self.assertEqual(len(align["corners"]), 4)  # 실제로 쓰인 모서리(픽셀)

        self.handler.handle({"cmd": "INIT", "type": "BALL"})
        self.assertEqual(self.last()["result"], "OK")
        self.assertEqual(self.engine.tracker.state, TrackerState.READY)

        self.handler.handle({"cmd": "ADD", "type": "BALL"})
        self.assertEqual(self.last(), {"cmd": "ADD", "result": "OK"})
        self.assertEqual(self.engine.tracker.state, TrackerState.ARMED)
        # 상태를 바꾼 명령마다 화면에 알리는 시그널이 나갔다 (ALIGN, INIT, ADD)
        self.assertEqual(len(self.changes), 3)

    def test_failures(self):
        self.handler.handle({"cmd": "INIT", "type": "BALL"})
        self.assertEqual(self.last()["reason"], "not_calibrated")

        self.step()
        self.handler.handle({"cmd": "ADD", "type": "BALL"})
        self.assertEqual(self.last()["reason"], "bad_state")

        self.camera.show_mat = False
        self.step()
        self.handler.handle({"cmd": "ALIGN", "type": "REQ"})
        self.assertEqual(self.last()["reason"], "mat_not_found")

        self.handler.handle({"cmd": "NOPE"})
        self.assertEqual(self.last()["reason"], "unknown_cmd")
        self.assertEqual(self.changes, [])  # 실패한 명령은 화면 갱신을 요청하지 않는다

    def test_handler_follows_reloaded_calibration(self):
        self.step()
        self.engine.reload_settings()
        self.assertIs(self.handler.calibration, self.engine.calibration)

    def test_finish_sends_status_then_result(self):
        self.step()
        self.handler.handle({"cmd": "ALIGN", "type": "REQ"})
        self.handler.handle({"cmd": "INIT", "type": "BALL"})
        self.handler.handle({"cmd": "ADD", "type": "BALL"})
        # 엔진에 연결된 실제 핸들러가 아닌, 테스트 핸들러로 프레임 결과를 흘려 보낸다.
        for i in range(1, 11):
            self.step(ball=(500 + i * 20, 400))
            self.handler.on_frame(self.engine.last_detection, self.clock)
        for _ in range(20):
            self.step()
            self.handler.on_frame(self.engine.last_detection, self.clock)
        self.handler.on_finished(STOP_REASON)

        commands = [m["cmd"] for m in self.sent]
        self.assertIn("STATUS", commands)
        result = self.last()
        self.assertEqual(result["cmd"], "RESULT")
        self.assertEqual(result["reason"], "STOP")
        self.assertGreater(result["distance_mm"], 100)


if __name__ == "__main__":
    unittest.main()
