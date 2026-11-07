"""
"전체 보기" 화면 계산 모듈 (27단계): 캘리브레이션한 영역을 위에서 반듯하게 펼쳐 보여준다.

카메라는 매트를 비스듬히 내려다보고 렌즈 때문에 가장자리가 휘어 보인다. 캘리브레이션 행렬
(Calibration.matrix)은 '처리 프레임 픽셀 → 매트 mm'로 바꾸는 원근 변환이므로, 여기에
'mm → 출력 화면 픽셀' 비율(S)을 곱하면 '처리 프레임 픽셀 → 출력 화면 픽셀' 변환이 된다.

    출력 픽셀 = S @ matrix @ 처리 프레임 픽셀

    S = [[출력 폭 / 매트 가로mm, 0,                   0],
         [0,                   출력 높이 / 매트 세로mm, 0],
         [0,                   0,                   1]]

이 변환으로 프레임 전체를 cv2.warpPerspective 하면, 측정 영역(예: 3000x1000mm)이 출력 화면
(예: 1440x480)을 꽉 채우는 반듯한 직사각형이 된다. 매트 좌상단 모서리 = 출력 (0, 0).

주의: 이 변환은 '매트 바닥면' 기준이다. 높이가 있는 물체(다리, 퍼터, 컵 테두리)는 비스듬히
늘어나 보이는 것이 정상이다 (카메라에서 멀어지는 방향으로 길게 보인다).

이 모듈은 Qt 위젯을 모른다 (numpy·OpenCV만 씀). 화면 그리기 쪽(main_window)이 매 프레임
render()를 불러 결과 이미지를 받아 간다. 엔진 처리와는 상관없다 (화면 갱신 때만 계산).
"""

import cv2
import numpy as np

# 전체 보기 출력 화면의 가로 크기(px). 세로는 매트 비율로 정한다 (3000x1000mm면 1440x480).
DEFAULT_VIEW_WIDTH = 1440


class MatView:
    """캘리브레이션 행렬과 매트 크기로 '전체 보기' 화면을 만든다. 변환 행렬은 바뀔 때만 다시 계산한다."""

    def __init__(self, width: int = DEFAULT_VIEW_WIDTH):
        self.width = int(width)
        self._key = None  # 마지막으로 계산한 조건 (행렬 값, 매트 크기, 출력 폭)
        self._transform: np.ndarray | None = None
        self._size: tuple[int, int] | None = None
        self._mat_size: tuple[float, float] | None = None

    def _update(self, calibration) -> bool:
        """캘리브레이션·매트 크기·출력 크기가 바뀌었으면 변환 행렬을 다시 계산한다. 쓸 수 있으면 True."""
        if calibration.matrix is None:
            self._key = None
            self._transform = None
            return False
        mat_w = float(calibration.mat_width_mm)
        mat_h = float(calibration.mat_height_mm)
        key = (calibration.matrix.tobytes(), mat_w, mat_h, self.width)
        if key == self._key:
            return True
        height = max(1, int(round(self.width * mat_h / mat_w)))
        scale = np.array(
            [[self.width / mat_w, 0.0, 0.0], [0.0, height / mat_h, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64
        )
        self._transform = scale @ np.asarray(calibration.matrix, dtype=np.float64)
        self._size = (self.width, height)
        self._mat_size = (mat_w, mat_h)
        self._key = key
        return True

    def size(self, calibration) -> tuple[int, int] | None:
        """출력 화면 크기 (폭, 높이). 캘리브레이션 전이면 None."""
        return self._size if self._update(calibration) else None

    def transform(self, calibration) -> np.ndarray | None:
        """처리 프레임 픽셀 → 출력 화면 픽셀 변환 행렬 (3x3). 캘리브레이션 전이면 None."""
        return self._transform if self._update(calibration) else None

    def render(self, frame: np.ndarray, calibration) -> np.ndarray | None:
        """처리 프레임을 '전체 보기'로 펼친다. 캘리브레이션 전이면 None."""
        if not self._update(calibration):
            return None
        return cv2.warpPerspective(frame, self._transform, self._size, flags=cv2.INTER_LINEAR)

    def mm_to_view(self, mm_point) -> tuple[int, int]:
        """매트 mm 좌표 → 출력 화면 픽셀 (render/size를 먼저 불러 둬야 한다)."""
        width, height = self._size
        mat_w, mat_h = self._mat_size
        return (int(round(mm_point[0] / mat_w * width)), int(round(mm_point[1] / mat_h * height)))

    def mm_length_to_view(self, length_mm: float) -> int:
        """매트 위 길이(mm) → 출력 화면 길이(px). 가로·세로 비율이 같으므로 가로 기준으로 계산한다."""
        return max(1, int(round(length_mm / self._mat_size[0] * self._size[0])))
