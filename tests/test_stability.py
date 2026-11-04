"""
운영 안정성 테스트: 카메라 끊김·재연결, 오류 격리, 상태 요약 로그.

화면 없이 오래 돌아가는 엔진이 "카메라를 뽑아도, 코드에 오류가 나도 죽지 않고
메인 컨트롤에 상황을 알려 주는지"를 확인한다.
"""

import unittest

from src.tracker import LOST_REASON, TrackerState
from tests.base import EngineTestCase


class StabilityTestCase(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.sent = []  # 엔진이 컨트롤에 보낸 메시지
        self.engine.protocol_handler._send = self.sent.append
        self.camera_events = []
        self.engine.camera_status_changed.connect(self.camera_events.append)

    def cameras(self):
        """보낸 CAMERA 이벤트 목록 (OK/LOST)"""
        return [m["event"] for m in self.sent if m["cmd"] == "CAMERA"]


class CameraRetryTests(StabilityTestCase):
    def test_reconnect_attempts_are_throttled(self):
        self.engine.camera_retry_interval = 1.0
        self.camera.available = False  # 카메라가 없다
        for _ in range(3):  # 0.75초 동안
            self.step(dt=0.25)
        self.assertEqual(self.camera.open_calls, 1)  # 처음 한 번만 시도한다

        for _ in range(11):  # 이어서 2.75초 더 (총 3.5초)
            self.step(dt=0.25)
        # 약 1초마다 시도: 0.25초, 1.25초, 2.25초, 3.25초 시점
        self.assertEqual(self.camera.open_calls, 4)
        self.assertEqual(len(self.camera_lost), 4)  # 화면 알림도 시도할 때만

    def test_recovers_once_interval_has_passed(self):
        self.engine.camera_retry_interval = 1.0
        self.camera.available = False
        self.step(dt=0.25)  # 시도 1 (실패)
        self.camera.available = True  # 카메라를 다시 꽂았다
        self.step(dt=0.25)
        self.step(dt=0.25)
        self.assertFalse(self.engine.camera_ok)  # 아직 재시도할 때가 아니다
        for _ in range(3):
            self.step(dt=0.25)  # 1.25초 시점에 재시도
        self.assertTrue(self.engine.camera_ok)
        self.assertIsNotNone(self.engine.last_detection)

    def test_read_failure_also_waits_before_reconnecting(self):
        self.engine.camera_retry_interval = 1.0
        self.step(dt=0.25)  # 정상 연결 (open 1번)
        self.assertEqual(self.camera.open_calls, 1)
        self.camera.available = False  # 영상을 읽는 도중 끊김
        self.step(dt=0.25)  # 읽기 실패 -> 장치 반납
        for _ in range(3):
            self.step(dt=0.25)
        self.assertEqual(self.camera.open_calls, 1)  # 곧바로 다시 열려고 매달리지 않는다
        self.step(dt=0.25)
        self.assertEqual(self.camera.open_calls, 2)  # 1초 뒤 재시도


class CameraEventTests(StabilityTestCase):
    def test_events_only_when_status_changes(self):
        for _ in range(5):
            self.step()
        self.assertEqual(self.cameras(), ["OK"])  # 영상을 받기 시작한 순간 한 번
        self.assertEqual(self.camera_events, [True])

        self.camera.available = False
        self.camera.release()
        for _ in range(5):
            self.step()
        self.assertEqual(self.cameras(), ["OK", "LOST"])  # 끊긴 순간 한 번 (반복 안 함)

        self.camera.available = True
        for _ in range(5):
            self.step()
        self.assertEqual(self.cameras(), ["OK", "LOST", "OK"])
        self.assertEqual(self.camera_events, [True, False, True])

    def test_camera_never_available(self):
        self.camera.available = False
        for _ in range(5):
            self.step()
        self.assertEqual(self.cameras(), ["LOST"])  # 처음부터 안 되면 LOST를 한 번 알린다
        self.assertFalse(self.engine.camera_ok)

    def test_status_reports_camera_state(self):
        self.step()
        self.assertEqual(self.engine.protocol_handler._build_status(self.clock)["camera"], "OK")
        self.camera.available = False
        self.camera.release()
        self.step()
        self.assertEqual(self.engine.protocol_handler._build_status(self.clock)["camera"], "LOST")


class CameraLossDuringTrackingTests(StabilityTestCase):
    def start_tracking(self):
        self.step()
        self.assertEqual(self.engine.align_auto(4500, 2000), "OK")
        self.assertEqual(self.engine.register_start(), "OK")
        self.assertTrue(self.engine.start_tracking())
        self.step(ball=(560, 400))  # 공이 움직여 TRACKING
        self.assertEqual(self.engine.tracker.state, TrackerState.TRACKING)
        self.sent.clear()

    def test_tracking_ends_with_lost_when_camera_stays_down(self):
        self.start_tracking()
        self.camera.available = False
        self.camera.release()
        for _ in range(8):  # 0.5초씩 4초 (공이 안 보이는 시간 기준 lost_duration 2초)
            self.step(dt=0.5)

        self.assertEqual(self.engine.tracker.state, TrackerState.FINISHED)
        self.assertEqual(self.finished, [LOST_REASON])
        commands = [(m["cmd"], m.get("state") or m.get("reason")) for m in self.sent]
        # 컨트롤이 영원히 기다리지 않도록 STATUS(FINISHED) 다음에 RESULT(LOST)가 나간다
        self.assertLess(commands.index(("STATUS", "FINISHED")), commands.index(("RESULT", "LOST")))
        self.assertEqual(self.cameras(), ["LOST"])

    def test_short_camera_gap_keeps_tracking(self):
        self.start_tracking()
        self.camera.available = False
        self.camera.release()
        self.step(dt=0.5)
        self.step(dt=0.5)  # 1초 끊김 (lost_duration 2초 미만)
        self.camera.available = True
        self.step(ball=(600, 400), dt=0.5)
        self.assertEqual(self.engine.tracker.state, TrackerState.TRACKING)
        self.assertEqual(self.finished, [])

    def test_camera_loss_while_armed_changes_nothing(self):
        self.step()
        self.engine.align_auto(4500, 2000)
        self.engine.register_start()
        self.engine.start_tracking()
        self.camera.available = False
        self.camera.release()
        for _ in range(20):
            self.step(dt=0.5)
        self.assertEqual(self.engine.tracker.state, TrackerState.ARMED)
        self.assertEqual(self.finished, [])


class ErrorIsolationTests(StabilityTestCase):
    def break_tracker(self):
        def boom(*args, **kwargs):
            raise RuntimeError("테스트용 오류")

        self.engine.tracker.update_detection = boom

    def test_error_in_a_frame_does_not_stop_the_engine(self):
        self.step()
        self.break_tracker()
        with self.assertLogs("engine", level="ERROR") as logs:
            self.step()  # 예외가 밖으로 나오면 이 줄에서 테스트가 실패한다
        self.assertEqual(self.engine.error_count, 1)
        self.assertIn("프레임 처리 중 오류", logs.output[0])
        self.assertIn("RuntimeError", logs.output[0])  # 원인(트레이스백)이 로그에 남는다

        del self.engine.tracker.update_detection  # 고쳐졌다고 치고 원래 함수로 되돌린다
        frames_before = len(self.frames)
        self.step()
        self.assertEqual(len(self.frames), frames_before + 1)  # 다음 프레임은 정상 처리

    def test_repeated_errors_do_not_flood_the_log(self):
        self.step()
        self.break_tracker()
        with self.assertLogs("engine", level="ERROR") as logs:
            for _ in range(20):  # 1초 동안 같은 오류가 20번
                self.step(dt=0.05)
        self.assertEqual(self.engine.error_count, 20)
        self.assertEqual(len(logs.records), 1)  # 로그는 한 번만

        with self.assertLogs("engine", level="ERROR") as later:
            self.step(dt=6.0)  # 일정 시간(5초)이 지나면 다시 남는다
        self.assertEqual(len(later.records), 1)

    def test_error_frames_are_not_counted_as_processed(self):
        self.step()
        self.break_tracker()
        with self.assertLogs("engine", level="ERROR"):
            self.step()
        summary = self.engine.log_stats()
        self.assertEqual(summary["frames"], 1)  # 오류 난 프레임은 제외
        self.assertEqual(summary["errors"], 1)


class StatsLogTests(StabilityTestCase):
    def test_summary_values_and_reset(self):
        for _ in range(10):
            self.step()
        with self.assertLogs("engine", level="INFO") as logs:
            summary = self.engine.log_stats()
        self.assertEqual(summary["frames"], 10)
        self.assertGreater(summary["fps"], 0)
        self.assertGreater(summary["avg_ms"], 0)
        self.assertGreaterEqual(summary["max_ms"], summary["avg_ms"])
        self.assertIn("상태 요약", logs.output[0])
        self.assertIn("카메라 OK", logs.output[0])

        again = self.engine.log_stats()  # 요약을 남기면 집계가 비워진다
        self.assertEqual(again["frames"], 0)
        self.assertEqual(again["avg_ms"], 0.0)

    def test_summary_without_frames(self):
        self.camera.available = False
        self.step()
        with self.assertLogs("engine", level="INFO") as logs:
            summary = self.engine.log_stats()
        self.assertEqual(summary["frames"], 0)
        self.assertIn("카메라 LOST", logs.output[0])


class InternalErrorReplyTests(StabilityTestCase):
    def test_command_crash_returns_internal_error_and_engine_survives(self):
        handler = self.engine.protocol_handler
        replies = []
        handler._send_to = lambda client, message: replies.append(message)

        def boom(message):
            raise RuntimeError("명령 처리 오류")

        handler._cmd_ping = boom
        with self.assertLogs("protocol", level="ERROR"):
            handler.handle({"cmd": "PING", "id": 7}, client="A")
        self.assertEqual(replies, [{"cmd": "ERROR", "reason": "internal_error", "received": "PING", "id": 7}])

        del handler._cmd_ping  # 원래 메서드로 되돌린다
        replies.clear()
        handler.handle({"cmd": "PING", "id": 8}, client="A")  # 다음 명령은 정상 처리
        self.assertEqual(replies[0]["cmd"], "PONG")
        self.assertEqual(replies[0]["id"], 8)


if __name__ == "__main__":
    unittest.main()
