"""
카메라 화면을 보여주는 QLabel.

일반 QLabel과 다른 점은 마우스 위치를 '라벨 안에서의 위치'가 아니라
'처리 프레임(전처리가 끝난 카메라 영상)의 픽셀 좌표'로 바꿔서 시그널로
보낸다는 것이다. 카메라 영상은 Qt.KeepAspectRatio로 비율을 유지한 채
축소/확대되고 가운데 정렬(레터박스: 남는 곳은 여백)되어 표시되므로,
화면에 보이는 위치를 그대로 쓰면 실제 프레임 좌표와 어긋난다.

22단계부터 처리 프레임은 1440x810(0.75배 축소 + 왜곡 보정)이고, 화면에는 이걸
다시 라벨 크기에 맞게 줄여서 그린다. 좌표 변환 계산은 label_to_frame() /
frame_to_label() 함수로 따로 빼 두어서, 화면 없이 테스트할 수 있다.

23단계: 캘리브레이션 모서리를 정확히 찍고 고칠 수 있도록
- 왼쪽 클릭: clicked(x, y)              -> 점 찍기 (또는 4점 완료 후 점 잡기)
- 왼쪽 버튼을 누른 채 움직임: dragged(x, y) -> 잡은 점 옮기기
- 왼쪽 버튼을 놓음: released(x, y)        -> 옮긴 점 확정
- 오른쪽 클릭: right_clicked()           -> 마지막 점 취소
- 마우스 이동(버튼 안 눌러도): hovered(x, y) -> 돋보기 위치 (영상 밖이면 left 시그널)
시그널을 나눴다. 무엇을 할지는 메인 창이 정한다.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel


def label_to_frame(point, label_size, pixmap_size, frame_size) -> tuple[int, int] | None:
    """라벨 위 좌표 -> 처리 프레임 좌표. 레터박스 여백(영상 밖)이면 None.

    point: (x, y) 라벨 안의 마우스 위치
    label_size: (폭, 높이) 라벨 전체 크기
    pixmap_size: (폭, 높이) 라벨 가운데에 실제로 그려진 영상 크기
    frame_size: (폭, 높이) 처리 프레임 크기 (예: 1440x810)
    """
    pixmap_width, pixmap_height = pixmap_size
    frame_width, frame_height = frame_size
    if pixmap_width <= 0 or pixmap_height <= 0:
        return None
    # 가운데 정렬이므로 (라벨 크기 - 영상 크기)의 절반이 왼쪽/위쪽 여백이다.
    offset_x = (label_size[0] - pixmap_width) / 2
    offset_y = (label_size[1] - pixmap_height) / 2
    x_in_pixmap = point[0] - offset_x
    y_in_pixmap = point[1] - offset_y
    if not (0 <= x_in_pixmap < pixmap_width and 0 <= y_in_pixmap < pixmap_height):
        return None  # 여백을 클릭함
    # 화면에 보이는 영상 1픽셀 = 프레임 몇 픽셀인지 곱해서 프레임 좌표로 바꾼다.
    frame_x = int(x_in_pixmap * frame_width / pixmap_width)
    frame_y = int(y_in_pixmap * frame_height / pixmap_height)
    # 반올림 오차로 프레임 끝을 넘지 않게 맞춘다
    return min(frame_x, frame_width - 1), min(frame_y, frame_height - 1)


def frame_to_label(point, label_size, pixmap_size, frame_size) -> tuple[float, float]:
    """처리 프레임 좌표 -> 라벨 위 좌표 (label_to_frame의 반대). 돋보기 위치 계산 등에 쓴다."""
    pixmap_width, pixmap_height = pixmap_size
    frame_width, frame_height = frame_size
    offset_x = (label_size[0] - pixmap_width) / 2
    offset_y = (label_size[1] - pixmap_height) / 2
    return (
        offset_x + point[0] * pixmap_width / frame_width,
        offset_y + point[1] * pixmap_height / frame_height,
    )


class CameraLabel(QLabel):
    """마우스 위치를 실제 처리 프레임 좌표로 알려주는 QLabel."""

    clicked = Signal(int, int)  # 왼쪽 버튼을 누른 위치 (프레임 좌표)
    dragged = Signal(int, int)  # 왼쪽 버튼을 누른 채 움직인 위치
    released = Signal(int, int)  # 왼쪽 버튼을 놓은 위치
    right_clicked = Signal()  # 오른쪽 클릭 (마지막 점 취소)
    hovered = Signal(int, int)  # 마우스가 영상 위에서 움직임 (돋보기용)
    left = Signal()  # 마우스가 영상 밖(여백·라벨 밖)으로 나감

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._frame_size: tuple[int, int] | None = None
        self._last_frame_point: tuple[int, int] | None = None
        # 버튼을 누르지 않아도 마우스 이동 이벤트를 받는다 (돋보기를 따라 움직이게)
        self.setMouseTracking(True)

    def set_frame_size(self, size: tuple[int, int] | None) -> None:
        """처리 프레임의 (너비, 높이)를 알려준다. 좌표 변환에 필요하다."""
        self._frame_size = size

    def display_scale(self) -> float | None:
        """화면에 그려진 영상 1픽셀이 프레임 몇 픽셀인지 (드래그로 점을 잡는 거리 계산용)."""
        pixmap = self.pixmap()
        if self._frame_size is None or pixmap is None or pixmap.isNull() or pixmap.width() == 0:
            return None
        return self._frame_size[0] / pixmap.width()

    def to_label_point(self, frame_point) -> tuple[float, float] | None:
        """프레임 좌표 -> 라벨 좌표 (영상이 없으면 None)."""
        pixmap = self.pixmap()
        if self._frame_size is None or pixmap is None or pixmap.isNull():
            return None
        return frame_to_label(
            frame_point, (self.width(), self.height()), (pixmap.width(), pixmap.height()), self._frame_size
        )

    # ------------------------------------------------------------------
    # 마우스 이벤트 -> 시그널
    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        if event.button() == Qt.RightButton:
            self.right_clicked.emit()
            return
        if event.button() != Qt.LeftButton:
            return
        frame_point = self._to_frame_point(event.position().toPoint())
        if frame_point is not None:
            self._last_frame_point = frame_point
            self.clicked.emit(*frame_point)

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)
        frame_point = self._to_frame_point(event.position().toPoint())
        if frame_point is None:
            self.left.emit()
            return
        self._last_frame_point = frame_point
        self.hovered.emit(*frame_point)
        if event.buttons() & Qt.LeftButton:
            self.dragged.emit(*frame_point)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if event.button() != Qt.LeftButton:
            return
        # 영상 밖에서 놓았으면 마지막으로 영상 안에 있던 위치로 본다
        frame_point = self._to_frame_point(event.position().toPoint()) or self._last_frame_point
        if frame_point is not None:
            self.released.emit(*frame_point)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.left.emit()

    def _to_frame_point(self, label_point) -> tuple[int, int] | None:
        """라벨 위 좌표를 처리 프레임 좌표로 바꾼다. 영상이 없거나 여백이면 None."""
        pixmap = self.pixmap()
        if self._frame_size is None or pixmap is None or pixmap.isNull():
            return None
        return label_to_frame(
            (label_point.x(), label_point.y()),
            (self.width(), self.height()),
            (pixmap.width(), pixmap.height()),
            self._frame_size,
        )
