"""
HSV 색상 기반 골프공 검출 모듈.

카메라 프레임에서 설정된 HSV 범위에 맞는 영역을 찾아,
그중 가장 공처럼 생긴(원형에 가깝고 크기가 적절한) 후보 하나를
골라 중심 좌표와 반지름을 돌려준다.

검출 프로필(HSV 범위, 최소·최대 면적, 최소 원형도)은
config/settings.json의 "detection" 항목에서 읽어온다.
카메라나 공 색이 바뀌어도 이 파일이 아니라 config/settings.json
값만 바꾸면 된다.

공을 못 찾은 경우(조명 문제, 공이 화면 밖으로 나감 등)에도
예외를 던지지 않고 조용히 None을 반환한다.
"""

from dataclasses import dataclass

import cv2
import numpy as np

from src.config import load_settings

# config/settings.json에 "detection" 항목이 없거나 일부 값이 빠져 있을 때 쓸 기본값.
# practice(연습용)는 흰 공이라 색상(Hue)보다 "채도 낮음 + 명도 높음"으로 구분한다.
# real(실전용)은 분홍 공이라 Hue(색상) 범위로 구분한다.
DEFAULT_DETECTION_SETTINGS = {
    "active_profile": "practice",
    "profiles": {
        "practice": {
            "name": "연습용 (흰 공)",
            "hsv_lower": [0, 0, 180],
            "hsv_upper": [180, 60, 255],
            "min_area": 200,
            "max_area": 8000,
            "min_circularity": 0.7,
        },
        "real": {
            "name": "실전용 (분홍 공)",
            "hsv_lower": [140, 80, 80],
            "hsv_upper": [170, 255, 255],
            "min_area": 200,
            "max_area": 8000,
            "min_circularity": 0.7,
        },
    },
}

# 가우시안 블러 커널 크기. 인조잔디(퍼팅 매트) 표면의 잔털 노이즈가
# HSV 마스크에 잡티로 섞여 들어가는 것을 줄이기 위해 검출 전에 적용한다.
# 가로/세로 모두 홀수여야 한다.
BLUR_KERNEL_SIZE = (9, 9)

# 마스크의 작은 구멍/잡티를 정리할 때 쓰는 모폴로지 커널 크기
MORPH_KERNEL_SIZE = (5, 5)


@dataclass
class BallDetection:
    """검출된 공 하나의 정보 (프레임 좌표 기준, 단위: 픽셀)."""

    x: int
    y: int
    radius: int


class BallDetector:
    """HSV 범위로 프레임에서 공 후보를 찾고, 가장 그럴듯한 것 하나를 고르는 클래스."""

    def __init__(self, settings: dict | None = None):
        loaded = settings if settings is not None else load_settings().get("detection", {})
        merged = {**DEFAULT_DETECTION_SETTINGS, **loaded}
        # profiles도 일부 프로필만 커스텀됐을 수 있으니 기본값과 합쳐준다.
        merged["profiles"] = {
            **DEFAULT_DETECTION_SETTINGS["profiles"],
            **merged.get("profiles", {}),
        }
        self._settings = merged

    def _active_profile(self) -> dict:
        name = self._settings.get("active_profile", "practice")
        profiles = self._settings["profiles"]
        return profiles.get(name, DEFAULT_DETECTION_SETTINGS["profiles"]["practice"])

    def detect(self, frame: np.ndarray) -> BallDetection | None:
        """프레임에서 공을 하나 찾는다.

        찾지 못했거나 처리 중 어떤 문제가 생겨도 예외를 던지지 않고
        조용히 None을 반환한다. (호출하는 UI 쪽이 죽지 않게)
        """
        try:
            return self._detect(frame)
        except Exception:
            # HSV 설정값이 잘못됐거나 프레임 형식이 예상과 달라도
            # 검출 실패로만 처리하고 프로그램은 계속 돌아가야 한다.
            return None

    def _detect(self, frame: np.ndarray) -> BallDetection | None:
        profile = self._active_profile()
        lower = np.array(profile["hsv_lower"], dtype=np.uint8)
        upper = np.array(profile["hsv_upper"], dtype=np.uint8)

        # 1) 인조잔디 노이즈를 줄이기 위해 블러 먼저 적용
        blurred = cv2.GaussianBlur(frame, BLUR_KERNEL_SIZE, 0)
        hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)

        # 2) 설정된 HSV 범위로 이진 마스크 생성 + 잡티 정리
        mask = cv2.inRange(hsv, lower, upper)
        kernel = np.ones(MORPH_KERNEL_SIZE, np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        # 3) 후보 중 면적이 적절하고 가장 원형에 가까운 것 하나만 선택
        best_contour = self._pick_best_contour(contours, profile)
        if best_contour is None:
            return None

        (x, y), radius = cv2.minEnclosingCircle(best_contour)
        return BallDetection(x=int(x), y=int(y), radius=int(radius))

    @staticmethod
    def _pick_best_contour(contours, profile: dict):
        min_area = profile.get("min_area", 200)
        max_area = profile.get("max_area", 8000)
        min_circularity = profile.get("min_circularity", 0.7)

        best_contour = None
        best_circularity = 0.0

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < min_area or area > max_area:
                continue

            perimeter = cv2.arcLength(contour, True)
            if perimeter == 0:
                continue

            # 원형도: 완전한 원이면 1.0, 찌그러질수록 작아진다.
            circularity = 4 * np.pi * area / (perimeter * perimeter)
            if circularity < min_circularity:
                continue

            if circularity > best_circularity:
                best_circularity = circularity
                best_contour = contour

        return best_contour
