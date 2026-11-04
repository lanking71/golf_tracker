"""
TCP 통신 테스트: 진짜 소켓으로 엔진의 통신 서버에 접속해서 프로토콜 v2를 확인한다.

Qt 서버와 파이썬 소켓 클라이언트가 같은 프로세스에서 돌기 때문에, 클라이언트가
데이터를 받으려면 그 사이에 Qt 이벤트를 처리해 줘야 한다 (pump 함수).
"""

import json
import socket
import time
import unittest

from PySide6.QtNetwork import QAbstractSocket

from tests.base import EngineTestCase, qt_app


def find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = find_free_port()


def pump(seconds: float = 0.3) -> None:
    """Qt 이벤트를 처리하면서 잠시 기다린다 (서버가 접속·수신·송신을 처리하도록)."""
    end = time.time() + seconds
    while time.time() < end:
        qt_app.processEvents()
        time.sleep(0.005)


class Client:
    """테스트용 TCP 클라이언트. 받은 메시지를 줄 단위 JSON으로 모은다."""

    def __init__(self):
        self.sock = socket.create_connection(("127.0.0.1", PORT), timeout=2)
        self.sock.setblocking(False)
        self._buffer = b""

    def send_raw(self, data: bytes) -> None:
        self.sock.sendall(data)

    def send(self, message: dict) -> None:
        self.send_raw(json.dumps(message).encode("utf-8") + b"\n")

    def messages(self) -> list:
        """지금까지 도착한 메시지를 모두 꺼내 돌려준다 (한 번 꺼내면 비워진다)."""
        try:
            while True:
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                self._buffer += chunk
        except BlockingIOError:
            pass
        *lines, self._buffer = self._buffer.split(b"\n")
        return [json.loads(line) for line in lines if line]

    def close(self) -> None:
        self.sock.close()


class ServerTestCase(EngineTestCase):
    settings_overrides = {"protocol": {"enabled": True, "port": PORT, "status_hz": 30}}

    def setUp(self):
        super().setUp()
        self.assertTrue(self.engine.protocol_server.start(), "테스트 포트를 열 수 없음")
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.close()
        super().tearDown()

    def connect(self) -> Client:
        """접속하고 인사(HELLO, STATUS)까지 받은 클라이언트를 돌려준다."""
        client = Client()
        self.clients.append(client)
        pump()
        client.messages()  # 접속 직후의 HELLO·STATUS는 버린다
        return client


class ConnectionTests(ServerTestCase):
    def test_hello_and_status_on_connect(self):
        client = Client()
        self.clients.append(client)
        pump()
        messages = client.messages()
        self.assertEqual([m["cmd"] for m in messages], ["HELLO", "STATUS"])
        self.assertEqual(messages[0]["version"], "2.2")

    def test_hello_goes_only_to_new_client(self):
        first = self.connect()
        second = Client()
        self.clients.append(second)
        pump()
        self.assertEqual([m["cmd"] for m in second.messages()], ["HELLO", "STATUS"])
        self.assertEqual(first.messages(), [])  # 먼저 접속한 앱은 새 인사를 받지 않는다

    def test_low_delay_option_set_on_accepted_sockets(self):
        self.connect()
        sockets = list(self.engine.protocol_server._buffers)
        self.assertEqual(len(sockets), 1)
        self.assertEqual(sockets[0].socketOption(QAbstractSocket.LowDelayOption), 1)

    def test_client_count_follows_connections(self):
        a = self.connect()
        self.connect()
        self.assertEqual(self.engine.protocol_server.client_count(), 2)
        a.close()
        pump()
        self.assertEqual(self.engine.protocol_server.client_count(), 1)


class RoutingTests(ServerTestCase):
    def test_reply_only_to_requester_with_id(self):
        a, b = self.connect(), self.connect()
        a.send({"cmd": "PING", "id": 7})
        pump()
        replies = a.messages()
        self.assertEqual(len(replies), 1)
        self.assertEqual((replies[0]["cmd"], replies[0]["id"]), ("PONG", 7))
        self.assertEqual(b.messages(), [])

    def test_events_reach_all_clients(self):
        a, b = self.connect(), self.connect()
        self.engine.protocol_handler.on_tracking_started(1000.0)
        pump()
        expected = {"cmd": "TRACK", "event": "START", "ts": 1_000_000}
        self.assertEqual(a.messages(), [expected])
        self.assertEqual(b.messages(), [expected])

    def test_bad_json_error_only_to_sender(self):
        a, b = self.connect(), self.connect()
        a.send_raw(b"this is not json\n")
        pump()
        self.assertEqual(a.messages(), [{"cmd": "ERROR", "reason": "bad_json"}])
        self.assertEqual(b.messages(), [])

    def test_message_split_across_packets(self):
        a = self.connect()
        a.send_raw(b'{"cmd":"PI')
        pump(0.1)
        self.assertEqual(a.messages(), [])  # 아직 한 줄이 완성되지 않았다
        a.send_raw(b'NG","id":1}\n{"cmd":"PING","id":2}\n')
        pump()
        self.assertEqual([m["id"] for m in a.messages()], [1, 2])

    def test_disconnect_before_reply_does_not_crash(self):
        a, b = self.connect(), self.connect()
        a.send({"cmd": "PING", "id": 1})
        a.close()
        pump()  # 응답을 보내려는 순간 이미 끊겨 있을 수 있다
        b.send({"cmd": "PING", "id": 2})
        pump()
        self.assertEqual([m["id"] for m in b.messages()], [2])


class EndToEndTests(ServerTestCase):
    def test_full_scenario_over_tcp(self):
        """ALIGN -> INIT -> ADD -> (대기) -> 굴림 -> TRACK START / STATUS / RESULT -> GET_RESULT."""
        self.step()  # 카메라 프레임을 한 장 읽어 둔다 (ALIGN에 필요)
        client = self.connect()

        def ask(message):
            client.send(message)
            pump(0.1)
            replies = [m for m in client.messages() if m.get("id") == message["id"]]
            self.assertEqual(len(replies), 1, f"{message}에 대한 응답이 1개여야 함")
            return replies[0]

        self.assertEqual(ask({"cmd": "ALIGN", "id": 1, "width_mm": 4500, "height_mm": 2000})["result"], "OK")
        self.assertEqual(ask({"cmd": "INIT", "type": "BALL", "id": 2})["result"], "OK")
        self.assertEqual(ask({"cmd": "ADD", "type": "BALL", "id": 3})["result"], "OK")

        received = []
        for _ in range(30):  # 공이 가만히 대기 (ARMED)
            self.step()
        for i in range(1, 11):
            self.step(ball=(500 + i * 20, 400))
        for _ in range(20):
            self.step()
        pump(0.2)
        received.extend(client.messages())

        commands = [m["cmd"] for m in received]
        self.assertEqual(commands.count("TRACK"), 1)
        self.assertEqual(commands.count("RESULT"), 1)
        self.assertLess(commands.index("TRACK"), commands.index("RESULT"))
        tracking = [m for m in received if m["cmd"] == "STATUS" and m["state"] == "TRACKING"]
        self.assertGreater(len(tracking), 3)
        self.assertTrue(all(m["x"] is not None and m["t"] is not None for m in tracking))

        result = next(m for m in received if m["cmd"] == "RESULT")
        replay = ask({"cmd": "GET_RESULT", "id": 4})
        self.assertEqual(replay["points"], result["points"])


if __name__ == "__main__":
    unittest.main()
