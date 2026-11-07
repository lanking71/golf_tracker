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

24단계 추가:
- detect(frame, roi, prefer): 화면 전체가 아니라 검색 영역(roi) 안에서만 찾을 수 있다.
  결과 좌표는 항상 프레임 전체 기준이다. prefer(예상 위치)를 주면 조건을 통과한 후보 중
  그 위치에 가장 가까운 것을 고른다 (흰 컵처럼 더 둥근 다른 물체보다 '공이 있을 곳'을 우선).
- Hue 범위가 빨강 경계(179 -> 0)를 넘을 수 있다: hsv_lower의 H가 hsv_upper의 H보다 크면
  [H최소 ~ 179] + [0 ~ H최대] 두 구간을 함께 본다 (분홍·빨강 공).
- 클릭으로 공 색 자동 추출: sample_ball_pixels() -> color_range_from_pixels() ->
  evaluate_color_range() -> auto_color_range()

25단계 추가 (mm 기준 공 크기):
- 캘리브레이션이 있으면 공 크기를 '픽셀 면적(min_area/max_area)'이 아니라 '그 위치에서 공이
  보여야 할 크기(예상 면적)' 대비 비율로 판정한다. 공 지름(ball.diameter_mm, 기본 42.67mm)을
  캘리브레이션으로 픽셀 면적으로 바꾼 값이 예상 면적이고, 덩어리 면적이 그 0.6~1.3배
  (ball.area_ratio_min ~ area_ratio_max)여야 공으로 본다. 흰 홀컵(공의 약 1.4~2.1배)은 여기서 걸러진다.
  매트 크기·카메라 높이가 현장마다 달라도 픽셀 기준값을 다시 맞출 필요가 없다.
- 캘리브레이션이 없으면 예전처럼 프로필의 min_area/max_area(픽셀)를 쓴다.
- detect()에 static_filter를 주면, 고정 물체(컵·티, src/static_objects.py) 자리의 후보를 뺀다.
"""

from dataclasses import dataclass

import cv2
import numpy as np

from src.config import load_settings

# 프로필에 min_area/max_area/min_circularity가 없을 때 쓰는 기본값.
# 22단계 현장 측정(1920x1080 -> process_scale 0.75 = 1440x810): 매트 끝에 놓인 공의 면적이
# 약 190~210px²(지름 약 18px)였다. 예전 기본값(200~8000)이면 공이 걸러질 수 있어서
# 최소는 그 절반 이하(80), 최대는 공의 약 3배(600)로 잡았다. (0.5배로 줄이면 공 면적이
# 약 70px²라 min_area를 30 정도로 더 낮춰야 한다)
DEFAULT_MIN_AREA = 80
DEFAULT_MAX_AREA = 600
DEFAULT_MIN_CIRCULARITY = 0.7

# config/settings.json에 "detection" 항목이 없거나 일부 값이 빠져 있을 때 쓸 기본값.
# practice(연습용 흰 공)는 색상(Hue)보다 "채도 낮음 + 명도 높음"으로 구분한다.
# real(실전용 분홍 공)과 practice_lime(연습용 연두 공)은 Hue(색상) 범위로 구분한다.
DEFAULT_DETECTION_SETTINGS = {
    "active_profile": "practice_lime",
    "profiles": {
        "practice": {
            "name": "연습용 (흰 공)",
            "hsv_lower": [0, 0, 180],
            "hsv_upper": [180, 60, 255],
            "min_area": DEFAULT_MIN_AREA,
            "max_area": DEFAULT_MAX_AREA,
            "min_circularity": DEFAULT_MIN_CIRCULARITY,
        },
        "real": {
            "name": "실전용 (분홍 공)",
            "hsv_lower": [140, 80, 80],
            "hsv_upper": [170, 255, 255],
            "min_area": DEFAULT_MIN_AREA,
            "max_area": DEFAULT_MAX_AREA,
            "min_circularity": DEFAULT_MIN_CIRCULARITY,
        },
        "practice_lime": {
            # 연두(옐로우그린) 공 + 어두운 조명 기준 추천 시작값.
            # Hue는 노랑(약 30)~초록(약 60) 사이를 넓게 잡고, 조명이 어두워도
            # 잡히도록 채도·명도 하한을 낮게 잡았다. '검출 설정'에서
            # 마스크 미리보기를 보면서 더 좁게 튜닝하면 된다.
            "name": "연습용 (연두 공)",
            "hsv_lower": [25, 40, 30],
            "hsv_upper": [55, 255, 255],
            "min_area": DEFAULT_MIN_AREA,
            "max_area": DEFAULT_MAX_AREA,
            "min_circularity": DEFAULT_MIN_CIRCULARITY,
        },
    },
}

# 가우시안 블러 커널 크기. 인조잔디(퍼팅 매트) 표면의 잔털 노이즈가
# HSV 마스크에 잡티로 섞여 들어가는 것을 줄이기 위해 검출 전에 적용한다.
# 가로/세로 모두 홀수여야 한다.
BLUR_KERNEL_SIZE = (9, 9)

# 마스크의 작은 구멍/잡티를 정리할 때 쓰는 모폴로지 커널 크기
MORPH_KERNEL_SIZE = (5, 5)

# 디버그 표시용으로 남겨 둘 덩어리: 이 넓이(px²) 이상인 것 중 큰 순서로 최대 몇 개
DEBUG_CANDIDATE_MIN_AREA = 10
DEBUG_CANDIDATE_LIMIT = 5

# 25단계: mm 기준 공 크기. config/settings.json의 "ball" 항목에 값이 없을 때 쓰는 기본값.
# 현장 측정(0.75배 화면 1440x810, k1 -0.50, 3000x1000 매트): 그 위치의 공 예상 면적은 약 215~218px².
#   - 공: 186~207px² -> x0.86~0.96 (블러·임계값 때문에 테두리가 조금 깎여 예상보다 약간 작게 잡힌다)
#   - 가운데 컵: 416~464px² -> x1.9~2.1
#   - 위·아래 컵(조금 어둡게 보임): 312~374px² -> x1.44~1.71  <- 프레임에 따라 1.5 아래로 내려온다!
# 그래서 지침의 제안값(0.6~1.5)으로는 위·아래 컵이 가끔 공 크기로 통과했다. 상한을 1.3으로 낮춰
# 공(최대 약 x0.96)과 컵(최소 약 x1.44) 사이에 둔다. 하한 0.6은 공이 조금 작게 잡히는 것을 감안한 여유.
DEFAULT_BALL_SETTINGS = {
    "diameter_mm": 42.67,  # 골프공 규격 지름(mm)
    "area_ratio_min": 0.6,  # 예상 면적 대비 최소 비율
    "area_ratio_max": 1.3,  # 예상 면적 대비 최대 비율 (컵 x1.44~2.1은 걸러짐)
}

# 후보가 공이 아니라고 본 이유 (디버그 표시 색을 정할 때 쓴다)
REJECT_SIZE = "size"  # 면적이 공 크기와 맞지 않음 (주황)
REJECT_SHAPE = "shape"  # 원형도가 낮음 (찌그러짐)
REJECT_STATIC = "static"  # 고정 물체(컵·티) 자리 (파랑)


def ball_settings(loaded: dict | None) -> dict:
    """설정 파일의 ball 항목과 기본값을 합친다 (값이 없어도 동작하게)."""
    return {**DEFAULT_BALL_SETTINGS, **(loaded or {})}


@dataclass
class BallDetection:
    """검출된 공 하나의 정보 (프레임 좌표 기준, 단위: 픽셀)."""

    x: int
    y: int
    radius: int
    # 공으로 고른 덩어리의 넓이(픽셀²). min_area/max_area를 현장에서 맞출 때 참고한다 (22단계).
    area: float = 0.0


@dataclass
class DetectionCandidate:
    """색 범위에 맞은 덩어리 하나 (공으로 뽑혔든 걸러졌든). 디버그 표시용.

    공이 min_area보다 작아서 걸러지면 화면에 아무것도 안 나와 원인을 알기 어렵다.
    그래서 '어떤 덩어리가 얼마 크기로 있었는지'를 남겨 두고, 화면 모드에서
    면적·지름·원형도를 글자로 보여준다.
    """

    x: int
    y: int
    area: float
    diameter: float  # 최소 외접원의 지름(픽셀)
    circularity: float
    accepted: bool  # 면적·원형도(+ 고정 물체 아님) 조건을 모두 통과했는지
    # 25단계: 예상 면적 대비 비율 (예: 1.02, 2.10). 캘리브레이션이 없으면 None (픽셀 기준 판정)
    area_ratio: float | None = None
    # 공이 아니라고 본 이유: None(통과) / REJECT_SIZE / REJECT_SHAPE / REJECT_STATIC
    reject: str | None = None


def blurred_hsv(frame: np.ndarray) -> np.ndarray:
    """블러를 먼저 적용한 뒤 HSV로 바꾼다 (검출과 색 추출이 같은 값을 보도록 함께 쓴다).

    인조잔디(퍼팅 매트) 표면의 잔털 노이즈를 줄이기 위해 블러를 먼저 적용한다.
    """
    blurred = cv2.GaussianBlur(frame, BLUR_KERNEL_SIZE, 0)
    return cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)


def hsv_in_range(hsv: np.ndarray, hsv_lower, hsv_upper) -> np.ndarray:
    """HSV 영상에서 범위 안 픽셀만 흰색(255)인 마스크를 만든다.

    Hue가 빨강 경계를 넘는 경우: hsv_lower의 H > hsv_upper의 H 이면 (예: 170 ~ 8)
    [170 ~ 179]와 [0 ~ 8] 두 구간을 따로 검사해서 합친다. OpenCV의 inRange는
    한 구간만 받기 때문이다. (예전 설정처럼 H최소 <= H최대이면 한 구간 그대로)
    """
    h_low, h_high = int(hsv_lower[0]), int(hsv_upper[0])
    if h_low <= h_high:
        return cv2.inRange(hsv, np.array(hsv_lower, dtype=np.uint8), np.array(hsv_upper, dtype=np.uint8))
    first = cv2.inRange(
        hsv,
        np.array([h_low, hsv_lower[1], hsv_lower[2]], dtype=np.uint8),
        np.array([179, hsv_upper[1], hsv_upper[2]], dtype=np.uint8),
    )
    second = cv2.inRange(
        hsv,
        np.array([0, hsv_lower[1], hsv_lower[2]], dtype=np.uint8),
        np.array([h_high, hsv_upper[1], hsv_upper[2]], dtype=np.uint8),
    )
    return cv2.bitwise_or(first, second)


def build_mask(frame: np.ndarray, hsv_lower, hsv_upper) -> np.ndarray:
    """프레임과 HSV 범위로 이진 마스크(흰색=범위 안, 검은색=범위 밖)를 만든다.

    BallDetector의 실제 검출과, HSV 튜닝 화면의 마스크 미리보기가
    이 함수를 함께 사용한다. 그래야 튜닝 화면에서 흰색으로 보이는
    영역이 실제 검출에 쓰이는 마스크와 항상 똑같다.
    """
    hsv = blurred_hsv(frame)
    mask = hsv_in_range(hsv, hsv_lower, hsv_upper)
    kernel = np.ones(MORPH_KERNEL_SIZE, np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return mask


class BallDetector:
    """HSV 범위로 프레임에서 공 후보를 찾고, 가장 그럴듯한 것 하나를 고르는 클래스."""

    def __init__(self, settings: dict | None = None, ball: dict | None = None):
        loaded = settings if settings is not None else load_settings().get("detection", {})
        merged = {**DEFAULT_DETECTION_SETTINGS, **loaded}
        # profiles도 일부 프로필만 커스텀됐을 수 있으니 기본값과 합쳐준다.
        merged["profiles"] = {
            **DEFAULT_DETECTION_SETTINGS["profiles"],
            **merged.get("profiles", {}),
        }
        self._settings = merged
        # 25단계: mm 기준 공 크기 설정 (공 지름, 예상 면적 대비 비율 범위)
        self.ball = ball_settings(ball if ball is not None else load_settings().get("ball"))
        # debug_enabled가 True면 detect()할 때마다 덩어리 목록(last_candidates)을 남긴다.
        self.debug_enabled = False
        self.last_candidates: list[DetectionCandidate] = []
        # 마지막 detect()에서 크기 때문에 걸러진 후보 수 (컵 등. 상태 요약 로그용)
        self.last_size_rejected = 0

    def _active_profile(self) -> dict:
        name = self._settings.get("active_profile", "practice")
        profiles = self._settings["profiles"]
        return profiles.get(name, DEFAULT_DETECTION_SETTINGS["profiles"]["practice"])

    def detect(
        self, frame: np.ndarray, roi=None, prefer=None, expected_area=None, static_filter=None
    ) -> BallDetection | None:
        """프레임에서 공을 하나 찾는다.

        roi: (x, y, 폭, 높이) - 이 사각형 안에서만 찾는다 (None이면 화면 전체).
             폭이나 높이가 0이면 아무 데도 찾지 않는다 (None).
             영역만 잘라서 블러·HSV 변환·마스크를 만들므로 영역이 작을수록 빠르다.
        prefer: (x, y) - 조건을 통과한 후보가 여럿이면 이 위치에 가장 가까운 것을 고른다.
             None이면 예전처럼 가장 원형에 가까운 것을 고른다.
        expected_area: (x, y) -> 그 위치에서 공의 예상 면적(px²) 또는 None 을 돌려주는 함수 (25단계).
             값이 나오면 면적을 '예상 면적의 area_ratio_min~max배'로 판정하고, None이면(캘리브레이션
             없음) 프로필의 min_area/max_area(픽셀)로 판정한다.
        static_filter: (x, y, 공조건통과여부) -> True면 '고정 물체(컵·티) 자리'라 공 후보에서 뺀다 (25단계).
        결과 좌표는 roi를 썼더라도 항상 프레임 전체 기준이다.

        찾지 못했거나 처리 중 어떤 문제가 생겨도 예외를 던지지 않고
        조용히 None을 반환한다. (호출하는 UI 쪽이 죽지 않게)
        """
        try:
            return self._detect(frame, roi, prefer, expected_area, static_filter)
        except Exception:
            # HSV 설정값이 잘못됐거나 프레임 형식이 예상과 달라도
            # 검출 실패로만 처리하고 프로그램은 계속 돌아가야 한다.
            return None

    def _detect(self, frame: np.ndarray, roi, prefer, expected_area, static_filter) -> BallDetection | None:
        profile = self._active_profile()
        self.last_size_rejected = 0
        offset = (0, 0)
        if roi is not None:
            x, y, w, h = (int(v) for v in roi)
            if w <= 0 or h <= 0:
                self.last_candidates = []
                return None  # 찾을 곳이 없음
            frame = frame[y : y + h, x : x + w]
            offset = (x, y)
        mask = build_mask(frame, profile["hsv_lower"], profile["hsv_upper"])

        # offset을 주면 윤곽선 좌표가 잘라낸 영역이 아니라 프레임 전체 기준으로 나온다.
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE, offset=offset)
        evaluated = self._evaluate_contours(contours, profile, expected_area, static_filter)
        # 크기 때문에 걸러진 후보 수 (모양은 공 같았는데 너무 크거나 작음 = 컵 등). 상태 요약 로그용
        self.last_size_rejected = sum(1 for c, _contour in evaluated if c.reject == REJECT_SIZE)
        if self.debug_enabled:
            # 디버그 표시용: 큰 덩어리부터 몇 개만 남긴다
            shown = [c for c, _contour in evaluated if c.area >= DEBUG_CANDIDATE_MIN_AREA]
            shown.sort(key=lambda c: c.area, reverse=True)
            self.last_candidates = shown[:DEBUG_CANDIDATE_LIMIT]

        # 조건을 모두 통과한 후보 중 하나만 고른다
        # (prefer가 있으면 그 위치에 가장 가까운 것, 없으면 가장 원형에 가까운 것)
        best = None
        best_key = None
        for candidate, contour in evaluated:
            if not candidate.accepted:
                continue
            if prefer is not None:
                key = (candidate.x - prefer[0]) ** 2 + (candidate.y - prefer[1]) ** 2
            else:
                key = -candidate.circularity
            if best_key is None or key < best_key:
                best_key, best = key, (candidate, contour)
        if best is None:
            return None
        _candidate, contour = best
        (cx, cy), radius = cv2.minEnclosingCircle(contour)
        return BallDetection(x=int(cx), y=int(cy), radius=int(radius), area=float(cv2.contourArea(contour)))

    def find_blobs(self, frame: np.ndarray, expected_area=None) -> list[DetectionCandidate]:
        """화면 전체에서 공 색 범위에 맞는 덩어리를 모두 돌려준다 (고정 물체 수집용, 25단계).

        고정 물체 제외(static_filter)는 하지 않는다. 각 덩어리에 크기·모양 판정 결과가 함께 들어 있다.
        """
        profile = self._active_profile()
        mask = build_mask(frame, profile["hsv_lower"], profile["hsv_upper"])
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        return [c for c, _contour in self._evaluate_contours(contours, profile, expected_area, None)]

    def blob_around(self, frame: np.ndarray, center, half_size: float):
        """center 주변 정사각형(한 변 2 x half_size) 안에서, center를 품은(또는 가장 가까운) 공 색 덩어리를 찾는다.

        26단계(25단계 보완): 홀인 확인 중 '공이 컵 테두리에 붙어서 컵과 한 덩어리로 보이는지' 보려고 쓴다.
        반환: (면적 px², 윤곽선 점들 N x 2 배열, 프레임 좌표) 또는 None (덩어리 없음)
        """
        profile = self._active_profile()
        height, width = frame.shape[:2]
        x0 = max(0, int(center[0] - half_size))
        y0 = max(0, int(center[1] - half_size))
        x1 = min(width, int(center[0] + half_size) + 1)
        y1 = min(height, int(center[1] + half_size) + 1)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return None
        mask = build_mask(frame[y0:y1, x0:x1], profile["hsv_lower"], profile["hsv_upper"])
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE, offset=(x0, y0))
        best = None
        best_distance = float("inf")
        for contour in contours:
            # pointPolygonTest: 안쪽이면 양수, 바깥이면 음수(테두리까지 거리)
            inside = cv2.pointPolygonTest(contour, (float(center[0]), float(center[1])), True)
            distance = 0.0 if inside >= 0 else -inside
            if distance < best_distance:
                best_distance, best = distance, contour
        if best is None:
            return None
        return float(cv2.contourArea(best)), best.reshape(-1, 2)

    def _evaluate_contours(self, contours, profile: dict, expected_area, static_filter) -> list:
        """덩어리마다 면적·원형도·크기 비율을 재고 공 조건 통과 여부를 정한다.

        반환: [(DetectionCandidate, contour), ...]
        판정 순서:
          1) 크기: 예상 면적이 있으면(캘리브레이션 있음) 비율이 area_ratio_min~max 안이어야 한다.
             없으면 프로필의 min_area~max_area(픽셀) 안이어야 한다.
          2) 모양: 원형도가 min_circularity 이상이어야 한다.
          3) 고정 물체: static_filter가 True를 돌려주면(컵·티 자리) 뺀다.
        """
        min_area = profile.get("min_area", DEFAULT_MIN_AREA)
        max_area = profile.get("max_area", DEFAULT_MAX_AREA)
        min_circularity = profile.get("min_circularity", DEFAULT_MIN_CIRCULARITY)
        ratio_min = float(self.ball["area_ratio_min"])
        ratio_max = float(self.ball["area_ratio_max"])
        # 이보다 작은 덩어리는 잔털 잡티라 계산할 필요도 없다 (min_area가 더 작게 설정됐으면 그 값)
        smallest = min(DEBUG_CANDIDATE_MIN_AREA, min_area)

        evaluated = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < smallest:
                continue
            perimeter = cv2.arcLength(contour, True)
            circularity = 4 * np.pi * area / (perimeter * perimeter) if perimeter else 0.0
            (x, y), radius = cv2.minEnclosingCircle(contour)

            expected = expected_area(x, y) if expected_area is not None else None
            if expected:
                ratio = area / expected
                size_ok = ratio_min <= ratio <= ratio_max
            else:
                ratio = None
                size_ok = min_area <= area <= max_area
            shape_ok = circularity >= min_circularity

            reject = None
            if not size_ok:
                reject = REJECT_SIZE
            elif not shape_ok:
                reject = REJECT_SHAPE
            if static_filter is not None and static_filter(x, y, reject is None):
                # 고정 물체 자리. 크기 때문에 이미 걸러졌어도 '컵'이라는 걸 보여주려고 static으로 표시한다
                reject = REJECT_STATIC
            candidate = DetectionCandidate(
                int(x), int(y), float(area), 2 * radius, float(circularity), reject is None, ratio, reject
            )
            evaluated.append((candidate, contour))
        return evaluated


# ======================================================================
# 24단계: 화면에서 공을 클릭해 색(HSV 범위) 자동 추출
# ======================================================================
# 클릭 지점 주변에서 공 픽셀을 찾을 범위(px, 처리 프레임 기준)
SAMPLE_RADIUS_PX = 30
# 채도(S) 중앙값이 이보다 낮으면 '흰색 계열'로 보고 Hue(색상)는 전체(0~179)를 허용한다.
# 흰 공은 색이 거의 없어서 Hue 값이 잡음처럼 제멋대로이기 때문이다.
LOW_SATURATION = 40
# 클릭 지점 색과 '비슷하다'고 볼 차이 (공 모양을 얻을 때)
#   흰색 계열: 밝기(V) 차이, 채도(S)는 클릭 지점보다 이만큼 높아도 됨
#   색이 있는 공: Hue 차이(원형 거리), 채도·밝기 차이
SIMILAR_V_LOW_SAT = 45
SIMILAR_S_LOW_SAT = 35
SIMILAR_H = 12
SIMILAR_S = 70
SIMILAR_V = 80
# 범위를 만들 때 쓰는 백분위수 (양 끝 2%는 잡음으로 보고 버림)
PERCENTILE_LOW = 2
PERCENTILE_HIGH = 98
# 범위에 더하는 여유값 (H, S, V). 조명 차이를 견디게 넓히는 값이다.
# 다른 덩어리가 많이 걸리면 이 여유에 아래 배율을 차례로 곱해 줄여 가며 다시 시도한다.
COLOR_MARGIN = (8, 40, 40)
MARGIN_SCALES = (1.0, 0.75, 0.5, 0.25, 0.0)
# min_area / max_area 제안 = 공 면적 x 이 배율.
# 25단계: 24단계의 0.4~2.5배는 홀컵(공의 약 1.4~2.1배)까지 통과시켜서 0.6~1.3배로 좁혔다
# (mm 기준 판정의 ball.area_ratio_min/max 기본값과 같다. 1.3인 이유는 DEFAULT_BALL_SETTINGS 참고).
SUGGEST_MIN_AREA_RATIO = 0.6
SUGGEST_MAX_AREA_RATIO = 1.3
# 여유를 줄이다가 공 모양이 망가지면 안 된다: 공 덩어리의 원형도가 이 값 미만이거나,
# 면적이 기본 여유(1.0)일 때의 이 비율 미만이 되는 범위는 고르지 않는다.
MIN_PICK_CIRCULARITY = 0.75
MIN_PICK_AREA_KEEP = 0.6


@dataclass
class ColorSample:
    """공을 한 번 클릭해서 얻은 샘플."""

    point: tuple[int, int]  # 클릭한 위치 (처리 프레임 좌표)
    pixels: np.ndarray  # 공으로 판단한 픽셀들의 HSV 값 (N x 3)
    area: int  # 공으로 판단한 픽셀 수


@dataclass
class ColorRangeResult:
    """추출한 색 범위와 시험 적용 결과."""

    hsv_lower: list
    hsv_upper: list
    margin_scale: float  # 쓰인 여유값 배율 (1.0 = 기본 여유)
    ball_found: bool  # 마지막으로 클릭한 위치에서 공 덩어리를 찾았는지
    ball_area: float
    ball_diameter: float
    ball_circularity: float
    other_blobs: int  # 공 말고 '공처럼 보이는' 다른 덩어리 수 (매트 위만, 매트를 모르면 화면 전체)
    suggested_min_area: int
    suggested_max_area: int
    # 25단계: 캘리브레이션으로 계산한 그 위치의 공 예상 면적(px²)과, 측정 면적 / 예상 면적.
    # 캘리브레이션이 없으면 None. 비율이 0.6~1.3을 벗어나면 area_warning에 경고 문구가 들어간다
    # (공 지름 설정이 틀렸거나, 공이 아닌 곳을 찍었거나, 색 범위가 공을 덜/더 잡는 경우).
    expected_area: float | None = None
    area_ratio: float | None = None
    area_warning: str | None = None


def sample_ball_pixels(frame: np.ndarray, point, radius: int = SAMPLE_RADIUS_PX) -> ColorSample | None:
    """클릭 지점 주변에서 '클릭 지점과 색·밝기가 비슷하고 이어져 있는' 영역을 공으로 보고 픽셀을 모은다.

    단순히 원으로 자르면 공 바깥의 매트 픽셀이 섞여서 범위가 엉뚱하게 넓어진다.
    그래서 클릭 지점 색과 비슷한 픽셀만 고른 마스크를 만들고, 그중 클릭 지점과
    붙어 있는 덩어리(연결 영역) 하나만 쓴다. 클릭 지점이 영상 밖이면 None.
    """
    height, width = frame.shape[:2]
    px, py = int(point[0]), int(point[1])
    if not (0 <= px < width and 0 <= py < height):
        return None
    x0, y0 = max(0, px - radius), max(0, py - radius)
    x1, y1 = min(width, px + radius + 1), min(height, py + radius + 1)
    # 검출(build_mask)과 같은 블러를 거친 HSV 값을 봐야 범위가 실제 검출과 맞는다
    hsv = blurred_hsv(frame[y0:y1, x0:x1]).astype(np.int32)
    cx, cy = px - x0, py - y0
    # 클릭 지점 3x3의 중앙값을 기준 색으로 쓴다 (한 픽셀만 보면 잡음에 흔들림)
    seed = np.median(hsv[max(0, cy - 1) : cy + 2, max(0, cx - 1) : cx + 2].reshape(-1, 3), axis=0)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    if seed[1] < LOW_SATURATION:
        similar = (np.abs(v - seed[2]) <= SIMILAR_V_LOW_SAT) & (s <= seed[1] + SIMILAR_S_LOW_SAT)
    else:
        hue_diff = np.abs(h - seed[0])
        hue_diff = np.minimum(hue_diff, 180 - hue_diff)  # Hue는 원형(179 다음이 0)
        similar = (
            (hue_diff <= SIMILAR_H) & (np.abs(s - seed[1]) <= SIMILAR_S) & (np.abs(v - seed[2]) <= SIMILAR_V)
        )
    mask = similar.astype(np.uint8)
    _count, labels = cv2.connectedComponents(mask)
    label = labels[cy, cx]
    if label == 0:
        return None
    region = labels == label
    pixels = hsv[region].astype(np.uint8)
    return ColorSample((px, py), pixels, int(region.sum()))


def color_range_from_pixels(pixels: np.ndarray, margin_scale: float = 1.0) -> tuple[list, list]:
    """공 픽셀들의 HSV 분포로 범위(lower, upper)를 만든다.

    각 값의 2%~98% 백분위수에 여유값(COLOR_MARGIN x margin_scale)을 더한다.
    - 채도 중앙값이 낮으면(흰 공) Hue는 의미가 없으니 0~179 전체를 허용한다.
    - Hue는 원형이라 공 색이 빨강 경계(0/179) 근처면 범위가 경계를 넘을 수 있다.
      이때는 lower H > upper H로 돌려준다 (예: 172 ~ 6). hsv_in_range가 두 구간으로 처리한다.
    """
    h = pixels[:, 0].astype(np.float64)
    s = pixels[:, 1].astype(np.float64)
    v = pixels[:, 2].astype(np.float64)
    mh, ms, mv = (m * margin_scale for m in COLOR_MARGIN)

    s_low = int(max(0, np.percentile(s, PERCENTILE_LOW) - ms))
    s_high = int(min(255, np.percentile(s, PERCENTILE_HIGH) + ms))
    v_low = int(max(0, np.percentile(v, PERCENTILE_LOW) - mv))
    v_high = int(min(255, np.percentile(v, PERCENTILE_HIGH) + mv))

    if np.median(s) < LOW_SATURATION:
        return [0, s_low, v_low], [179, s_high, v_high]

    # Hue 원형 평균을 구해서, 평균이 90이 되도록 돌린 뒤(경계를 피함) 백분위수를 잰다.
    angles = h * (2 * np.pi / 180)
    mean = (np.degrees(np.arctan2(np.sin(angles).mean(), np.cos(angles).mean())) / 2) % 180
    shifted = (h - mean + 90) % 180
    low = np.percentile(shifted, PERCENTILE_LOW) - mh
    high = np.percentile(shifted, PERCENTILE_HIGH) + mh
    if high - low >= 179:
        return [0, s_low, v_low], [179, s_high, v_high]
    h_low = int(round(low + mean - 90)) % 180
    h_high = int(round(high + mean - 90)) % 180
    return [h_low, s_low, v_low], [h_high, s_high, v_high]


def evaluate_color_range(frame: np.ndarray, lower, upper, ball_point, mat_corners=None) -> dict:
    """추출한 범위를 프레임 전체에 시험 적용한다.

    반환: ball_found, ball_area, ball_diameter, ball_circularity, other_blobs
    - 공 덩어리: 클릭 위치를 포함하는(또는 가장 가까운, 공 반경 안) 덩어리
    - other_blobs: 공 덩어리 말고, 실제로 공으로 잘못 잡힐 수 있는 덩어리 수.
      즉 제안 면적 범위(공 면적의 0.6~1.3배) 안이고 원형도가 기본 기준(0.7) 이상인 것만 센다.
      (흩어진 잔털 점이나 길쭉한 물체는 어차피 공 조건에서 걸러지므로 세지 않는다)
      mat_corners(매트 4점)가 있으면 매트 안에 있는 것만 센다.
    """
    mask = build_mask(frame, lower, upper)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    ball = None
    best_distance = float("inf")
    for contour in contours:
        inside = cv2.pointPolygonTest(contour, (float(ball_point[0]), float(ball_point[1])), True)
        distance = 0.0 if inside >= 0 else -inside
        if distance < best_distance:
            best_distance, ball = distance, contour
    result = {"ball_found": False, "ball_area": 0.0, "ball_diameter": 0.0, "ball_circularity": 0.0, "other_blobs": 0}
    if ball is not None and best_distance <= SAMPLE_RADIUS_PX / 2:
        area = cv2.contourArea(ball)
        perimeter = cv2.arcLength(ball, True)
        (_x, _y), radius = cv2.minEnclosingCircle(ball)
        result.update(
            ball_found=area > 0,
            ball_area=float(area),
            ball_diameter=float(2 * radius),
            ball_circularity=float(4 * np.pi * area / (perimeter * perimeter)) if perimeter else 0.0,
        )
    else:
        ball = None
    ball_area = result["ball_area"]
    min_other = max(10.0, SUGGEST_MIN_AREA_RATIO * ball_area)
    max_other = SUGGEST_MAX_AREA_RATIO * ball_area if ball_area > 0 else float("inf")
    mat = np.array(mat_corners, dtype=np.int32) if mat_corners is not None and len(mat_corners) == 4 else None
    others = 0
    for contour in contours:
        if contour is ball:
            continue
        area = cv2.contourArea(contour)
        if not min_other <= area <= max_other:
            continue
        perimeter = cv2.arcLength(contour, True)
        if not perimeter or 4 * np.pi * area / (perimeter * perimeter) < DEFAULT_MIN_CIRCULARITY:
            continue
        if mat is not None:
            (cx, cy), _r = cv2.minEnclosingCircle(contour)
            if cv2.pointPolygonTest(mat, (float(cx), float(cy)), False) < 0:
                continue
        others += 1
    result["other_blobs"] = others
    return result


def auto_color_range(
    frame: np.ndarray, samples: list, mat_corners=None, expected_area: float | None = None
) -> ColorRangeResult | None:
    """샘플들(여러 번 클릭한 것 합침)로 범위를 만들고, 다른 덩어리가 적은 범위를 고른다.

    여유값을 기본(1.0)부터 차례로 줄여 가며(MARGIN_SCALES) 시험한다.
    - 마지막 클릭 위치에서 공이 잡히고 다른 덩어리가 0개인 첫 범위를 고른다.
    - 그런 범위가 없으면 공이 잡히는 범위 중 다른 덩어리가 가장 적은 것을 고른다
      (같으면 여유가 큰 쪽 = 조명 변화에 강한 쪽).
    시험은 지금 프레임(마지막 샘플을 찍은 화면)에서 한다.
    expected_area: 마지막 클릭 위치에서 공의 예상 면적(px², 캘리브레이션으로 계산). 주면 측정 면적과
        비교해서 차이가 크면(0.6~1.3배 밖) 경고 문구를 남긴다 (25단계).
    """
    if not samples:
        return None
    pixels = np.concatenate([sample.pixels for sample in samples], axis=0)
    point = samples[-1].point
    best = None
    best_key = None
    reference_area = None  # 기본 여유(첫 단계)일 때의 공 면적
    for scale in MARGIN_SCALES:
        lower, upper = color_range_from_pixels(pixels, scale)
        report = evaluate_color_range(frame, lower, upper, point, mat_corners)
        if not report["ball_found"]:
            continue
        if reference_area is None:
            reference_area = report["ball_area"]
        elif (
            report["ball_circularity"] < MIN_PICK_CIRCULARITY
            or report["ball_area"] < reference_area * MIN_PICK_AREA_KEEP
        ):
            # 여유를 너무 줄여 공 모양이 깎임 -> 더 줄여 봐야 나빠지기만 하므로 멈춘다
            break
        key = (report["other_blobs"], -scale)
        if best_key is None or key < best_key:
            best_key, best = key, (scale, lower, upper, report)
        if report["other_blobs"] == 0:
            break
    if best is None:
        # 어떤 여유로도 공 덩어리가 안 잡힘 (공이 너무 작거나 클릭 위치가 공이 아님): 기본 여유 범위라도 돌려준다
        lower, upper = color_range_from_pixels(pixels, MARGIN_SCALES[0])
        report = evaluate_color_range(frame, lower, upper, point, mat_corners)
        best = (MARGIN_SCALES[0], lower, upper, report)
    scale, lower, upper, report = best
    area = report["ball_area"]
    ratio = None
    warning = None
    if expected_area and area > 0:
        ratio = area / expected_area
        if not SUGGEST_MIN_AREA_RATIO <= ratio <= SUGGEST_MAX_AREA_RATIO:
            warning = (
                f"잡힌 공 면적({area:.0f}px²)이 캘리브레이션으로 계산한 공 크기({expected_area:.0f}px²)의 "
                f"x{ratio:.2f}입니다. 공을 정확히 찍었는지, 매트 크기·공 지름 설정이 맞는지 확인하세요."
            )
    return ColorRangeResult(
        hsv_lower=lower,
        hsv_upper=upper,
        margin_scale=scale,
        ball_found=report["ball_found"],
        ball_area=area,
        ball_diameter=report["ball_diameter"],
        ball_circularity=report["ball_circularity"],
        other_blobs=report["other_blobs"],
        suggested_min_area=int(round(area * SUGGEST_MIN_AREA_RATIO)),
        suggested_max_area=int(round(area * SUGGEST_MAX_AREA_RATIO)),
        expected_area=expected_area,
        area_ratio=ratio,
        area_warning=warning,
    )
