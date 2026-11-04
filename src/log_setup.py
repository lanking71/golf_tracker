"""
로그 설정 모듈: 엔진이 무엇을 했는지 파일(logs/engine.log)과 콘솔에 남긴다.

화면 없이 오래 돌아가는 프로그램은 문제가 생겼을 때 "그때 무슨 일이 있었는지"를
나중에 볼 수 있어야 한다. exe로 묶으면 콘솔 창도 없으므로 파일 로그가 유일한 단서가 된다.

- 파일: logs/engine.log (exe 옆 / 개발 중엔 프로젝트 루트). 1MB가 넘으면 engine.log.1 ...
  로 넘기고 최대 3개까지만 보관해서 디스크가 가득 차지 않는다.
- 콘솔: 표준 출력(stdout)에 같은 내용을 한 줄씩 바로바로 출력한다 (메인 컨트롤이 엔진의
  출력을 읽을 수 있다). 콘솔이 없는 exe(stdout을 임시 버퍼로 대신한 경우)는 생략한다.

설정은 config/settings.json의 "logging" 항목 (없으면 기본값):
    level          로그 수준: DEBUG / INFO / WARNING / ERROR (기본 INFO)
    max_bytes      로그 파일 하나의 최대 크기 (기본 1048576 = 1MB)
    backup_count   보관할 이전 로그 파일 수 (기본 3)
    stats_interval_s  FPS·처리 시간 요약을 남기는 간격(초), 0이면 끔 (기본 10) - 엔진이 사용
"""

import io
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.config import load_settings
from src.paths import BASE_DIR

DEFAULT_LEVEL = "INFO"
DEFAULT_MAX_BYTES = 1024 * 1024
DEFAULT_BACKUP_COUNT = 3
DEFAULT_LOG_PATH = BASE_DIR / "logs" / "engine.log"

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

# 이 모듈이 붙인 핸들러를 구분하는 표시 (setup_logging을 여러 번 불러도 중복으로 붙지 않게)
_HANDLER_MARK = "_ball_tracker_handler"


def setup_logging(log_path: Path | None = None, console: bool = True) -> Path | None:
    """로그를 설정하고, 로그 파일 경로를 돌려준다 (파일을 못 만들면 None).

    프로그램 시작 때 한 번 부른다. 다시 불러도 핸들러가 중복으로 붙지 않는다.
    로그 폴더를 못 만들거나 파일을 못 열어도(읽기 전용 폴더 등) 프로그램이 죽지
    않고 콘솔 로그만 남긴다.
    """
    settings = load_settings().get("logging", {})
    level = getattr(logging, str(settings.get("level", DEFAULT_LEVEL)).upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)
    remove_handlers()  # 이미 붙인 것이 있으면 먼저 정리해서 중복을 막는다

    formatter = logging.Formatter(LOG_FORMAT)
    path = Path(log_path) if log_path is not None else DEFAULT_LOG_PATH

    used_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path,
            maxBytes=settings.get("max_bytes", DEFAULT_MAX_BYTES),
            backupCount=settings.get("backup_count", DEFAULT_BACKUP_COUNT),
            encoding="utf-8",
        )
        _attach(root, file_handler, formatter)
        used_path = path
    except OSError as error:
        # 로그 파일을 못 쓴다고 엔진이 멈추면 안 된다. 콘솔 로그로 알리고 계속한다.
        print(f"[경고] 로그 파일을 열 수 없습니다 ({path}): {error}", file=sys.stderr, flush=True)

    # exe(콘솔 없음)에서는 main.py가 stdout을 임시 버퍼(StringIO)로 바꿔 두는데,
    # 거기에 로그를 쌓으면 계속 메모리만 차지하므로 콘솔 출력은 하지 않는다.
    if console and sys.stdout is not None and not isinstance(sys.stdout, io.StringIO):
        _attach(root, logging.StreamHandler(sys.stdout), formatter)

    return used_path


def remove_handlers() -> None:
    """setup_logging이 붙인 핸들러를 모두 떼고 닫는다 (파일을 풀어 주기 위해 테스트에서도 쓴다)."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            root.removeHandler(handler)
            handler.close()


def _attach(root: logging.Logger, handler: logging.Handler, formatter: logging.Formatter) -> None:
    handler.setFormatter(formatter)
    setattr(handler, _HANDLER_MARK, True)
    root.addHandler(handler)
