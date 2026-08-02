"""
카메라 화면을 보여주는 QLabel.

일반 QLabel과 다른 점은 클릭했을 때 '라벨 안에서의 클릭 위치'가 아니라
'카메라 원본 프레임에서의 픽셀 좌표'로 변환해서 clicked 시그널을
보낸다는 것이다. 카메라 영상은 Qt.KeepAspectRatio로 비율을 유지한 채
축소/확대되고 가운데 정렬(레터박스)되어 표시되므로, 화면에 보이는
위치를 그대로 쓰면 실제 프레임 좌표와 어긋난다. 9단계(캘리브레이션)
에서 매트 모서리를 클릭으로 지정할 때 이 변환이 필요하다.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel


class CameraLabel(QLabel):
    """클릭 시 실제 카메라 프레임 좌표를 알려주는 QLabel."""

    clicked = Signal(int, int)  # 프레임(원본 영상) 좌표계 기준 (x, y)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._frame_size: tuple[int, int] | None = None

    def set_frame_size(self, size: tuple[int, int] | None) -> None:
        """실제 카메라 프레임의 (너비, 높이)를 알려준다. 좌표 변환에 필요하다."""
        self._frame_size = size

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        frame_point = self._to_frame_point(event.position().toPoint())
        if frame_point is not None:
            self.clicked.emit(*frame_point)

    def _to_frame_point(self, label_point) -> tuple[int, int] | None:
        """라벨 위 클릭 좌표를 카메라 프레임 좌표로 바꾼다.

        아직 영상이 없거나(플레이스홀더 문구만 있음), 레터박스 여백
        부분을 클릭했으면 None을 반환한다.
        """
        pixmap = self.pixmap()
        if self._frame_size is None or pixmap is None or pixmap.isNull():
            return None

        frame_width, frame_height = self._frame_size
        pixmap_width, pixmap_height = pixmap.width(), pixmap.height()
        if pixmap_width == 0 or pixmap_height == 0:
            return None

        # QSS의 qproperty-alignment: AlignCenter로 가운데 정렬되므로,
        # 라벨 크기와 (KeepAspectRatio로 스케일된) 실제 표시 크기 차이의
        # 절반이 좌우/상하 여백(레터박스)이다.
        offset_x = (self.width() - pixmap_width) / 2
        offset_y = (self.height() - pixmap_height) / 2

        x_in_pixmap = label_point.x() - offset_x
        y_in_pixmap = label_point.y() - offset_y
        if not (0 <= x_in_pixmap < pixmap_width and 0 <= y_in_pixmap < pixmap_height):
            return None  # 레터박스 여백을 클릭함

        scale_x = frame_width / pixmap_width
        scale_y = frame_height / pixmap_height
        return int(x_in_pixmap * scale_x), int(y_in_pixmap * scale_y)
