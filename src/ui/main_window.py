"""
볼 트래커 - 2단계: 카메라 영상 연결 (메인 창)

- 왼쪽 위: 실시간 카메라 화면 (USB 카메라 영상을 그대로 표시)
- 왼쪽 아래: 궤적 결과 화면 자리 (다음 단계에서 연결)
- 오른쪽: 세로로 배치된 버튼 6개
- 위쪽: 현재 상태 배지 + 실제 측정 FPS 배지

카메라가 연결되어 있지 않으면 에러 없이 카메라 패널에
"카메라를 연결해주세요" 안내 문구를 보여준다.
버튼을 누르면 아직 실제 동작은 하지 않고,
상단 상태 라벨의 문구만 바뀝니다. (추적 관련 기능은 다음 단계에서 연결합니다)
"""

import time

import cv2

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.camera import Camera
from src.ui.styles import QSS

# 카메라를 몇 밀리초마다 확인할지 (약 30번/초)
CAMERA_POLL_INTERVAL_MS = 33
# 카메라가 없을 때, 다시 연결됐는지 확인하는 문구
CAMERA_NOT_CONNECTED_TEXT = "카메라를 연결해주세요"


class MainWindow(QMainWindow):
    """볼 트래커 메인 창 (2단계: 카메라 영상 연결까지 구현)"""

    def __init__(self):
        super().__init__()

        self.setWindowTitle("볼 트래커")
        self.resize(1100, 650)

        # 중앙 위젯 + 전체를 세로로 나누는 레이아웃
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        root_layout = QVBoxLayout(central_widget)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        # 1) 상단: 상태 배지 + FPS 배지 (가로로 나란히)
        top_layout = QHBoxLayout()
        top_layout.setSpacing(12)
        root_layout.addLayout(top_layout)

        self.status_label = QLabel("상태: 대기 중")
        self.status_label.setObjectName("status")
        top_layout.addWidget(self.status_label, 1)

        self.fps_label = QLabel("FPS: -")
        self.fps_label.setObjectName("status")
        top_layout.addWidget(self.fps_label)

        # 2) 본문: 왼쪽(카메라/궤적) + 오른쪽(버튼들)을 가로로 배치
        body_layout = QHBoxLayout()
        body_layout.setSpacing(12)
        root_layout.addLayout(body_layout)

        body_layout.addLayout(self._build_left_panel(), 3)
        body_layout.addLayout(self._build_right_panel(), 1)

        # 전체 창에 QSS 스타일 적용
        self.setStyleSheet(QSS)

        # 3) 카메라 연결 + 실시간 영상 표시 준비
        self.camera = Camera()
        self._last_frame_time: float | None = None
        self._fps: float = 0.0

        self._camera_timer = QTimer(self)
        self._camera_timer.setInterval(CAMERA_POLL_INTERVAL_MS)
        self._camera_timer.timeout.connect(self._update_camera_frame)
        self._camera_timer.start()

    def _build_left_panel(self) -> QVBoxLayout:
        """왼쪽 영역: 위(실시간 카메라) / 아래(궤적 결과) QLabel 두 개를 세로로 배치"""
        layout = QVBoxLayout()
        layout.setSpacing(12)

        self.camera_label = QLabel("실시간 카메라 화면\n(다음 단계에서 연결)")
        self.camera_label.setObjectName("panel")
        self.camera_label.setMinimumSize(400, 250)
        layout.addWidget(self.camera_label, 1)

        self.trajectory_label = QLabel("궤적 결과 화면\n(다음 단계에서 연결)")
        self.trajectory_label.setObjectName("panel")
        self.trajectory_label.setMinimumSize(400, 250)
        layout.addWidget(self.trajectory_label, 1)

        return layout

    def _build_right_panel(self) -> QVBoxLayout:
        """오른쪽 영역: 버튼 6개를 세로로 배치"""
        layout = QVBoxLayout()
        layout.setSpacing(10)

        # (버튼에 표시할 이름, 골드 강조 여부) 순서대로 정의
        button_specs = [
            ("캘리브레이션", True),
            ("시작 위치 등록", False),
            ("추적 준비", False),
            ("추적 중지", False),
            ("궤적 초기화", False),
            ("저장", False),
        ]

        for name, is_primary in button_specs:
            button = QPushButton(name)
            if is_primary:
                button.setObjectName("primary")
            # 클릭 시 상태 라벨 갱신 (실제 기능은 TODO: 다음 단계에서 구현)
            button.clicked.connect(
                lambda _checked=False, n=name: self._on_button_clicked(n)
            )
            layout.addWidget(button)

        # 버튼들을 위쪽으로 붙이고 아래는 빈 공간으로 남김
        layout.addStretch(1)
        return layout

    def _on_button_clicked(self, button_name: str) -> None:
        """버튼 클릭 시 호출되는 공통 슬롯.

        지금은 상태 라벨 문구만 갱신합니다.
        # TODO: 다음 단계에서 각 버튼의 실제 기능(카메라 열기, 추적 시작/중지 등)을 연결합니다.
        """
        self.status_label.setText(f"상태: {button_name} 클릭됨")

    def _update_camera_frame(self) -> None:
        """타이머가 주기적으로 호출하는 함수. 카메라에서 한 프레임을 읽어 화면에 그린다.

        카메라가 아직 없거나 연결이 끊긴 경우에는 예외를 던지지 않고
        패널에 안내 문구만 표시하고, 다음 호출 때 다시 연결을 시도한다.
        """
        if not self.camera.is_opened():
            if not self.camera.open():
                self._show_camera_not_connected()
                return

        frame = self.camera.read_frame()
        if frame is None:
            # 읽기에 실패했다면 연결이 끊긴 것으로 보고 장치를 반납한다.
            self.camera.release()
            self._show_camera_not_connected()
            return

        self._update_fps()
        pixmap = self._frame_to_pixmap(frame)
        self.camera_label.setPixmap(pixmap)

    def _show_camera_not_connected(self) -> None:
        """카메라 패널에 안내 문구를 표시하고 FPS 표시를 초기화한다."""
        self.camera_label.setText(CAMERA_NOT_CONNECTED_TEXT)
        self.fps_label.setText("FPS: -")
        self._last_frame_time = None
        self._fps = 0.0

    def _update_fps(self) -> None:
        """프레임 간 실제 시간 간격을 재서 FPS를 계산하고 라벨에 표시한다."""
        now = time.perf_counter()
        if self._last_frame_time is not None:
            elapsed = now - self._last_frame_time
            if elapsed > 0:
                instant_fps = 1.0 / elapsed
                # 값이 프레임마다 크게 튀지 않도록 지수 이동 평균으로 부드럽게 만든다.
                if self._fps == 0.0:
                    self._fps = instant_fps
                else:
                    self._fps = self._fps * 0.9 + instant_fps * 0.1
                self.fps_label.setText(f"FPS: {self._fps:.1f}")
        self._last_frame_time = now

    def _frame_to_pixmap(self, frame) -> QPixmap:
        """OpenCV 프레임(BGR numpy 배열)을 카메라 패널에 그릴 QPixmap으로 바꾼다."""
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        height, width, channels = rgb_frame.shape
        bytes_per_line = channels * width
        # numpy 배열 메모리가 QImage보다 먼저 해제되지 않도록 .copy()로 복사한다.
        image = QImage(
            rgb_frame.data, width, height, bytes_per_line, QImage.Format_RGB888
        ).copy()
        pixmap = QPixmap.fromImage(image)
        return pixmap.scaled(
            self.camera_label.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

    def closeEvent(self, event) -> None:
        """창을 닫을 때 타이머를 멈추고 카메라 장치를 반납한다."""
        self._camera_timer.stop()
        self.camera.release()
        super().closeEvent(event)
