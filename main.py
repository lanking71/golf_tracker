"""
볼 트래커 - 프로그램 시작점.

이 파일은 프로그램 실행만 담당합니다. (CLAUDE.md 디렉토리 구조 규칙)
실제 화면 구성은 src/ui/main_window.py, 스타일은 src/ui/styles.py에 있습니다.
"""

import sys

from PySide6.QtWidgets import QApplication

from src.ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
