"""
볼 트래커 비전 엔진 - 프로그램 시작점.

이 파일은 프로그램 실행만 담당합니다. (CLAUDE.md 디렉토리 구조 규칙)
엔진 본체는 src/engine.py, 실행 모드 분기는 src/app.py, 화면은 src/ui/에 있습니다.

실행 방법:
    python main.py        화면 없이 엔진만 실행 (메인 컨트롤 프로그램이 띄우는 평소 방식)
    python main.py --ui   설정·확인용 화면과 함께 실행 (카메라 설치, 공 색 조정, 캘리브레이션)
"""

import argparse
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

from src.app import run_headless, run_ui


def main() -> int:
    parser = argparse.ArgumentParser(description="볼 트래커 비전 엔진")
    parser.add_argument(
        "--ui",
        action="store_true",
        help="설정·확인용 화면을 함께 띄웁니다 (기본: 화면 없이 엔진만 실행)",
    )
    args = parser.parse_args()
    return run_ui() if args.ui else run_headless()


if __name__ == "__main__":
    sys.exit(main())
