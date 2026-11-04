"""
프로토콜 v2 핸들러 테스트 (가짜 카메라 + 엔진, 소켓 없이 메시지만 확인).

- 응답은 요청한 앱에게만, 이벤트는 모두에게
- 요청 id 되돌려주기
- HELLO / STATUS 필드 / ABORT / RESET / GET_RESULT / PING
- RESULT의 궤적 점 [x, y, t, dist, speed]
"""

import unittest

from src.config import load_settings
from src.engine import START_OK
from src.protocol_handler import PROTOCOL_VERSION
from src.tracker import TrackerState
from tests.base import EngineTestCase
from tests.fake_camera import MAT_CORNERS


class HandlerTestCase(EngineTestCase):
    """엔진에 연결된 실제 핸들러가 보내는 메시지를 모아서 확인한다."""

    def setUp(self):
        super().setUp()
        self.handler = self.engine.protocol_handler
        self.broadcast = []  # 접속한 모든 앱에 간 메시지
        self.direct = []  # (client, 메시지): 특정 앱에만 간 메시지
        self.handler._send = self.broadcast.append
        self.handler._send_to = lambda client, message: self.direct.append((client, message))

    def ask(self, message, client="A"):
        """명령을 보내고 그 앱에게 돌아온 응답 1개를 돌려준다."""
        self.direct.clear()
        self.handler.handle(message, client)
        self.assertEqual(len(self.direct), 1, f"응답이 1개여야 함: {self.direct}")
        self.assertEqual(self.direct[0][0], client)
        return self.direct[0][1]

    def prepare_ready(self):
        """매트 정렬 + 시작점 등록까지 마친 READY 상태로 만든다."""
        self.step()
        self.assertEqual(self.ask({"cmd": "ALIGN"})["result"], "OK")
        self.assertEqual(self.ask({"cmd": "INIT", "type": "BALL"})["result"], "OK")
        self.assertEqual(self.engine.tracker.state, TrackerState.READY)

    def run_putt(self, wait_frames=20):
        """ADD 후 잠시 기다렸다가 공을 굴려서 멈출 때까지 진행한다."""
        self.assertEqual(self.ask({"cmd": "ADD", "type": "BALL"})["result"], "OK")
        for _ in range(wait_frames):
            self.step()
        for i in range(1, 11):
            self.step(ball=(500 + i * 20, 400))
        for _ in range(20):
            self.step()
        self.assertEqual(self.engine.tracker.state, TrackerState.FINISHED)

    def last_result_event(self):
        results = [m for m in self.broadcast if m["cmd"] == "RESULT"]
        self.assertEqual(len(results), 1)
        return results[0]


class RoutingAndIdTests(HandlerTestCase):
    def test_reply_goes_only_to_requester(self):
        reply = self.ask({"cmd": "PING"}, client="A")
        self.assertEqual(reply["cmd"], "PONG")
        self.assertEqual(self.broadcast, [])  # 다른 앱에는 가지 않는다

    def test_without_client_reply_is_broadcast(self):
        self.handler.handle({"cmd": "PING"})
        self.assertEqual([m["cmd"] for m in self.broadcast], ["PONG"])
        self.assertEqual(self.direct, [])

    def test_id_is_echoed_on_ok_ng_and_error(self):
        self.assertEqual(self.ask({"cmd": "PING", "id": "abc"})["id"], "abc")
        # 실패(NG) 응답에도 붙는다
        self.step()
        ng = self.ask({"cmd": "ADD", "type": "BALL", "id": 5})
        self.assertEqual((ng["result"], ng["id"]), ("NG", 5))
        # 모르는 명령(ERROR)에도 붙고, 0처럼 '거짓' 값도 그대로 돌려준다
        error = self.ask({"cmd": "NOPE", "id": 0})
        self.assertEqual((error["reason"], error["id"]), ("unknown_cmd", 0))

    def test_no_id_means_no_id_field(self):
        self.assertNotIn("id", self.ask({"cmd": "PING"}))

    def test_id_does_not_leak_into_events(self):
        self.step()
        self.ask({"cmd": "ALIGN", "id": 1})
        self.step()  # 상태가 바뀐 뒤 첫 프레임: 이벤트(STATUS)가 모두에게 간다
        self.assertTrue(self.broadcast)
        self.assertTrue(all("id" not in m for m in self.broadcast))

    def test_state_events_are_broadcast(self):
        self.step()
        self.assertIn("STATUS", [m["cmd"] for m in self.broadcast])
        self.assertEqual(self.direct, [])


class HelloAndStatusTests(HandlerTestCase):
    def test_hello_then_status_to_new_client_only(self):
        self.step()
        self.broadcast.clear()
        self.handler.on_client_connected("NEW")

        self.assertEqual(self.broadcast, [])
        self.assertEqual([c for c, _ in self.direct], ["NEW", "NEW"])
        hello, status = self.direct[0][1], self.direct[1][1]
        self.assertEqual(hello["cmd"], "HELLO")
        self.assertEqual(hello["version"], PROTOCOL_VERSION)
        self.assertEqual(hello["state"], "IDLE")
        self.assertEqual(hello["camera"], "OK")
        self.assertIn("calibrated", hello)
        self.assertEqual(status["cmd"], "STATUS")

    def test_hello_reports_camera_lost_before_first_frame(self):
        self.handler.on_client_connected("NEW")
        self.assertEqual(self.direct[0][1]["camera"], "LOST")

    def test_status_has_ts_frame_and_camera(self):
        for _ in range(3):
            self.step()
        status = self.ask({"cmd": "STATUS"})
        self.assertEqual(status["camera"], "OK")
        self.assertEqual(status["frame"], 3)
        self.assertIsInstance(status["ts"], int)
        self.assertGreater(status["ts"], 1_600_000_000_000)  # epoch 밀리초

    def test_status_camera_lost(self):
        self.step()
        self.camera.available = False
        self.camera.release()
        self.step()
        self.assertEqual(self.ask({"cmd": "STATUS"})["camera"], "LOST")

    def test_ping_returns_pong_with_time(self):
        pong = self.ask({"cmd": "PING"})
        self.assertEqual(pong["cmd"], "PONG")
        self.assertIsInstance(pong["ts"], int)


class AbortResetTests(HandlerTestCase):
    def test_abort_from_armed_keeps_start_point(self):
        self.prepare_ready()
        self.assertEqual(self.ask({"cmd": "ADD", "type": "BALL"})["result"], "OK")
        self.assertEqual(self.engine.tracker.state, TrackerState.ARMED)

        self.assertEqual(self.ask({"cmd": "ABORT"}), {"cmd": "ABORT", "result": "OK"})
        self.assertEqual(self.engine.tracker.state, TrackerState.READY)
        self.assertIsNotNone(self.engine.tracker.start_position)
        # 다시 대기할 수 있다
        self.assertEqual(self.ask({"cmd": "ADD", "type": "BALL"})["result"], "OK")

    def test_abort_from_tracking(self):
        self.prepare_ready()
        self.ask({"cmd": "ADD", "type": "BALL"})
        self.step(ball=(560, 400))  # 움직여서 TRACKING
        self.assertEqual(self.engine.tracker.state, TrackerState.TRACKING)
        self.assertEqual(self.ask({"cmd": "ABORT"})["result"], "OK")
        self.assertEqual(self.engine.tracker.state, TrackerState.READY)

    def test_abort_bad_state(self):
        reply = self.ask({"cmd": "ABORT"})  # IDLE
        self.assertEqual((reply["result"], reply["reason"]), ("NG", "bad_state"))
        self.prepare_ready()
        self.assertEqual(self.ask({"cmd": "ABORT"})["reason"], "bad_state")  # READY

    def test_reset_clears_start_and_returns_to_ready(self):
        self.prepare_ready()
        self.assertEqual(self.ask({"cmd": "RESET"}), {"cmd": "RESET", "result": "OK"})
        self.assertEqual(self.engine.tracker.state, TrackerState.READY)
        self.assertIsNone(self.engine.tracker.start_position)
        # 캘리브레이션은 남아 있어서 바로 다시 시작점을 등록할 수 있다
        self.assertEqual(self.engine.register_start(), START_OK)

    def test_reset_bad_state_when_idle(self):
        self.assertEqual(self.ask({"cmd": "RESET"})["reason"], "bad_state")

    def test_commands_notify_ui(self):
        changes = []
        self.engine.changed.connect(lambda: changes.append(1))
        self.prepare_ready()  # ALIGN, INIT
        self.ask({"cmd": "ADD", "type": "BALL"})
        self.ask({"cmd": "ABORT"})
        self.ask({"cmd": "RESET"})
        self.assertEqual(len(changes), 5)
        self.assertEqual(self.ask({"cmd": "ABORT"})["result"], "NG")  # READY에서는 실패
        self.assertEqual(len(changes), 5)  # 실패한 명령은 화면 갱신을 요청하지 않는다


class ResultTests(HandlerTestCase):
    def test_result_event_has_points(self):
        self.prepare_ready()
        self.run_putt()
        result = self.last_result_event()

        self.assertEqual(result["reason"], "STOP")
        self.assertEqual(result["fields"], ["x", "y", "t", "dist", "speed"])
        points = result["points"]
        self.assertGreaterEqual(len(points), 5)
        self.assertTrue(all(len(p) == 5 for p in points))
        # 첫 점: 출발 위치, 시간·거리·속도 0
        self.assertEqual(points[0][2:], [0.0, 0.0, 0.0])
        self.assertEqual(points[0][:2], result["start"])
        # 시간과 누적 거리는 줄어들지 않고, 마지막 누적 거리는 총 거리와 같다
        times = [p[2] for p in points]
        dists = [p[3] for p in points]
        self.assertEqual(times, sorted(times))
        self.assertEqual(dists, sorted(dists))
        self.assertAlmostEqual(dists[-1], result["distance_mm"], delta=0.2)
        self.assertAlmostEqual(times[-1], result["duration"], delta=0.01)
        self.assertGreater(max(p[4] for p in points), 0)

    def test_result_without_points_when_disabled(self):
        self.handler.result_points = False
        self.prepare_ready()
        self.run_putt()
        result = self.last_result_event()
        self.assertNotIn("points", result)
        self.assertNotIn("fields", result)
        self.assertIsNotNone(result["distance_mm"])  # 요약값은 그대로

    def test_get_result_before_any_result(self):
        reply = self.ask({"cmd": "GET_RESULT", "id": 9})
        self.assertEqual((reply["cmd"], reply["result"], reply["reason"]), ("RESULT", "NG", "no_result"))
        self.assertEqual(reply["id"], 9)

    def test_get_result_replays_last_result_to_requester_only(self):
        self.prepare_ready()
        self.run_putt()
        event = self.last_result_event()
        self.broadcast.clear()

        reply = self.ask({"cmd": "GET_RESULT", "id": "r1"}, client="B")
        self.assertEqual(self.broadcast, [])
        self.assertEqual(reply["result"], "OK")
        self.assertEqual(reply["id"], "r1")
        replayed = {k: v for k, v in reply.items() if k not in ("result", "id")}
        self.assertEqual(replayed, event)

    def test_reset_forgets_last_result(self):
        self.prepare_ready()
        self.run_putt()
        self.assertEqual(self.ask({"cmd": "RESET"})["result"], "OK")
        self.assertEqual(self.ask({"cmd": "GET_RESULT"})["reason"], "no_result")

    def test_status_and_result_order_on_finish(self):
        self.prepare_ready()
        self.run_putt()
        commands = [m["cmd"] for m in self.broadcast]
        finished_status = [
            i for i, m in enumerate(self.broadcast) if m["cmd"] == "STATUS" and m["state"] == "FINISHED"
        ]
        self.assertTrue(finished_status)
        self.assertLess(finished_status[0], commands.index("RESULT"))


class AlignOptionsTests(HandlerTestCase):
    """ALIGN의 세 가지 방식: 자동 검출 / corners 직접 전달 / use_saved 저장값 재사용."""

    def corners_message(self, **extra):
        return {"cmd": "ALIGN", "corners": [list(c) for c in MAT_CORNERS], **extra}

    def test_auto_reply_reports_method_and_corners(self):
        self.step()
        reply = self.ask({"cmd": "ALIGN"})
        self.assertEqual((reply["result"], reply["method"]), ("OK", "auto"))
        self.assertEqual(len(reply["corners"]), 4)
        for found, expected in zip(reply["corners"], MAT_CORNERS):  # 자동 검출은 1~2픽셀 오차
            self.assertAlmostEqual(found[0], expected[0], delta=3)
            self.assertAlmostEqual(found[1], expected[1], delta=3)

    def test_corners_applied_exactly(self):
        self.step()
        reply = self.ask(self.corners_message(width_mm=4500, height_mm=2000))
        self.assertEqual((reply["result"], reply["method"]), ("OK", "corners"))
        self.assertEqual(reply["corners"], [list(c) for c in MAT_CORNERS])
        self.assertEqual(self.engine.tracker.state, TrackerState.CALIBRATING)
        # 우하단 모서리는 정확히 (4500, 2000)mm
        x_mm, y_mm = self.engine.calibration.pixel_to_real(*MAT_CORNERS[2])
        self.assertAlmostEqual(x_mm, 4500, delta=0.5)
        self.assertAlmostEqual(y_mm, 2000, delta=0.5)

    def test_corners_work_without_camera_frame(self):
        # 카메라 영상이 없어도(자동 검출은 no_frame) 직접 전달한 모서리는 쓸 수 있다
        self.assertEqual(self.ask({"cmd": "ALIGN"})["reason"], "no_frame")
        self.assertEqual(self.ask(self.corners_message())["result"], "OK")

    def test_corners_make_auto_detection_failure_recoverable(self):
        self.camera.show_mat = False  # 매트가 안 보이는 상황 (조명 등)
        self.step()
        self.assertEqual(self.ask({"cmd": "ALIGN"})["reason"], "mat_not_found")
        self.assertEqual(self.ask(self.corners_message())["result"], "OK")
        self.assertTrue(self.engine.calibration.is_calibrated())

    def test_bad_corners_keep_previous_calibration(self):
        self.step()
        self.assertEqual(self.ask({"cmd": "ALIGN", "width_mm": 4500, "height_mm": 2000})["result"], "OK")
        before = list(self.engine.calibration.corners)

        rotated = [list(c) for c in MAT_CORNERS[1:] + MAT_CORNERS[:1]]  # 순서가 틀림
        reply = self.ask({"cmd": "ALIGN", "corners": rotated, "width_mm": 3000, "height_mm": 1500})
        self.assertEqual((reply["result"], reply["reason"]), ("NG", "bad_corners"))
        self.assertEqual(self.engine.calibration.corners, before)
        self.assertEqual(self.engine.calibration.mat_width_mm, 4500)  # 크기도 그대로

    def test_corners_out_of_frame(self):
        self.step()
        outside = [[100, 150], [5000, 100], [1020, 620], [90, 600]]
        reply = self.ask({"cmd": "ALIGN", "corners": outside})
        self.assertEqual(reply["reason"], "corners_out_of_frame")
        self.assertFalse(self.engine.calibration.is_calibrated())

    def test_corners_with_bad_size(self):
        reply = self.ask(self.corners_message(width_mm=-1))
        self.assertEqual(reply["reason"], "bad_size")

    def test_use_saved_reuses_existing_calibration(self):
        self.step()
        first = self.ask(self.corners_message(width_mm=4500, height_mm=2000))
        reply = self.ask({"cmd": "ALIGN", "use_saved": True})
        self.assertEqual((reply["result"], reply["method"]), ("OK", "saved"))
        self.assertEqual(reply["corners"], first["corners"])

    def test_use_saved_with_new_size_recomputes(self):
        self.ask(self.corners_message(width_mm=4500, height_mm=2000))
        reply = self.ask({"cmd": "ALIGN", "use_saved": True, "width_mm": 3000, "height_mm": 1500})
        self.assertEqual((reply["width_mm"], reply["height_mm"]), (3000, 1500))
        x_mm, y_mm = self.engine.calibration.pixel_to_real(*MAT_CORNERS[2])
        self.assertAlmostEqual(x_mm, 3000, delta=0.5)
        self.assertAlmostEqual(y_mm, 1500, delta=0.5)

    def test_use_saved_without_saved_calibration(self):
        reply = self.ask({"cmd": "ALIGN", "use_saved": True})
        self.assertEqual((reply["result"], reply["reason"]), ("NG", "not_calibrated"))

    def test_saved_calibration_survives_engine_restart(self):
        self.ask(self.corners_message(width_mm=4500, height_mm=2000))  # 설정 파일에 저장된다

        from src.engine import VisionEngine
        from tests.fake_camera import FakeCamera

        restarted = VisionEngine(camera=FakeCamera())  # 새로 시작한 엔진 (카메라 영상은 아직 없음)
        try:
            self.assertTrue(restarted.calibration.is_calibrated())
            hello = []
            restarted.protocol_handler._send_to = lambda client, message: hello.append(message)
            restarted.protocol_handler.on_client_connected("NEW")
            self.assertTrue(hello[0]["calibrated"])  # 접속하자마자 캘리브레이션 여부를 알 수 있다
            self.assertEqual(restarted.align_saved(), "OK")  # 영상 없이도 ALIGN use_saved 가능
        finally:
            restarted.shutdown()

    def test_corners_and_use_saved_together_rejected(self):
        reply = self.ask(self.corners_message(use_saved=True))
        self.assertEqual(reply["reason"], "bad_option")

    def test_use_saved_must_be_boolean(self):
        self.assertEqual(self.ask({"cmd": "ALIGN", "use_saved": "yes"})["reason"], "bad_option")
        self.assertEqual(self.ask({"cmd": "ALIGN", "use_saved": 1})["reason"], "bad_option")

    def test_null_corners_means_not_given(self):
        self.step()
        reply = self.ask({"cmd": "ALIGN", "corners": None})
        self.assertEqual(reply["method"], "auto")

    def test_all_methods_refused_while_armed(self):
        self.prepare_ready()
        self.ask({"cmd": "ADD", "type": "BALL"})
        for extra in ({}, {"use_saved": True}, {"corners": [list(c) for c in MAT_CORNERS]}):
            reply = self.ask({"cmd": "ALIGN", **extra})
            self.assertEqual(reply["reason"], "bad_state", extra)


class SaveTests(HandlerTestCase):
    def test_save_after_finished(self):
        self.prepare_ready()
        self.run_putt()
        reply = self.ask({"cmd": "SAVE", "id": "s1"})
        self.assertEqual((reply["cmd"], reply["result"]), ("SAVE", "OK"))
        self.assertEqual(reply["id"], "s1")  # 요청 id 되돌림
        self.assertIsInstance(reply["record_id"], int)  # DB 행 번호는 별도 필드
        saved = self.engine.storage.get_result(reply["record_id"])
        self.assertIsNotNone(saved)
        self.assertGreater(saved.total_distance_cm, 0)

    def test_each_save_gets_new_record_id(self):
        self.prepare_ready()
        self.run_putt()
        first = self.ask({"cmd": "SAVE"})["record_id"]
        second = self.ask({"cmd": "SAVE"})["record_id"]
        self.assertNotEqual(first, second)

    def test_save_only_when_finished(self):
        reply = self.ask({"cmd": "SAVE"})  # IDLE
        self.assertEqual((reply["result"], reply["reason"]), ("NG", "bad_state"))
        self.prepare_ready()
        self.assertEqual(self.ask({"cmd": "SAVE"})["reason"], "bad_state")  # READY
        self.ask({"cmd": "ADD", "type": "BALL"})
        self.assertEqual(self.ask({"cmd": "SAVE"})["reason"], "bad_state")  # ARMED

    def test_save_needs_calibration(self):
        # 캘리브레이션 없이 화면 버튼 방식으로 추적을 끝낸 상황 (앱의 INIT은 캘리브레이션이 필요하다)
        self.step()
        self.assertEqual(self.engine.register_start(), START_OK)
        self.assertTrue(self.engine.start_tracking())
        for i in range(1, 11):
            self.step(ball=(500 + i * 20, 400))
        for _ in range(20):
            self.step()
        self.assertEqual(self.engine.tracker.state, TrackerState.FINISHED)
        reply = self.ask({"cmd": "SAVE"})
        self.assertEqual((reply["result"], reply["reason"]), ("NG", "not_calibrated"))


class ProfileTests(HandlerTestCase):
    def test_list_profiles(self):
        reply = self.ask({"cmd": "PROFILE", "id": 1})
        self.assertEqual((reply["cmd"], reply["result"], reply["id"]), ("PROFILE", "OK", 1))
        self.assertEqual(reply["active"], "test")
        keys = [p["key"] for p in reply["profiles"]]
        for expected in ("test", "practice", "real", "practice_lime"):
            self.assertIn(expected, keys)
        self.assertTrue(all(p["name"] for p in reply["profiles"]))

    def test_switch_profile_changes_detection_and_persists(self):
        self.step()
        self.assertIsNotNone(self.engine.last_detection)  # 'test' 프로필: 자홍색 공이 검출된다

        reply = self.ask({"cmd": "PROFILE", "name": "practice"})  # 흰 공 프로필
        self.assertEqual((reply["result"], reply["active"]), ("OK", "practice"))
        self.assertEqual(load_settings()["detection"]["active_profile"], "practice")  # 파일에 저장됨
        self.step()
        self.assertIsNone(self.engine.last_detection)  # 검출기가 새로 만들어져 자홍색 공은 안 보인다

        self.assertEqual(self.ask({"cmd": "PROFILE", "name": "test"})["active"], "test")
        self.step()
        self.assertIsNotNone(self.engine.last_detection)

    def test_unknown_profile(self):
        reply = self.ask({"cmd": "PROFILE", "name": "nope"})
        self.assertEqual((reply["result"], reply["reason"]), ("NG", "unknown_profile"))
        self.assertEqual(self.ask({"cmd": "PROFILE"})["active"], "test")  # 그대로

    def test_name_must_be_string(self):
        self.assertEqual(self.ask({"cmd": "PROFILE", "name": 5})["reason"], "bad_value")

    def test_null_name_only_lists(self):
        self.assertEqual(self.ask({"cmd": "PROFILE", "name": None})["active"], "test")

    def test_cannot_switch_while_armed_or_tracking(self):
        self.prepare_ready()
        self.ask({"cmd": "ADD", "type": "BALL"})
        self.assertEqual(self.ask({"cmd": "PROFILE", "name": "practice"})["reason"], "bad_state")
        self.step(ball=(560, 400))  # 움직여서 TRACKING
        self.assertEqual(self.engine.tracker.state, TrackerState.TRACKING)
        self.assertEqual(self.ask({"cmd": "PROFILE", "name": "practice"})["reason"], "bad_state")
        # 목록 조회는 언제든 가능하다
        self.assertEqual(self.ask({"cmd": "PROFILE"})["result"], "OK")

    def test_hello_reports_active_profile(self):
        self.handler.on_client_connected("NEW")
        self.assertEqual(self.direct[0][1]["profile"], "test")


if __name__ == "__main__":
    unittest.main()
