"""
설정 파일(config/settings.json)을 읽어오는 모듈.

카메라 해상도·FPS 같은 값을 코드에 직접 박아 넣지 않고
이 파일에서 읽어오게 해서, 카메라가 바뀌어도 코드 수정 없이
config/settings.json 값만 바꾸면 되도록 한다.

settings.json은 표준 JSON이라 파일 안에 주석을 쓸 수 없다(문법 오류가
되고, save_settings()가 파일을 통째로 다시 쓸 때 사라지기도 한다).
각 설정값이 무슨 뜻인지는 config/settings.md에 정리되어 있다.
"""

import json
from pathlib import Path

# 프로젝트 루트를 기준으로 한 설정 파일 경로
SETTINGS_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.json"


def load_settings() -> dict:
    """설정 파일을 읽어 dict로 반환한다.

    파일이 없거나 내용이 비어 있어도 프로그램이 죽지 않도록
    빈 dict를 반환한다. (호출하는 쪽에서 각자 기본값으로 채운다)
    """
    if not SETTINGS_PATH.exists():
        return {}
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_settings(settings: dict) -> None:
    """설정 dict를 config/settings.json에 저장한다.

    HSV 튜닝 화면에서 '저장'을 누르면 이 함수로 파일에 반영된다.
    """
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=4)
        f.write("\n")
