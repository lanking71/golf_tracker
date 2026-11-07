"""
설정 파일을 읽고 쓰는 모듈.

카메라 해상도·FPS 같은 값을 코드에 직접 박아 넣지 않고 설정 파일에서 읽어오게 해서,
카메라가 바뀌어도 코드 수정 없이 설정 값만 바꾸면 되도록 한다.

25단계부터 설정 파일이 두 개다:

    config/settings.default.json  기본값. git이 관리한다 (모든 PC가 같은 파일).
    config/settings.json          이 PC(현장)의 값. git에 올라가지 않는다 (.gitignore).
                                  캘리브레이션 모서리, k1, 사용자 프로필, 고정 물체 등.

왜 나눴나: 예전에는 settings.json 하나를 git이 관리해서, git pull·커밋을 할 때마다
현장에서 맞춘 값(캘리브레이션 등)이 다른 PC의 값으로 덮어써졌다. 이제 현장 값은
git과 상관없는 settings.json에만 있으므로 pull해도 바뀌지 않는다.

읽는 순서 (load_settings):
  1. settings.json이 없으면 settings.default.json을 복사해서 만든다 (새 PC에서 처음 실행할 때).
  2. settings.json을 읽은 뒤, default에는 있는데 settings.json에는 없는 키를 기본값으로
     채운다 (깊은 병합). 새 단계에서 설정 항목이 늘어나도 현장 파일을 손으로 고칠 필요가 없다.
     settings.json에 이미 있는 값은 절대 바꾸지 않는다.
저장(save_settings)은 언제나 현장 파일(settings.json)에만 한다. default 파일은 프로그램이
고치지 않는다.

settings.json은 표준 JSON이라 파일 안에 주석을 쓸 수 없다(문법 오류가 되고,
save_settings()가 파일을 통째로 다시 쓸 때 사라지기도 한다).
각 설정값이 무슨 뜻인지는 config/settings.md에 정리되어 있다.

exe로 실행할 때는 exe와 같은 폴더의 config/settings.json을 읽고 쓰고 (src.paths.BASE_DIR),
기본값 파일은 exe 안에 함께 담아 둔 것(src.paths.BUNDLE_DIR)을 쓴다.
"""

import copy
import json
import shutil

from src.paths import BASE_DIR, BUNDLE_DIR

# 이 PC(현장)의 설정 파일: 프로그램이 읽고 쓰는 진짜 설정 (exe 옆 / 개발 중엔 프로젝트 루트)
SETTINGS_PATH = BASE_DIR / "config" / "settings.json"

# 기본값 파일 (git 관리). exe에서는 exe 안에 담아 온 파일이다.
DEFAULT_SETTINGS_PATH = BUNDLE_DIR / "config" / "settings.default.json"

# 설정값 설명 문서. exe 옆에 없으면 처음 한 번 꺼내 놓는다 (참고용).
_BUNDLED_DOCS_PATH = BUNDLE_DIR / "config" / "settings.md"


def deep_merge_defaults(site: dict, defaults: dict) -> dict:
    """site(현장 값)에 없는 키만 defaults(기본값)로 채운 새 dict를 돌려준다.

    - site에 있는 값은 그대로 둔다 (값이 null이어도 '일부러 비워 둔 값'으로 보고 유지).
    - 양쪽 모두 dict인 항목은 안쪽까지 같은 규칙으로 채운다 (깊은 병합).
    - 목록(list)은 합치지 않는다. 현장 파일에 목록이 있으면 그 목록을 그대로 쓴다
      (예: HSV 범위 [0, 0, 180]을 기본값과 섞으면 엉뚱한 값이 된다).
    원본(site, defaults)은 바꾸지 않는다.

    예) site     = {"tracking": {"stop_duration": 0.8}}
        defaults = {"tracking": {"stop_duration": 1.0, "lost_duration": 2.0}, "ball": {...}}
        결과     = {"tracking": {"stop_duration": 0.8, "lost_duration": 2.0}, "ball": {...}}
    """
    merged = copy.deepcopy(site)
    for key, default_value in defaults.items():
        if key not in merged:
            merged[key] = copy.deepcopy(default_value)
        elif isinstance(merged[key], dict) and isinstance(default_value, dict):
            merged[key] = deep_merge_defaults(merged[key], default_value)
    return merged


def load_default_settings() -> dict:
    """기본값 파일(settings.default.json)을 읽는다. 없거나 깨져 있으면 빈 dict."""
    if not DEFAULT_SETTINGS_PATH.exists():
        return {}
    try:
        with open(DEFAULT_SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _ensure_settings_file_exists() -> None:
    """현장 파일(settings.json)이 없으면 기본값 파일을 복사해서 만든다 (한 번만).

    새 PC에서 처음 실행하거나, exe를 처음 실행하는 경우다. 이후에는 settings.json이
    진짜 설정 파일이 되고 사용자가 자유롭게 고칠 수 있다.

    설정값 설명 문서(config/settings.md)도 exe 옆에 없으면 같은 방식으로 한 번 꺼내 놓는다
    (프로그램 동작에는 영향 없는 참고용이라 실패해도 조용히 넘어간다).
    """
    if SETTINGS_PATH.exists():
        return
    if not DEFAULT_SETTINGS_PATH.exists():
        return
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DEFAULT_SETTINGS_PATH, SETTINGS_PATH)

    docs_target = SETTINGS_PATH.parent / "settings.md"
    if _BUNDLED_DOCS_PATH.exists() and not docs_target.exists():
        try:
            shutil.copyfile(_BUNDLED_DOCS_PATH, docs_target)
        except OSError:
            pass


def load_settings() -> dict:
    """설정을 읽어 dict로 반환한다 (현장 값 + 빠진 키는 기본값으로 채움).

    파일이 없거나 내용이 비어 있어도 프로그램이 죽지 않도록
    빈 dict(또는 기본값만)를 반환한다. (호출하는 쪽에서 각자 기본값으로 채운다)
    """
    _ensure_settings_file_exists()
    site: dict = {}
    if SETTINGS_PATH.exists():
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            site = json.load(f)
    return deep_merge_defaults(site, load_default_settings())


def save_settings(settings: dict) -> None:
    """설정 dict를 현장 파일(config/settings.json)에 저장한다. 기본값 파일은 건드리지 않는다.

    HSV 튜닝 화면에서 '저장'을 누르거나 캘리브레이션을 마치면 이 함수로 파일에 반영된다.
    """
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=4)
        f.write("\n")
