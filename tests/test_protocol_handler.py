"""
프로토콜 v2 핸들러 테스트 (가짜 카메라 + 엔진, 소켓 없이 메시지만 확인).

- 응답은 요청한 앱에게만, 이벤트는 모두에게
- 요청 id 되돌려주기
- HELLO / STATUS 필드 / ABORT / RESET / GET_RESULT / PING
- RESULT의 궤적 점 [x, y, t, dist, speed]
"""

import unittest

from src.engine import START_OK
from src.protocol_handler import PROTOCOL_VERSION
from src.tracker import TrackerState
from tests.base import EngineTestCase


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


if __name__ == "__main__":
    unittest.main()
