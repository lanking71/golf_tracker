"""
설정 파일(config/settings.json)을 읽어오는 모듈.

카메라 해상도·FPS 같은 값을 코드에 직접 박아 넣지 않고
이 파일에서 읽어오게 해서, 카메라가 바뀌어도 코드 수정 없이
config/settings.json 값만 바꾸면 되도록 한다.

settings.json은 표준 JSON이라 파일 안에 주석을 쓸 수 없다(문법 오류가
되고, save_settings()가 파일을 통째로 다시 쓸 때 사라지기도 한다).
각 설정값이 무슨 뜻인지는 config/settings.md에 정리되어 있다.

exe로 실행할 때는 이 파일이 exe와 같은 폴더의 config/settings.json을
읽고 쓴다 (src.paths.BASE_DIR 참고) - 그래야 사용자가 탐색기에서 바로
찾아 열어보고 고칠 수 있고, exe를 다시 실행해도 값이 유지된다.
"""

import json
import shutil

from src.paths import BASE_DIR, BUNDLE_DIR

# 사용자가 실제로 읽고 쓰는 설정 파일 (exe 옆 / 개발 중엔 프로젝트 루트)
SETTINGS_PATH = BASE_DIR / "config" / "settings.json"

# exe 안에 함께 담아둔 기본 설정 파일. 개발 중에는 SETTINGS_PATH와
# 같은 파일을 가리킨다 (BUNDLE_DIR == BASE_DIR이므로 아래 복사 로직이
# 자기 자신에 복사를 시도하지 않도록 별도로 검사한다).
_BUNDLED_DEFAULT_SETTINGS_PATH = BUNDLE_DIR / "config" / "settings.json"


def _ensure_settings_file_exists() -> None:
    """exe 옆에 설정 파일이 없으면, exe 안에 담아온 기본값을 한 번만 꺼내놓는다.

    exe를 처음 실행하는 상황(설정 파일을 아예 새로 받은 경우)을 위한
    처리다. 이후에는 exe 옆 파일이 진짜 설정 파일이 되고, 사용자가
    자유롭게 고칠 수 있다 - exe 안의 기본값은 다시 참조하지 않는다.

    설정값 설명 문서(config/settings.md)도 exe 안에 함께 담아뒀다가,
    같은 방식으로 exe 옆에 없을 때만 한 번 꺼내놓는다 - 설정을 직접
    보고 고치려는 사용자가 탐색기에서 바로 참고할 수 있게 하기 위해서다
    (프로그램 동작에는 영향 없는 참고용 문서라 실패해도 조용히 넘어간다).
    """
    if SETTINGS_PATH.exists():
        return
    if not _BUNDLED_DEFAULT_SETTINGS_PATH.exists():
        return
    if _BUNDLED_DEFAULT_SETTINGS_PATH == SETTINGS_PATH:
        return
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_BUNDLED_DEFAULT_SETTINGS_PATH, SETTINGS_PATH)

    bundled_docs = _BUNDLED_DEFAULT_SETTINGS_PATH.parent / "settings.md"
    if bundled_docs.exists():
        try:
            shutil.copyfile(bundled_docs, SETTINGS_PATH.parent / "settings.md")
        except OSError:
            pass


def load_settings() -> dict:
    """설정 파일을 읽어 dict로 반환한다.

    파일이 없거나 내용이 비어 있어도 프로그램이 죽지 않도록
    빈 dict를 반환한다. (호출하는 쪽에서 각자 기본값으로 채운다)
    """
    _ensure_settings_file_exists()
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
