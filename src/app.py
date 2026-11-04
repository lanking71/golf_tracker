"""
프로그램 실행 모드를 담당하는 모듈 (main.py가 부른다).

- run_headless(): 화면 없이 엔진만 실행한다. 메인 컨트롤 프로그램이 이
  프로그램을 띄우고 JSON(TCP)으로 명령을 보내는 평소 운영 방식이다.
  Ctrl+C(SIGINT)로 깨끗하게 종료할 수 있다.
- run_ui(): 같은 엔진에 설정·확인용 화면(MainWindow)을 얹어서 실행한다.
  카메라 설치, 공 색(HSV) 조정, 매트 캘리브레이션 확인 같은 현장 작업용이다.
  화면이 떠 있어도 TCP 서버는 그대로 동작해서 컨트롤 프로그램이 접속할 수 있다.

종료 코드: 0 = 정상, 2 = 통신 서버를 열지 못함(포트가 이미 사용 중 등).
"""

import signal
import sys

from PySide6.QtCore import QCoreApplication, QTimer

from src.engine import VisionEngine

# 헤드리스에서 Python의 시그널(Ctrl+C) 처리가 돌 기회를 주는 주기(ms).
# Qt 이벤트 루프가 C++ 안에서 돌고 있으면 Python이 Ctrl+C를 바로 알아채지 못해서,
# 아무 일도 안 하는 타이머를 돌려 주기적으로 Python 코드가 실행되게 한다.
SIGNAL_CHECK_INTERVAL_MS = 200


def _start_engine(engine: VisionEngine) -> bool:
    """엔진을 시작하고, 통신 서버를 못 열었으면 안내 문구를 stderr에 출력한다."""
    ok = engine.start()
    server = engine.protocol_server
    if not ok:
        print(
            f"[오류] 통신 포트 {server.port}을(를) 열 수 없습니다. "
            "같은 프로그램이 이미 실행 중이거나 다른 프로그램이 포트를 쓰고 있을 수 있습니다.",
            file=sys.stderr,
            flush=True,
        )
    elif server.is_listening():
        print(f"[엔진] 시작 - 통신 대기 중 (TCP 포트 {server.port})", flush=True)
    else:
        print("[엔진] 시작 - 통신 꺼짐 (config/settings.json의 protocol.enabled)", flush=True)
    return ok


def run_headless() -> int:
    """화면 없이 엔진만 실행한다. 종료 코드를 돌려준다."""
    app = QCoreApplication(sys.argv)
    engine = VisionEngine()
    app.aboutToQuit.connect(engine.shutdown)

    if not _start_engine(engine):
        engine.shutdown()
        return 2

    signal.signal(signal.SIGINT, lambda *_: app.quit())
    keepalive = QTimer()
    keepalive.timeout.connect(lambda: None)
    keepalive.start(SIGNAL_CHECK_INTERVAL_MS)

    code = app.exec()
    print("[엔진] 종료", flush=True)
    return code


def run_ui() -> int:
    """엔진 + 설정·확인용 화면을 함께 실행한다. 종료 코드를 돌려준다."""
    # 화면 관련 모듈은 여기서 가져온다 (헤드리스 실행에서는 QtWidgets를 불러오지 않는다).
    from PySide6.QtWidgets import QApplication

    from src.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    engine = VisionEngine()
    window = MainWindow(engine)
    app.aboutToQuit.connect(engine.shutdown)

    # 통신 포트를 못 열어도 화면 모드에서는 종료하지 않는다 (화면 상단에
    # '포트 사용 불가'가 표시되고, 설정 창에서 포트를 바꿀 수 있다).
    _start_engine(engine)
    window.show()
    return app.exec()
