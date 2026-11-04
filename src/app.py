"""
프로그램 실행 모드를 담당하는 모듈 (main.py가 부른다).

- run_headless(): 화면 없이 엔진만 실행한다. 메인 컨트롤 프로그램이 이
  프로그램을 띄우고 JSON(TCP)으로 명령을 보내는 평소 운영 방식이다.
  Ctrl+C(SIGINT)로 깨끗하게 종료할 수 있다.
- run_ui(): 같은 엔진에 설정·확인용 화면(MainWindow)을 얹어서 실행한다.
  카메라 설치, 공 색(HSV) 조정, 매트 캘리브레이션 확인 같은 현장 작업용이다.
  화면이 떠 있어도 TCP 서버는 그대로 동작해서 컨트롤 프로그램이 접속할 수 있다.

두 모드 모두 시작할 때 로그를 설정한다 (logs/engine.log + 콘솔, src/log_setup.py).
엔진이 하는 일(시작·접속·명령·추적·카메라 끊김·오류)은 모두 이 로그에 남는다.

종료 코드: 0 = 정상, 2 = 통신 서버를 열지 못함(포트가 이미 사용 중 등).
"""

import logging
import signal
import sys

from PySide6.QtCore import QCoreApplication, QTimer

from src.engine import VisionEngine
from src.log_setup import setup_logging

logger = logging.getLogger("app")

# 헤드리스에서 Python의 시그널(Ctrl+C) 처리가 돌 기회를 주는 주기(ms).
# Qt 이벤트 루프가 C++ 안에서 돌고 있으면 Python이 Ctrl+C를 바로 알아채지 못해서,
# 아무 일도 안 하는 타이머를 돌려 주기적으로 Python 코드가 실행되게 한다.
SIGNAL_CHECK_INTERVAL_MS = 200


def run_headless() -> int:
    """화면 없이 엔진만 실행한다. 종료 코드를 돌려준다."""
    log_path = setup_logging()
    logger.info("볼 트래커 엔진 실행 (화면 없음) - 로그 파일: %s", log_path)

    app = QCoreApplication(sys.argv)
    engine = VisionEngine()
    app.aboutToQuit.connect(engine.shutdown)

    # 통신 포트를 못 열었으면 컨트롤이 접속할 수 없으므로 의미가 없다.
    # 원인은 엔진이 이미 로그에 남겼으니(engine.start) 여기서는 종료 코드만 돌려준다.
    if not engine.start():
        engine.shutdown()
        return 2

    signal.signal(signal.SIGINT, lambda *_: app.quit())
    keepalive = QTimer()
    keepalive.timeout.connect(lambda: None)
    keepalive.start(SIGNAL_CHECK_INTERVAL_MS)

    return app.exec()


def run_ui() -> int:
    """엔진 + 설정·확인용 화면을 함께 실행한다. 종료 코드를 돌려준다."""
    # 화면 관련 모듈은 여기서 가져온다 (헤드리스 실행에서는 QtWidgets를 불러오지 않는다).
    from PySide6.QtWidgets import QApplication

    from src.ui.main_window import MainWindow

    log_path = setup_logging()
    logger.info("볼 트래커 엔진 실행 (설정·확인용 화면과 함께) - 로그 파일: %s", log_path)

    app = QApplication(sys.argv)
    engine = VisionEngine()
    window = MainWindow(engine)
    app.aboutToQuit.connect(engine.shutdown)

    # 통신 포트를 못 열어도 화면 모드에서는 종료하지 않는다 (화면 상단에
    # '포트 사용 불가'가 표시되고, 설정 창에서 포트를 바꿀 수 있다).
    engine.start()
    window.show()
    return app.exec()
