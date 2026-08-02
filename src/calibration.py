"""
매트 캘리브레이션 모듈.

카메라 화면에서 클릭한 매트 네 모서리(좌상단 -> 우상단 -> 우하단 ->
좌하단 순서)로 cv2.getPerspectiveTransform 원근 변환 행렬을 만들고,
config/settings.json에 저장/로드한다.

매트의 실제 크기(mm)도 여기서 관리한다. 집에서 수건으로 연습할 때는
실측값을, 실전 매트에서는 4000x2000(mm)을 config/settings.json의
mat_width_mm/mat_height_mm에 넣으면 된다.

pixel_to_real()로 카메라 픽셀 좌표를 매트 위의 실제 mm 좌표로 바꾼다
(10단계에서 좌표 배지·요약 통계·궤적 패널에 실제로 적용했다).

is_inside()로 어떤 픽셀 좌표가 캘리브레이션된 매트 네 모서리 안쪽에
있는지 확인할 수 있다. config/settings.json의 filter_outside_mat이
true(기본값)면, 매트 밖에서 검출된 공은 오검출로 보고 무시한다.
"""

import numpy as np
import cv2

from src.config import load_settings, save_settings

# config/settings.json에 "calibration" 항목이 없거나 값이 빠져 있을 때 쓸 기본값.
# 4000x2000mm은 실전 매트 크기. 집에서 수건으로 테스트할 때는 이 값을
# 수건 실측 크기로 바꿔서 쓴다.
DEFAULT_MAT_WIDTH_MM = 4000
DEFAULT_MAT_HEIGHT_MM = 2000
DEFAULT_FILTER_OUTSIDE_MAT = True

# 모서리를 찍는 순서 (상태 배지에 다음에 찍을 위치를 안내하는 데 쓴다)
CORNER_NAMES = ["좌상단", "우상단", "우하단", "좌하단"]


class Calibration:
    """매트 네 모서리 클릭 + 원근 변환 행렬을 관리하는 클래스."""

    def __init__(self, settings: dict | None = None):
        loaded = settings if settings is not None else load_settings().get("calibration", {})

        self.mat_width_mm = loaded.get("mat_width_mm", DEFAULT_MAT_WIDTH_MM)
        self.mat_height_mm = loaded.get("mat_height_mm", DEFAULT_MAT_HEIGHT_MM)
        self.filter_outside_mat = loaded.get("filter_outside_mat", DEFAULT_FILTER_OUTSIDE_MAT)

        corners = loaded.get("corners") or []
        # 저장된 값이 깨져 있어도(예: 형식이 다름) 크래시 없이 빈 목록으로 시작한다.
        try:
            self.corners: list[tuple[int, int]] = [(int(x), int(y)) for x, y in corners]
        except (TypeError, ValueError):
            self.corners = []

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

    def reset_corners(self) -> None:
        """'다시 지정': 지금까지 찍은 모서리와 변환 행렬을 지운다."""
        self.corners = []
        self.matrix = None

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
            "corners": [list(c) for c in self.corners],
            "perspective_matrix": self.matrix.tolist() if self.matrix is not None else None,
        }
        save_settings(settings)
