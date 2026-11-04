"""
비전엔진 TCP 서버 테스트용 클라이언트 (Flutter 앱 대신 사용).

사용법 (비전엔진 프로그램을 먼저 실행해 둔 상태에서):
    python tools/test_client.py                 # 기본: 127.0.0.1:9000
    python tools/test_client.py 192.168.0.10 9000

접속하면 명령을 JSON으로 직접 입력해 보낼 수 있다. 예)
    {"cmd":"STATUS"}
엔진이 보내는 메시지는 화면에 계속 출력된다. 종료는 Ctrl+C.
"""

import socket
import sys
import threading


def receive_loop(sock: socket.socket) -> None:
    """서버가 보내는 메시지를 한 줄씩 화면에 출력한다 (별도 스레드)."""
    buffer = b""
    while True:
        data = sock.recv(4096)
        if not data:
            print("[서버와 연결이 끊어졌습니다]")
            return
        buffer += data
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            print("<-", line.decode("utf-8"))


def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9000
    sock = socket.create_connection((host, port), timeout=5)
    sock.settimeout(None)
    print(f"[{host}:{port} 접속 완료] JSON 한 줄을 입력하고 Enter (Ctrl+C로 종료)")
    threading.Thread(target=receive_loop, args=(sock,), daemon=True).start()
    try:
        for line in sys.stdin:
            line = line.strip()
            if line:
                sock.sendall(line.encode("utf-8") + b"\n")
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()


if __name__ == "__main__":
    main()
