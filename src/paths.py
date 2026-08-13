"""
실행 위치 기준 경로를 계산하는 모듈.

PyInstaller로 묶은 exe를 실행하면 다른 모듈들이 흔히 쓰는
`Path(__file__).resolve().parent.parent` 방식이 더는 프로젝트
루트를 가리키지 않는다 - exe를 실행하는 순간 코드가 통째로
sys._MEIPASS라는 임시 폴더에 풀려서 돌아가고, 이 폴더는 프로그램이
끝나면 사라지기 때문이다. 그 안에 config/settings.json이나
data/results.db를 두면 실행할 때마다 초기화되거나 아예 저장이
안 되는 것처럼 보인다.

그래서 "사용자가 계속 고치고 들여다볼 파일(설정, DB)을 둘 위치"와
"exe 안에 참고용으로 얼어붙어 있는 리소스가 풀리는 위치"를
아래처럼 구분해서 계산한다.
"""

import sys
from pathlib import Path


def _is_frozen() -> bool:
    """PyInstaller로 묶인 exe로 실행 중인지 여부."""
    return getattr(sys, "frozen", False)


if _is_frozen():
    # exe 파일이 있는 폴더. config/settings.json, data/results.db를
    # 여기 기준으로 둬야 exe를 옮기거나 다시 실행해도 그대로 남는다.
    BASE_DIR = Path(sys.executable).resolve().parent
    # PyInstaller가 실행 중에 리소스를 풀어두는 임시 폴더
    # (--onefile 기준. sys._MEIPASS는 PyInstaller가 실행 시점에만 만들어
    # 붙이는 속성이라 평소 정적 분석 도구는 모른다 - 그래서 getattr로 접근한다).
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
else:
    # 개발 중(python main.py로 실행)에는 프로젝트 루트가 곧 기준 폴더다.
    BASE_DIR = Path(__file__).resolve().parent.parent
    BUNDLE_DIR = BASE_DIR
