"""
볼 트래커 - 3단계: HSV 공 검출 연결 (메인 창)

- 왼쪽 위: 실시간 카메라 화면 (검출된 공에 원 + 중심점을 그려서 표시)
- 왼쪽 아래: 궤적 결과 화면 자리 (다음 단계에서 연결)
- 오른쪽: 세로로 배치된 버튼 6개
- 위쪽: 현재 상태 배지 + 검출 좌표 배지 + 실제 측정 FPS 배지
- 상단 메뉴 "설정 > 검출 설정...": HSV 튜닝 다이얼로그를 연다.
  다이얼로그가 열려 있는 동안에는 카메라 패널에 원본 대신
  마스크 미리보기(검출되는 영역이 흰색으로 보이는 화면)를 보여준다.

카메라가 연결되어 있지 않으면 에러 없이 카메라 패널에
"카메라를 연결해주세요" 안내 문구를 보여준다.
공이 검출되지 않으면(조명, 공이 화면 밖으로 나감 등) 조용히 넘어가고
좌표 배지만 "-"로 표시한다.
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
from src.config import load_settings, save_settings
from src.detector import (
    DEFAULT_DETECTION_SETTINGS,
    BallDetection,
    BallDetector,
    build_mask,
)
from src.ui.styles import QSS
from src.ui.tuning_dialog import TuningDialog

# 검출된 공 표시 색상 (OpenCV는 BGR 순서)
BALL_CIRCLE_COLOR_BGR = (23, 160, 212)  # 골드 (#D4A017)
BALL_CENTER_COLOR_BGR = (0, 0, 255)  # 중심점은 빨간색으로 눈에 띄게
BALL_CIRCLE_THICKNESS = 2
BALL_CENTER_RADIUS = 4

# 카메라를 몇 밀리초마다 확인할지.
# 카메라 자체 FPS(설정상 최대 90)보다 충분히 짧게 잡아야, 우리가 폴링하는
# 주기가 병목이 되지 않고 실제 카메라 FPS가 그대로 측정/표시된다.
CAMERA_POLL_INTERVAL_MS = 8
# 카메라가 없을 때, 다시 연결됐는지 확인하는 문구
CAMERA_NOT_CONNECTED_TEXT = "카메라를 연결해주세요"


class MainWindow(QMainWindow):
    """볼 트래커 메인 창 (3단계: HSV 공 검출까지 구현)"""

    def __init__(self):
        super().__init__()

        self.setWindowTitle("볼 트래커")
        self.resize(1100, 650)

        self._build_menu()

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

        self.coord_label = QLabel("좌표: -")
        self.coord_label.setObjectName("status")
        top_layout.addWidget(self.coord_label)

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
        self.detector = BallDetector()
        self._last_frame_time: float | None = None
        self._fps: float = 0.0

        # HSV 튜닝 다이얼로그 (검출 설정 메뉴에서 연다). 열려 있는 동안은
        # 마스크 미리보기를 카메라 패널에 대신 보여준다.
        self.tuning_dialog: TuningDialog | None = None
        self._mask_preview_active = False
        self._preview_hsv: tuple[list, list] | None = None

        self._camera_timer = QTimer(self)
        self._camera_timer.setInterval(CAMERA_POLL_INTERVAL_MS)
        self._camera_timer.timeout.connect(self._update_camera_frame)
        self._camera_timer.start()

    def _build_menu(self) -> None:
        """상단 메뉴 바에 '설정 > 검출 설정...' 항목을 만든다."""
        settings_menu = self.menuBar().addMenu("설정")
        tuning_action = settings_menu.addAction("검출 설정...")
        tuning_action.triggered.connect(self._open_tuning_dialog)

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

    def _open_tuning_dialog(self) -> None:
        """'설정 > 검출 설정...' 메뉴를 눌렀을 때 HSV 튜닝 창을 연다."""
        detection_settings = load_settings().get("detection", {})
        profiles = {
            **DEFAULT_DETECTION_SETTINGS["profiles"],
            **detection_settings.get("profiles", {}),
        }
        active_profile = detection_settings.get(
            "active_profile", DEFAULT_DETECTION_SETTINGS["active_profile"]
        )

        if self.tuning_dialog is None:
            self.tuning_dialog = TuningDialog(profiles, active_profile, self)
            self.tuning_dialog.values_changed.connect(self._on_tuning_values_changed)
            self.tuning_dialog.saved.connect(self._on_tuning_profile_saved)
            self.tuning_dialog.finished.connect(self._on_tuning_dialog_closed)
        else:
            self.tuning_dialog.refresh_profiles(profiles, active_profile)

        self._mask_preview_active = True
        self.tuning_dialog.show()
        self.tuning_dialog.raise_()
        self.tuning_dialog.activateWindow()

    def _on_tuning_values_changed(self, lower: list, upper: list) -> None:
        """튜닝 창 슬라이더가 움직일 때마다 마스크 미리보기에 쓸 범위를 갱신한다."""
        self._preview_hsv = (lower, upper)

    def _on_tuning_dialog_closed(self) -> None:
        """튜닝 창이 닫히면 마스크 미리보기를 끄고 원래 카메라 화면으로 돌아간다."""
        self._mask_preview_active = False
        self._preview_hsv = None

    def _on_tuning_profile_saved(self, key: str, profile: dict) -> None:
        """'저장'을 누르면 config/settings.json에 반영하고 검출기를 새로 만든다."""
        settings = load_settings()
        detection_settings = settings.setdefault("detection", {})
        profiles = detection_settings.setdefault("profiles", {})
        profiles[key] = profile
        detection_settings["active_profile"] = key
        save_settings(settings)

        # 저장한 설정을 바로 검출에 쓰도록 새로 불러온다.
        self.detector = BallDetector()
        self.status_label.setText(f"상태: '{profile.get('name', key)}' 프로필 저장됨")

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

        if self._mask_preview_active and self._preview_hsv is not None:
            # 튜닝 중에는 원본 대신 마스크(검출 영역이 흰색)를 보여준다.
            lower, upper = self._preview_hsv
            mask = build_mask(frame, lower, upper)
            self.coord_label.setText("좌표: -")
            self._update_fps()
            self.camera_label.setPixmap(self._mask_to_pixmap(mask))
            return

        # 공 검출 (못 찾아도 예외 없이 None만 돌아온다)
        detection = self.detector.detect(frame)
        self._update_coord_label(detection)
        if detection is not None:
            self._draw_detection(frame, detection)

        self._update_fps()
        pixmap = self._frame_to_pixmap(frame)
        self.camera_label.setPixmap(pixmap)

    def _update_coord_label(self, detection: BallDetection | None) -> None:
        """검출된 공의 중심 좌표를 상태 라벨 옆 배지에 표시한다."""
        if detection is None:
            self.coord_label.setText("좌표: -")
        else:
            self.coord_label.setText(f"좌표: ({detection.x}, {detection.y})")

    @staticmethod
    def _draw_detection(frame, detection: BallDetection) -> None:
        """검출된 공 위치에 원과 중심점을 그린다. (frame을 그 자리에서 수정)"""
        center = (detection.x, detection.y)
        cv2.circle(frame, center, detection.radius, BALL_CIRCLE_COLOR_BGR, BALL_CIRCLE_THICKNESS)
        cv2.circle(frame, center, BALL_CENTER_RADIUS, BALL_CENTER_COLOR_BGR, -1)

    def _show_camera_not_connected(self) -> None:
        """카메라 패널에 안내 문구를 표시하고 FPS·좌표 표시를 초기화한다."""
        self.camera_label.setText(CAMERA_NOT_CONNECTED_TEXT)
        self.fps_label.setText("FPS: -")
        self.coord_label.setText("좌표: -")
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

    def _mask_to_pixmap(self, mask) -> QPixmap:
        """흑백 마스크(numpy 2차원 배열)를 카메라 패널에 그릴 QPixmap으로 바꾼다."""
        height, width = mask.shape
        image = QImage(
            mask.data, width, height, width, QImage.Format_Grayscale8
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
        if self.tuning_dialog is not None:
            self.tuning_dialog.close()
        super().closeEvent(event)
