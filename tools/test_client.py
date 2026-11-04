"""
비전 엔진 TCP 서버 테스트용 클라이언트 (메인 컨트롤 프로그램 대신 사용).

비전 엔진(python main.py)을 먼저 실행해 둔 상태에서 쓴다.

사용법:
    python tools/test_client.py                       직접 입력 모드 (127.0.0.1:9000)
    python tools/test_client.py 192.168.0.10 9000     다른 PC의 엔진에 접속
    python tools/test_client.py --script tools/scenario_putt.jsonl
                                                      파일의 명령을 순서대로 보냄

직접 입력 모드: JSON 한 줄을 입력하고 Enter. 예) {"cmd":"STATUS"}
엔진이 보내는 메시지는 화면에 계속 출력된다. 종료는 Ctrl+C.

스크립트 파일 형식 (한 줄에 하나):
    {"cmd":"PING","id":1}      JSON 명령 (그대로 전송)
    sleep 2                    2초 쉼 (공을 굴릴 시간 등)
    # 설명                     주석 (무시), 빈 줄도 무시
스크립트를 다 보내면 --wait초(기본 3초) 더 기다리며 응답을 받은 뒤 종료한다.
"""

import argparse
import json
import socket
import sys
import threading
import time


def receive_loop(sock: socket.socket) -> None:
    """서버가 보내는 메시지를 한 줄씩 화면에 출력한다 (별도 스레드)."""
    buffer = b""
    while True:
        try:
            data = sock.recv(4096)
        except OSError:
            return
        if not data:
            print("[서버와 연결이 끊어졌습니다]", flush=True)
            return
        buffer += data
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            print("<-", line.decode("utf-8"), flush=True)


def send_line(sock: socket.socket, line: str) -> None:
    """JSON 한 줄을 보낸다 (형식이 틀려도 서버의 오류 응답을 보려고 그대로 보낸다)."""
    print("->", line, flush=True)
    sock.sendall(line.encode("utf-8") + b"\n")


def run_script(sock: socket.socket, path: str, wait: float) -> None:
    """스크립트 파일의 명령을 순서대로 보낸다. 명령 사이에 0.3초씩 쉬어서 응답이 먼저 출력되게 한다."""
    with open(path, encoding="utf-8") as f:
        lines = [line.strip() for line in f]
    for line in lines:
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("sleep"):
            seconds = float(line.split()[1])
            print(f"[{seconds}초 대기]", flush=True)
            time.sleep(seconds)
            continue
        try:
            json.loads(line)
        except ValueError:
            print(f"[경고] JSON이 아닌 줄입니다 (그대로 보냅니다): {line}", flush=True)
        send_line(sock, line)
        time.sleep(0.3)
    print(f"[스크립트 끝 - {wait}초 더 기다립니다]", flush=True)
    time.sleep(wait)


def run_interactive(sock: socket.socket) -> None:
    print("JSON 한 줄을 입력하고 Enter (Ctrl+C로 종료)", flush=True)
    for line in sys.stdin:
        line = line.strip()
        if line:
            sock.sendall(line.encode("utf-8") + b"\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="비전 엔진 테스트 클라이언트")
    parser.add_argument("host", nargs="?", default="127.0.0.1", help="엔진이 실행 중인 PC의 주소")
    parser.add_argument("port", nargs="?", type=int, default=9000, help="통신 포트 (기본 9000)")
    parser.add_argument("--script", help="순서대로 보낼 명령이 적힌 파일")
    parser.add_argument("--wait", type=float, default=3.0, help="스크립트 종료 후 응답을 더 기다릴 초")
    args = parser.parse_args()

    sock = socket.create_connection((args.host, args.port), timeout=5)
    sock.settimeout(None)
    print(f"[{args.host}:{args.port} 접속 완료]", flush=True)
    threading.Thread(target=receive_loop, args=(sock,), daemon=True).start()
    try:
        if args.script:
            run_script(sock, args.script, args.wait)
        else:
            run_interactive(sock)
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()


if __name__ == "__main__":
    main()
