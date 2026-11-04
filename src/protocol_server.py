"""
앱(Flutter) <-> 비전엔진 통신용 TCP 서버 모듈.

규칙 (한 줄 = JSON 메시지 1개):
- 메시지 하나는 JSON 객체 한 개이고, 끝에 줄바꿈('\\n')을 붙여 구분한다.
  예) {"cmd":"STATUS"}\\n
- 받은 데이터는 '\\n' 단위로 잘라 JSON으로 해석한다.
  해석에 실패하면 {"cmd":"ERROR","reason":"bad_json"} 으로 응답한다.

Qt의 QTcpServer를 쓰기 때문에 별도 스레드가 필요 없다 (Qt 이벤트
루프가 소켓 입출력을 알아서 처리해 준다).

이 모듈은 '통신'만 담당한다. 받은 명령을 어떻게 처리할지는
command_received 시그널을 받는 쪽(15단계에서 만들 protocol_handler)이 정한다.
"""

import json

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QHostAddress, QTcpServer, QTcpSocket

from src.config import load_settings

# config/settings.json에 "protocol" 항목이 없을 때 쓸 기본값
DEFAULT_ENABLED = True
DEFAULT_PORT = 9000


class ProtocolServer(QObject):
    """TCP로 앱과 JSON 메시지를 주고받는 서버."""

    # 앱에서 JSON 명령이 한 개 도착할 때마다 발생 (dict로 해석된 상태)
    command_received = Signal(dict)
    # 앱이 접속하거나 끊겨서 접속자 수가 바뀔 때 발생 (현재 접속자 수)
    client_count_changed = Signal(int)

    def __init__(self, settings: dict | None = None, parent=None):
        super().__init__(parent)
        loaded = settings if settings is not None else load_settings().get("protocol", {})
        self.enabled: bool = loaded.get("enabled", DEFAULT_ENABLED)
        self.port: int = loaded.get("port", DEFAULT_PORT)

        self._server = QTcpServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        # 소켓별로 아직 줄바꿈이 오지 않은 '반쪽짜리' 데이터를 모아두는 버퍼
        self._buffers: dict[QTcpSocket, bytes] = {}

    def start(self) -> bool:
        """서버를 시작한다. 사용 안 함 설정이거나 포트가 이미 사용 중이면 False."""
        if not self.enabled:
            return False
        # 모든 네트워크 인터페이스(Any)에서 받아야 다른 기기(폰 앱)가 접속할 수 있다.
        return self._server.listen(QHostAddress.Any, self.port)

    def stop(self) -> None:
        """서버를 멈추고 모든 접속을 끊는다."""
        for sock in list(self._buffers):
            sock.disconnectFromHost()
        self._server.close()

    def is_listening(self) -> bool:
        return self._server.isListening()

    def client_count(self) -> int:
        return len(self._buffers)

    def send(self, message: dict) -> None:
        """접속해 있는 모든 앱에 JSON 메시지를 한 줄로 보낸다."""
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        for sock in list(self._buffers):
            sock.write(data)

    def _send_to(self, sock: QTcpSocket, message: dict) -> None:
        """특정 앱 한 대에게만 보낸다 (그 앱이 보낸 명령에 대한 응답용)."""
        sock.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))

    def _on_new_connection(self) -> None:
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            self._buffers[sock] = b""
            sock.readyRead.connect(lambda s=sock: self._on_ready_read(s))
            sock.disconnected.connect(lambda s=sock: self._on_disconnected(s))
            self.client_count_changed.emit(len(self._buffers))

    def _on_disconnected(self, sock: QTcpSocket) -> None:
        self._buffers.pop(sock, None)
        sock.deleteLater()
        self.client_count_changed.emit(len(self._buffers))

    def _on_ready_read(self, sock: QTcpSocket) -> None:
        """데이터가 도착하면 줄 단위로 잘라 JSON으로 해석한다."""
        if sock not in self._buffers:
            return
        self._buffers[sock] += bytes(sock.readAll())
        # 줄바꿈이 올 때까지는 남겨 두고, 완성된 줄만 꺼내서 처리한다.
        while b"\n" in self._buffers[sock]:
            line, self._buffers[sock] = self._buffers[sock].split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line.decode("utf-8"))
                if not isinstance(message, dict):
                    raise ValueError("JSON 객체가 아님")
            except (ValueError, UnicodeDecodeError):
                self._send_to(sock, {"cmd": "ERROR", "reason": "bad_json"})
                continue
            self.command_received.emit(message)
