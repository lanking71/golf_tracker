"""
볼 트래커 - 프로그램 시작점.

이 파일은 프로그램 실행만 담당합니다. (CLAUDE.md 디렉토리 구조 규칙)
실제 화면 구성은 src/ui/main_window.py, 스타일은 src/ui/styles.py에 있습니다.
"""

import io
import sys

# PyInstaller로 "콘솔 창 없이"(windowed) 묶은 exe는 sys.stdout/stderr가
# None이 된다. src/camera.py가 카메라 연결 시 진단 정보를 print()로
# 출력하는데, 이걸 그대로 두면 exe 실행 중 print()를 호출하는 순간
# "'NoneType' object has no attribute 'write'"로 프로그램이 죽는다.
# 개발 중(python main.py) 실행이나 콘솔 있는 빌드에서는 stdout/stderr가
# 정상적으로 있으므로 이 코드는 아무 영향을 주지 않는다.
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

from PySide6.QtWidgets import QApplication

from src.ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
