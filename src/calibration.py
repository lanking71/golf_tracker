"""
매트 캘리브레이션 모듈.

카메라 화면에서 클릭한 매트 네 모서리(좌상단 -> 우상단 -> 우하단 ->
좌하단 순서)로 cv2.getPerspectiveTransform 원근 변환 행렬을 만들고,
config/settings.json에 저장/로드한다.

매트의 실제 크기(mm)도 여기서 관리한다. 기본값은 지금 매트의 측정 영역인
3000x1000(mm)이고, 다른 규격(예: 4500x2000) 현장은 config/settings.json의
mat_width_mm/mat_height_mm(또는 설정창)에서 바꾼다.

23단계: 녹색 매트 자동 검출은 매트 모양에 따라 틀린 4점으로 '성공'할 수 있어서
기본은 사람이 4점을 직접 찍는다(auto_detect = False). 마지막 점 취소(undo_last_corner),
점 옮기기용 가장 가까운 점 찾기(nearest_corner), 매트 크기 오타를 잡는 비율 검사
(aspect_ratio_warning)도 여기 있다.

pixel_to_real()로 카메라 픽셀 좌표를 매트 위의 실제 mm 좌표로 바꾼다
(10단계에서 좌표 배지·요약 통계·궤적 패널에 실제로 적용했다).

is_inside()로 어떤 픽셀 좌표가 캘리브레이션된 매트 네 모서리 안쪽에
있는지 확인할 수 있다. config/settings.json의 filter_outside_mat이
true(기본값)면, 매트 밖에서 검출된 공은 오검출로 보고 무시한다.

22단계: 모서리 좌표는 '전처리(회전·축소·왜곡 보정)가 끝난 프레임' 기준이다.
그래서 저장할 때 그때의 처리 프레임 크기(frame_size)와 왜곡 보정 값(undistort_k1)을
함께 저장하고(stamp), 나중에 둘 중 하나라도 바뀌면 저장된 모서리가 화면과 맞지 않으므로
엔진이 '캘리브레이션 안 된 상태'로 취급한다(matches_processing / invalidate).
예전(21단계까지) 설정 파일에는 이 값이 없으므로 자동으로 무효 처리된다.
"""

import math

import numpy as np
import cv2

from src.config import load_settings, save_settings

# config/settings.json에 "calibration" 항목이 없거나 값이 빠져 있을 때 쓸 기본값.
# 23단계: 지금 설치된 매트의 실제 측정 영역은 가로 3000mm x 세로 1000mm다
# (화면에서 긴 쪽이 가로). 4500x2000 같은 다른 규격 현장은 설정창에서 바꾼다.
DEFAULT_MAT_WIDTH_MM = 3000
DEFAULT_MAT_HEIGHT_MM = 1000
DEFAULT_FILTER_OUTSIDE_MAT = True

# 설정창에서 입력할 수 있는 매트 크기 범위(mm). 오타(예: 1000을 10000으로)를 줄이려고
# 현실적인 값으로 제한한다.
MAT_WIDTH_RANGE_MM = (500, 10000)
MAT_HEIGHT_RANGE_MM = (300, 5000)

# 녹색 매트 자동 검출 사용 여부의 기본값 (23단계).
# 매트 모양이 현장마다 달라(계단 모양, 퍼팅 그린이 붙어 있음 등) 자동 검출은
# 틀린 4점으로 "성공"할 수 있다. 그래서 기본은 사람이 4점을 직접 찍는 수동 모드다.
# true로 바꾸면 자동 검출 결과를 바로 저장하지 않고 '제안'으로만 보여준다.
DEFAULT_AUTO_DETECT = False

# 찍은 4점의 가로:세로 비율이 입력한 매트 크기의 비율과 이만큼(30%) 이상 다르면 경고한다.
ASPECT_WARNING_TOLERANCE = 0.3

# 녹색 매트 자동 검출용 HSV 기본 범위 (OpenCV: H 0~179, S/V 0~255).
# 조명에 따라 다르므로 config/settings.json의 calibration.mat_hsv_lower/upper로 조정한다.
DEFAULT_MAT_HSV_LOWER = [35, 40, 40]
DEFAULT_MAT_HSV_UPPER = [85, 255, 255]
# 녹색 영역이 화면의 이 비율보다 작으면 매트가 아닌 잡음으로 보고 실패 처리한다.
MIN_MAT_AREA_RATIO = 0.2

# 모서리를 찍는 순서 (상태 배지에 다음에 찍을 위치를 안내하는 데 쓴다)
CORNER_NAMES = ["좌상단", "우상단", "우하단", "좌하단"]


# 모서리 네 점이 이루는 사각형의 넓이가 이보다 작으면(px²) 사각형으로 보지 않는다.
MIN_CORNERS_AREA_PX = 100


def corners_aspect_ratio(corners) -> float | None:
    """찍은 4점(좌상, 우상, 우하, 좌하)이 화면에서 이루는 '가로 / 세로' 비율.

    가로 길이 = 윗변과 아랫변 길이의 평균, 세로 길이 = 왼쪽 변과 오른쪽 변 길이의 평균.
    점이 4개가 아니거나 세로 길이가 0이면 None.
    """
    if len(corners) != 4:
        return None
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = [(float(x), float(y)) for x, y in corners]
    top = math.hypot(x1 - x0, y1 - y0)
    bottom = math.hypot(x2 - x3, y2 - y3)
    left = math.hypot(x3 - x0, y3 - y0)
    right = math.hypot(x2 - x1, y2 - y1)
    height = (left + right) / 2
    if height == 0:
        return None
    return ((top + bottom) / 2) / height


def aspect_ratio_warning(corners, width_mm, height_mm, tolerance: float = ASPECT_WARNING_TOLERANCE) -> str | None:
    """찍은 4점의 모양과 입력한 매트 크기의 비율이 크게 다르면 경고 문구를, 아니면 None을 돌려준다.

    매트 크기 오타(예: 세로 1000을 10000으로 입력)를 잡기 위한 것이다.
    두 비율 중 큰 값 / 작은 값이 (1 + tolerance) 이상이면 '크게 다르다'고 본다.
    카메라가 비스듬히 보면 실제로도 비율이 좀 달라질 수 있어서 저장을 막지는 않고 경고만 한다.
    """
    pixel_ratio = corners_aspect_ratio(corners)
    if pixel_ratio is None or pixel_ratio <= 0 or not width_mm or not height_mm:
        return None
    mm_ratio = width_mm / height_mm
    if mm_ratio <= 0:
        return None
    if max(pixel_ratio, mm_ratio) / min(pixel_ratio, mm_ratio) < 1 + tolerance:
        return None
    return (
        f"입력한 매트 크기({width_mm:g}x{height_mm:g})와 화면에서 찍은 모양의 비율이 크게 다릅니다. "
        f"크기를 확인하세요 (화면 비율 {pixel_ratio:.2f} : 입력 비율 {mm_ratio:.2f})"
    )


def check_corners(corners, frame_size: tuple[int, int] | None = None) -> str | None:
    """직접 전달받은 매트 모서리 4점(픽셀 좌표)이 쓸 수 있는 값인지 검사한다.

    메인 컨트롤이 ALIGN에 corners를 실어 보낼 때 잘못된 값이 캘리브레이션을
    망가뜨리지 않도록, 적용하기 전에 확인한다. 문제가 없으면 None, 있으면 이유 코드:
    - "bad_corners": 형식이 틀림 (점이 4개가 아님, 숫자가 아님), 사각형이 아님
      (넓이가 너무 작음, 볼록하지 않음, 꼬임), 순서가 틀림
    - "corners_out_of_frame": 점이 카메라 화면 밖에 있음 (frame_size를 알 때만 검사)

    순서는 좌상 -> 우상 -> 우하 -> 좌하이다. 화면 좌표는 y가 아래쪽이라 이 순서는
    화면에서 시계 방향이며, 네 꼭짓점의 외적이 모두 양수일 때만 통과시킨다
    (순서가 반대거나 뒤섞이면 실제 mm 좌표가 좌우·상하 뒤집혀 나오기 때문이다).
    """
    if not isinstance(corners, (list, tuple)) or len(corners) != 4:
        return "bad_corners"

    points = []
    for corner in corners:
        if not isinstance(corner, (list, tuple)) or len(corner) != 2:
            return "bad_corners"
        for value in corner:
            # True/False는 파이썬에서 int지만 좌표로 인정하지 않는다
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return "bad_corners"
            if not math.isfinite(value):
                return "bad_corners"
        points.append((float(corner[0]), float(corner[1])))

    # 모든 꼭짓점에서 같은 방향(시계)으로 꺾여야 한다 -> 볼록 + 순서 올바름 + 꼬임 없음
    for i in range(4):
        ax, ay = points[i]
        bx, by = points[(i + 1) % 4]
        cx, cy = points[(i + 2) % 4]
        if (bx - ax) * (cy - by) - (by - ay) * (cx - bx) <= 0:
            return "bad_corners"

    # 순서를 한 칸 돌린 목록(우상, 우하, 좌하, 좌상)도 시계 방향이라 위 검사를 통과하지만
    # 그러면 mm 좌표가 90도 돌아서 나온다. 시계 방향으로 돌 때 윗변(좌상 -> 우상)은 화면에서
    # 가장 오른쪽을 향하는 변이므로, 첫 변이 네 변 중 가장 오른쪽을 향하는지 확인한다.
    # (점들의 x+y 합 같은 값으로 순서를 추정하면 매트가 많이 기울어 보일 때 올바른 순서도
    # 틀렸다고 판단할 수 있어서, 변의 방향으로 판단한다.)
    def rightward(i: int) -> float:
        (ax, ay), (bx, by) = points[i], points[(i + 1) % 4]
        return (bx - ax) / math.hypot(bx - ax, by - ay)

    if any(rightward(i) > rightward(0) for i in range(1, 4)):
        return "bad_corners"

    # 신발끈 공식으로 넓이를 구해 너무 작은 사각형을 걸러낸다
    area = 0.0
    for i in range(4):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % 4]
        area += x1 * y2 - x2 * y1
    if abs(area) / 2 < MIN_CORNERS_AREA_PX:
        return "bad_corners"

    if frame_size is not None:
        width, height = frame_size
        if any(not (0 <= x <= width and 0 <= y <= height) for x, y in points):
            return "corners_out_of_frame"
    return None


def _order_corners(points) -> list[tuple[int, int]]:
    """꼭짓점 4개를 좌상 -> 우상 -> 우하 -> 좌하 순서로 정렬한다.

    x+y가 가장 작은 점이 좌상, 가장 큰 점이 우하이고,
    y-x가 가장 작은 점이 우상, 가장 큰 점이 좌하이다.
    """
    pts = np.array(points, dtype=np.float32).reshape(4, 2)
    s = pts.sum(axis=1)
    d = pts[:, 1] - pts[:, 0]
    ordered = [pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]]
    return [(int(round(p[0])), int(round(p[1]))) for p in ordered]


def detect_mat_corners(frame: np.ndarray, hsv_lower, hsv_upper) -> list[tuple[int, int]] | None:
    """카메라 프레임에서 녹색 매트의 네 모서리를 자동으로 찾는다.

    순서: HSV 녹색 마스크 -> 잡음 제거(모폴로지) -> 가장 큰 덩어리 ->
    다각형 근사로 꼭짓점 4개 추출. 4개로 안 떨어지면 최소 외접 사각형으로 대체한다.
    매트를 못 찾으면(덩어리가 없거나 너무 작으면) None을 반환한다.
    반환 순서는 좌상, 우상, 우하, 좌하.
    """
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(
        hsv, np.array(hsv_lower, dtype=np.uint8), np.array(hsv_upper, dtype=np.uint8)
    )
    # close: 매트 안의 작은 구멍(공, 그림자) 메우기 / open: 바깥의 작은 점 잡음 제거
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest) < MIN_MAT_AREA_RATIO * frame.shape[0] * frame.shape[1]:
        return None

    # 외곽선 길이의 2%까지 오차를 허용하며 꼭짓점 수를 줄여본다
    approx = cv2.approxPolyDP(largest, 0.02 * cv2.arcLength(largest, True), True)
    if len(approx) == 4:
        return _order_corners(approx)
    # 꼭짓점이 4개가 아니면(둥글게 찍히거나 가려진 경우) 최소 외접 사각형으로 대체
    return _order_corners(cv2.boxPoints(cv2.minAreaRect(largest)))


class Calibration:
    """매트 네 모서리 클릭 + 원근 변환 행렬을 관리하는 클래스."""

    def __init__(self, settings: dict | None = None):
        loaded = settings if settings is not None else load_settings().get("calibration", {})

        self.mat_width_mm = loaded.get("mat_width_mm", DEFAULT_MAT_WIDTH_MM)
        self.mat_height_mm = loaded.get("mat_height_mm", DEFAULT_MAT_HEIGHT_MM)
        self.filter_outside_mat = loaded.get("filter_outside_mat", DEFAULT_FILTER_OUTSIDE_MAT)
        self.mat_hsv_lower = loaded.get("mat_hsv_lower", DEFAULT_MAT_HSV_LOWER)
        self.mat_hsv_upper = loaded.get("mat_hsv_upper", DEFAULT_MAT_HSV_UPPER)
        # 녹색 매트 자동 검출을 쓸지 (기본 False = 사람이 4점을 직접 찍음)
        self.auto_detect = bool(loaded.get("auto_detect", DEFAULT_AUTO_DETECT))

        corners = loaded.get("corners") or []
        # 저장된 값이 깨져 있어도(예: 형식이 다름) 크래시 없이 빈 목록으로 시작한다.
        try:
            self.corners: list[tuple[int, int]] = [(int(x), int(y)) for x, y in corners]
        except (TypeError, ValueError):
            self.corners = []

        # 이 모서리를 찍었을 때의 처리 프레임 크기 (폭, 높이)와 왜곡 보정 k1.
        # 없으면(예전 설정 파일) None -> 어떤 처리 조건과도 맞지 않는 것으로 본다.
        self.frame_size: tuple[int, int] | None = None
        frame_size = loaded.get("frame_size")
        try:
            if frame_size is not None:
                self.frame_size = (int(frame_size[0]), int(frame_size[1]))
        except (TypeError, ValueError, IndexError):
            self.frame_size = None
        k1 = loaded.get("undistort_k1")
        self.undistort_k1: float | None = (
            float(k1) if isinstance(k1, (int, float)) and not isinstance(k1, bool) else None
        )

        self.matrix: np.ndarray | None = None
        matrix = loaded.get("perspective_matrix")
        if matrix is not None:
            try:
                self.matrix = np.array(matrix, dtype=np.float64)
            except (TypeError, ValueError):
                self.matrix = None

    def is_calibrated(self) -> bool:
        """네 모서리로 변환 행렬이 만들어져 있는지."""
        return self.matrix is not None

    def stamp(self, frame_size: tuple[int, int] | None, undistort_k1: float) -> None:
        """'이 모서리는 이 처리 조건(프레임 크기·k1)에서 찍었다'고 기록한다 (저장 전에 부른다)."""
        self.frame_size = (int(frame_size[0]), int(frame_size[1])) if frame_size else None
        self.undistort_k1 = float(undistort_k1)

    def matches_processing(self, frame_size: tuple[int, int] | None, undistort_k1: float) -> bool:
        """저장된 처리 조건이 지금 조건과 같은지.

        frame_size가 None이면(아직 첫 프레임을 못 받아 크기를 모르면) k1만 비교한다.
        저장된 값이 없으면(예전 설정 파일) 항상 False.
        """
        if self.undistort_k1 is None or self.frame_size is None:
            return False
        if abs(self.undistort_k1 - float(undistort_k1)) > 1e-9:
            return False
        if frame_size is not None and tuple(frame_size) != self.frame_size:
            return False
        return True

    def invalidate(self) -> None:
        """처리 조건이 바뀌어 저장된 모서리가 화면과 맞지 않을 때: 미보정 상태로 만든다.

        메모리에서만 지운다 (settings.json의 예전 값은 그대로 남는다 - 값을 되돌리면
        다시 쓸 수 있게). 새로 캘리브레이션해서 저장하면 그때 덮어쓴다.
        """
        self.corners = []
        self.matrix = None

    def add_corner(self, x: int, y: int) -> bool:
        """모서리 좌표를 하나 추가한다.

        이미 4개를 다 찍었으면 아무 것도 하지 않고 False를 반환한다
        (그 상태에서 다시 찍고 싶으면 reset_corners()를 먼저 호출해야 한다).
        4번째 점을 찍는 순간 변환 행렬을 계산한다.
        """
        if len(self.corners) >= 4:
            return False
        self.corners.append((x, y))
        if len(self.corners) == 4:
            self._compute_matrix()
        return True

    def set_corners(self, corners: list[tuple[int, int]]) -> None:
        """모서리 4점을 한 번에 설정하고 변환 행렬을 계산한다 (자동 검출 결과 적용용)."""
        self.corners = [(int(x), int(y)) for x, y in corners]
        self._compute_matrix()

    def auto_calibrate(self, frame: np.ndarray) -> bool:
        """프레임에서 녹색 매트를 자동 검출해 캘리브레이션한다. 성공 여부를 반환한다."""
        corners = detect_mat_corners(frame, self.mat_hsv_lower, self.mat_hsv_upper)
        if corners is None:
            return False
        self.set_corners(corners)
        return True

    def undo_last_corner(self) -> bool:
        """마지막으로 찍은 점 하나를 지운다 (화면에서 오른쪽 클릭). 지운 점이 없으면 False.

        4점이 다 찍혀 있었다면 변환 행렬도 지운다 (점이 3개면 행렬을 만들 수 없다).
        """
        if not self.corners:
            return False
        self.corners.pop()
        self.matrix = None
        return True

    def nearest_corner(self, x: float, y: float, max_distance: float) -> int | None:
        """(x, y)에서 max_distance(px) 안에 있는 가장 가까운 모서리의 번호(0~3). 없으면 None.

        4점을 다 찍은 뒤 점을 드래그해서 옮길 때 '어느 점을 잡았는지' 찾는 데 쓴다.
        """
        best_index, best_distance = None, max_distance
        for index, (cx, cy) in enumerate(self.corners):
            distance = math.hypot(cx - x, cy - y)
            if distance <= best_distance:
                best_index, best_distance = index, distance
        return best_index

    def aspect_warning(self) -> str | None:
        """지금 모서리 모양과 매트 크기의 비율이 크게 다르면 경고 문구 (aspect_ratio_warning 참고)."""
        return aspect_ratio_warning(self.corners, self.mat_width_mm, self.mat_height_mm)

    def reset_corners(self) -> None:
        """'다시 지정': 지금까지 찍은 모서리와 변환 행렬을 지운다."""
        self.corners = []
        self.matrix = None

    def set_mat_size(self, width_mm: float, height_mm: float) -> None:
        """매트 실제 크기를 바꾼다.

        이미 모서리 4점이 찍혀 있으면(캘리브레이션 완료 상태) 모서리를
        다시 찍을 필요 없이 새 크기 기준으로 변환 행렬을 즉시 다시
        계산한다 - 그렇게 하지 않으면 예전 크기로 계산된 행렬이 남아
        좌표가 실제와 어긋나게 된다.
        """
        self.mat_width_mm = width_mm
        self.mat_height_mm = height_mm
        if len(self.corners) == 4:
            self._compute_matrix()

    def _compute_matrix(self) -> None:
        """찍힌 네 모서리(좌상->우상->우하->좌하)로 원근 변환 행렬을 만든다."""
        src = np.array(self.corners, dtype=np.float32)
        dst = np.array(
            [
                [0, 0],
                [self.mat_width_mm, 0],
                [self.mat_width_mm, self.mat_height_mm],
                [0, self.mat_height_mm],
            ],
            dtype=np.float32,
        )
        self.matrix = cv2.getPerspectiveTransform(src, dst)

    def pixel_to_real(self, x: int, y: int) -> tuple[float, float] | None:
        """카메라 픽셀 좌표를 매트 위의 실제 좌표(mm)로 변환한다.

        아직 캘리브레이션이 안 됐으면(변환 행렬이 없으면) None을 반환한다.
        """
        if self.matrix is None:
            return None
        point = np.array([[[x, y]]], dtype=np.float32)
        transformed = cv2.perspectiveTransform(point, self.matrix.astype(np.float32))
        return float(transformed[0][0][0]), float(transformed[0][0][1])

    def is_inside(self, x: int, y: int) -> bool:
        """(x, y)가 캘리브레이션된 매트 영역(모서리 4점 사각형) 안에 있는지 확인한다.

        아직 모서리 4점을 다 안 찍었으면(판단할 기준이 없으면) 항상
        True를 반환한다 - 캘리브레이션 전까지는 아무것도 걸러내지 않는다.
        """
        if len(self.corners) < 4:
            return True
        contour = np.array(self.corners, dtype=np.int32)
        return cv2.pointPolygonTest(contour, (float(x), float(y)), False) >= 0

    def save(self) -> None:
        """현재 캘리브레이션 상태(매트 크기·모서리·변환 행렬·필터 설정)를 config/settings.json에 저장한다."""
        settings = load_settings()
        settings["calibration"] = {
            "mat_width_mm": self.mat_width_mm,
            "mat_height_mm": self.mat_height_mm,
            "filter_outside_mat": self.filter_outside_mat,
            "auto_detect": self.auto_detect,
            "mat_hsv_lower": list(self.mat_hsv_lower),
            "mat_hsv_upper": list(self.mat_hsv_upper),
            "corners": [list(c) for c in self.corners],
            "perspective_matrix": self.matrix.tolist() if self.matrix is not None else None,
            # 이 모서리를 찍은 처리 조건 (22단계). 바뀌면 엔진이 캘리브레이션을 무효로 본다.
            "frame_size": list(self.frame_size) if self.frame_size is not None else None,
            "undistort_k1": self.undistort_k1,
        }
        save_settings(settings)
