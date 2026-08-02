"""
설정 파일(config/settings.json)을 읽어오는 모듈.

카메라 해상도·FPS 같은 값을 코드에 직접 박아 넣지 않고
이 파일에서 읽어오게 해서, 카메라가 바뀌어도 코드 수정 없이
config/settings.json 값만 바꾸면 되도록 한다.
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
