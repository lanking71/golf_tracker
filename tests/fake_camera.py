"""
테스트용 가짜 카메라와 테스트 설정 도우미.

진짜 카메라 없이도 엔진을 시험할 수 있도록, 회색 배경 위에 녹색 매트와
자홍색(마젠타) 공을 그려서 프레임으로 돌려준다. Camera 클래스와 같은
메서드(open / is_opened / read_frame / release)를 가지고 있어서 엔진이
진짜 카메라와 똑같이 다룬다.

테스트가 사용자의 config/settings.json을 건드리지 않도록, make_test_settings()가
임시 폴더에 테스트 전용 설정 파일을 만들고 src.config가 그 파일을 읽게 바꾼다.
"""

import json
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

import src.config

# 합성 영상 속 매트의 네 모서리 (좌상, 우상, 우하, 좌하) - 픽셀 좌표
MAT_CORNERS = [(100, 150), (1000, 100), (1020, 620), (90, 600)]
MAT_COLOR_BGR = (60, 160, 60)  # 녹색
# 자홍색: HSV 색상(H)이 약 150이라 매트(녹색, H 약 60)와 확실히 구분된다.
BALL_COLOR_BGR = (255, 0, 255)
BALL_RADIUS = 18
FRAME_SIZE = (1280, 720)  # (너비, 높이)


class FakeCamera:
    """합성 영상을 돌려주는 가짜 카메라."""

    def __init__(self, ball=(500, 400), show_mat=True):
        self.ball = ball  # (x, y) 픽셀 좌표, None이면 공이 없는 화면
        self.show_mat = show_mat  # False면 매트가 안 보이는 화면
        self.available = True  # False로 바꾸면 카메라가 끊긴 것처럼 동작한다
        self.open_calls = 0  # open()이 불린 횟수 (재연결 시도 간격을 확인하는 데 쓴다)
        self._opened = False

    def open(self) -> bool:
        self.open_calls += 1
        self._opened = self.available
        return self._opened

    def is_opened(self) -> bool:
        return self._opened

    def read_frame(self):
        if not self._opened or not self.available:
            return None
        width, height = FRAME_SIZE
        frame = np.full((height, width, 3), (40, 40, 40), np.uint8)
        if self.show_mat:
            cv2.fillPoly(frame, [np.array(MAT_CORNERS, np.int32)], MAT_COLOR_BGR)
        if self.ball is not None:
            cv2.circle(frame, self.ball, BALL_RADIUS, BALL_COLOR_BGR, -1)
        return frame

    def release(self) -> None:
        self._opened = False


def make_test_settings(tmp_dir: str, **overrides) -> mock._patch:
    """임시 폴더에 테스트 전용 설정을 만들고, src.config가 그걸 읽도록 바꾸는 패치를 돌려준다.

    사용법 (setUp에서 patch.start(), tearDown에서 patch.stop()):
        patch = make_test_settings(tmp_dir)
        patch.start()
    overrides로 설정의 일부 항목을 바꿀 수 있다 (예: tracking={"stop_duration": 1.0}).
    """
    settings = {
        "camera": {},
        "detection": {
            "active_profile": "test",
            "profiles": {
                "test": {
                    "name": "테스트 공",
                    "hsv_lower": [140, 80, 80],
                    "hsv_upper": [170, 255, 255],
                    "min_area": 200,
                    "max_area": 8000,
                    "min_circularity": 0.7,
                }
            },
        },
        "tracking": {
            "min_move_distance": 6,
            "max_jump_distance": 200,
            "max_consecutive_jumps": 10,
            "stop_threshold": 3,
            "stop_duration": 0.5,
            "lost_duration": 2.0,
        },
        "calibration": {
            "mat_width_mm": 4500,
            "mat_height_mm": 2000,
            "filter_outside_mat": True,
        },
        # 테스트에서는 실제 통신 포트를 열지 않는다
        "protocol": {"enabled": False, "port": 9000, "status_hz": 30},
        "storage": {"db_path": str(Path(tmp_dir) / "results.db")},
    }
    settings.update(overrides)

    settings_path = Path(tmp_dir) / "settings.json"
    settings_path.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
    return mock.patch.object(src.config, "SETTINGS_PATH", settings_path)
