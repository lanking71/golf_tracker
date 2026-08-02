"""
볼 트래커 - 12단계: 결과 저장·조회 (메인 창)

- 왼쪽 위: 실시간 카메라 화면
  - CALIBRATING 상태에서는 화면을 클릭해 매트 네 모서리를
    좌상단->우상단->우하단->좌하단 순서로 지정한다. 클릭한 점은
    골드 번호(1~4)로 표시된다. 4점을 다 찍으면 원근 변환 행렬을
    만들어 config/settings.json에 저장한다 (src.calibration 담당).
    '캘리브레이션' 버튼을 다시 누르면 지금까지 찍은 점을 지우고
    '다시 지정'할 수 있다.
  - 캘리브레이션이 됐으면 매트 영역을 얇은 골드 선으로 계속 표시한다.
  - 지금 검출된 공: 흰 원 + 빨간 중심점 (단, 캘리브레이션됐고
    필터가 켜져 있으면 매트 영역 밖 검출은 무시한다 -
    config/settings.json의 calibration.filter_outside_mat)
  - 등록된 시작점(있으면): 골드 십자가 + 링, 계속 표시됨
- 왼쪽 아래: 궤적 결과 화면. TRACKING 중 기록된 궤적을 실시간으로
  그린다 - 시작점은 골드 점, 정지 위치는 골드 링, 이동 경로는
  라임(#95D5B2) 선, 현재 위치는 흰 점. 캘리브레이션이 됐으면 캔버스를
  매트 실제 비율(가로:세로)로 그리고 좌표도 mm로 변환해서 찍는다 -
  안 됐으면 기존처럼 카메라 프레임 비율 그대로 픽셀 좌표로 그린다.
  그 오른쪽에는 별도 Qt 패널로 요약 통계(총 거리·소요 시간·평균/최고
  속도·직진성)를 보여준다 (계산은 src.stats.compute_trajectory_stats
  담당). 캘리브레이션이 됐으면 cm·cm/초 단위로, 안 됐으면 "캘리브레이션
  후 표시됩니다" 안내로 대신한다 (px 단위는 더 이상 보여주지 않는다).
  캔버스에 직접 그리지 않고 별도 QLabel로 분리해서, 카메라 해상도나
  창 크기와 무관하게 글씨가 항상 선명하게 보인다.
- 오른쪽: 세로로 배치된 버튼 6개. 각 버튼은 src.tracker.Tracker의 상태
  전환을 호출하고, 지금 상태에서 허용되지 않는 버튼은 자동으로
  비활성화된다.
- 위쪽: 상태 배지(Tracker.state, FINISHED면 정지/이탈 사유도 표시) +
  검출 좌표 배지 + 시작점 배지 + 정지점 배지 + FPS 배지
- 상단 메뉴 "설정 > 검출 설정...": HSV 튜닝 다이얼로그를 연다.
  다이얼로그가 열려 있는 동안에는 카메라 패널에 원본 대신
  마스크 미리보기(검출되는 영역이 흰색으로 보이는 화면)를 보여준다.
- 상단 메뉴 "기록 > 기록 보기...": 저장된 측정 기록을 날짜순으로 보고
  하나를 클릭하면 그 궤적과 통계를 다시 볼 수 있는 창을 연다
  (src.ui.records_dialog.RecordsDialog 담당). 기록 삭제도 거기서 한다.
- '저장' 버튼(FINISHED 상태에서만 활성화): 캘리브레이션이 안 됐거나
  궤적이 너무 짧아 통계를 계산할 수 없으면 저장하지 않고 상태 배지에
  안내만 잠깐 보여준다. 저장에 성공하면 시작점/정지점(mm)·요약 통계
  (cm·cm/초)·전체 궤적(mm)·사용한 검출 프로필 이름을 SQLite DB
  (src.storage.Storage, config/settings.json의 storage.db_path)에
  기록하고, 상태 배지에 "저장 완료"를 잠깐 보여준다.

'시작 위치 등록' 버튼을 누른 순간 검출된 공이 없으면 등록하지 않고,
상태 배지에 "공이 감지되지 않습니다"를 잠깐 보여준 뒤 원래 상태
문구로 되돌린다 (상태 전환 자체는 일어나지 않는다).
TRACKING 중 공이 잠깐 검출되지 않는 프레임은 궤적 기록을 건너뛰고,
다시 검출되면 이어서 기록한다 (프로그램이 멈추지 않는다).
직전 점과 너무 멀리 떨어진 검출(오검출로 추정)은 기록하지 않고,
연속으로 max_consecutive_jumps번 이상 그런 값이 나오면 공을 실제로
옮긴 것으로 보고 받아들인다 (Tracker.add_trajectory_point 참고).
궤적을 그릴 때도 점 사이 거리가 너무 크면 선으로 잇지 않아서 매트를
가로지르는 가짜 직선이 생기지 않는다.

TRACKING 중 매 프레임 Tracker.update_detection()을 호출해 정지·이탈을
자동으로 판정한다 - 공이 거의 안 움직인 채 stop_duration초 이상
지나면 "정지 감지"로, 공이 lost_duration초 이상 검출되지 않으면
"화면 이탈"로 보고 자동으로 FINISHED로 전환된다.
'궤적 초기화'를 누르면 시작점·정지점·궤적이 카메라/궤적 패널 모두에서
함께 지워지고, 요약 통계 카드도(궤적 데이터가 없어져 계산이 안 되므로)
자연히 사라진다.

카메라가 연결되어 있지 않으면 에러 없이 카메라 패널에
"카메라를 연결해주세요" 안내 문구를 보여준다.
공이 검출되지 않으면(조명, 공이 화면 밖으로 나감 등) 조용히 넘어가고
좌표 배지만 "-"로 표시한다.
"""

import time

import cv2
import numpy as np

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

from src.calibration import CORNER_NAMES, Calibration
from src.camera import Camera
from src.config import load_settings, save_settings
from src.detector import (
    DEFAULT_DETECTION_SETTINGS,
    BallDetection,
    BallDetector,
    build_mask,
)
from src.stats import compute_trajectory_stats
from src.storage import Storage
from src.tracker import Tracker, TrackerState, TrajectoryPoint
from src.ui.camera_label import CameraLabel
from src.ui.records_dialog import RecordsDialog
from src.ui.settings_dialog import SettingsDialog
from src.ui.styles import QSS
from src.ui.tuning_dialog import TuningDialog

# 버튼 이름 -> Tracker에 정의된 동작 이름.
# "저장"은 상태를 바꾸지 않는 동작이라 Tracker에 전환 메서드가 없고,
# Tracker.ALLOWED_STATES["save"]로 활성화 여부만 확인한다.
BUTTON_ACTIONS = {
    "캘리브레이션": "calibrate",
    "시작 위치 등록": "register_start_position",
    "추적 준비": "start_tracking",
    "추적 중지": "stop_tracking",
    "궤적 초기화": "reset_trajectory",
    "저장": "save",
}

# 검출된 공 표시 색상 (OpenCV는 BGR 순서). 골드는 시작점 전용 색이라
# 매 프레임 바뀌는 검출 표시는 흰색으로 구분한다.
BALL_CIRCLE_COLOR_BGR = (255, 255, 255)  # 흰색
BALL_CENTER_COLOR_BGR = (0, 0, 255)  # 중심점은 빨간색으로 눈에 띄게
BALL_CIRCLE_THICKNESS = 2
BALL_CENTER_RADIUS = 4

# 등록된 시작점 표시 색상/크기 (골드 십자가 + 링)
START_POINT_COLOR_BGR = (23, 160, 212)  # 골드 (#D4A017)
START_POINT_MARKER_SIZE = 16
START_POINT_RING_RADIUS = 10
START_POINT_THICKNESS = 2

# 캘리브레이션 중 클릭한 매트 모서리 표시 (골드 점 + 번호)
CALIBRATION_POINT_RADIUS = 6
CALIBRATION_FONT_SCALE = 0.8
CALIBRATION_FONT_THICKNESS = 2

# 캘리브레이션된 매트 영역을 카메라 화면에 표시하는 얇은 골드 선
MAT_OUTLINE_COLOR_BGR = START_POINT_COLOR_BGR
MAT_OUTLINE_THICKNESS = 1

# 캘리브레이션됐을 때 궤적 패널을 매트 실제 비율(가로:세로)로 그리는
# 기준 해상도. 라벨 크기에 맞춰 다시 스케일되므로 절대값은 중요하지
# 않고 비율만 정확하면 된다.
TRAJECTORY_REAL_CANVAS_WIDTH = 1000

# 궤적 결과 패널 색상 (OpenCV는 BGR 순서)
TRAJECTORY_BG_COLOR_BGR = (79, 106, 45)  # 패널 배경 (#2D6A4F)과 동일
TRAJECTORY_START_RADIUS = 10  # 궤적 패널의 시작점(골드) 반지름
TRAJECTORY_PATH_COLOR_BGR = (178, 213, 149)  # 라임 (#95D5B2)
TRAJECTORY_PATH_THICKNESS = 2
TRAJECTORY_CURRENT_COLOR_BGR = (255, 255, 255)  # 현재 위치는 흰 점
TRAJECTORY_CURRENT_RADIUS = 6
TRAJECTORY_STOP_RADIUS = 14  # 정지 위치는 골드 "링"(테두리만)으로 표시
TRAJECTORY_STOP_THICKNESS = 3

# 요약 통계 패널 (궤적 캔버스 오른쪽의 별도 Qt 라벨). 캔버스에 직접 그리지
# 않고 Qt 텍스트로 표시해서, 카메라 해상도/창 크기와 무관하게 항상 선명하다.
STATS_PLACEHOLDER_TEXT = "<b>요약 통계</b><br><br>추적이 끝나면<br>여기에 표시됩니다"
# 캘리브레이션이 안 된 상태로 FINISHED가 되면, 픽셀 통계 대신 이 안내를 보여준다
# (요구사항: px 단위는 더 이상 화면에 보여주지 않는다).
STATS_NEEDS_CALIBRATION_TEXT = "<b>요약 통계</b><br><br>캘리브레이션 후<br>실제 단위(cm)로<br>표시됩니다"

# 시작 위치 등록 시 공이 검출되지 않았을 때 잠깐 보여줄 안내 문구
NOT_DETECTED_MESSAGE = "공이 감지되지 않습니다"
# 공은 보이지만 매트 영역 밖이라 무시됐을 때 보여줄 안내 문구
OUTSIDE_MAT_MESSAGE = "공이 매트 밖에 있습니다"
NOT_DETECTED_MESSAGE_DURATION_MS = 1500

# 매트 영역 밖에서 검출됐지만 무시된 공 표시 색 (경고색)
OUTSIDE_MAT_DETECTION_COLOR_BGR = (0, 100, 255)  # 주황빛 빨강

# '저장' 버튼 관련 안내 문구
SAVE_NEEDS_CALIBRATION_MESSAGE = "캘리브레이션이 필요합니다"
SAVE_NO_TRAJECTORY_MESSAGE = "저장할 궤적이 없습니다"
SAVE_SUCCESS_MESSAGE = "저장 완료"

# 카메라를 몇 밀리초마다 확인할지.
# 카메라 자체 FPS(설정상 최대 90)보다 충분히 짧게 잡아야, 우리가 폴링하는
# 주기가 병목이 되지 않고 실제 카메라 FPS가 그대로 측정/표시된다.
CAMERA_POLL_INTERVAL_MS = 8
# 카메라가 없을 때, 다시 연결됐는지 확인하는 문구
CAMERA_NOT_CONNECTED_TEXT = "카메라를 연결해주세요"


class MainWindow(QMainWindow):
    """볼 트래커 메인 창 (12단계: 결과 저장·조회까지 구현)"""

    def __init__(self):
        super().__init__()

        self.setWindowTitle("볼 트래커")
        self.resize(1100, 650)

        # 버튼 활성화 여부를 계산하려면 레이아웃을 만들기 전에 필요하다.
        self.tracker = Tracker()
        self.calibration = Calibration()
        self._buttons: dict[str, QPushButton] = {}

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

        self.start_point_label = QLabel("시작점: -")
        self.start_point_label.setObjectName("status")
        top_layout.addWidget(self.start_point_label)

        self.stop_point_label = QLabel("정지점: -")
        self.stop_point_label.setObjectName("status")
        top_layout.addWidget(self.stop_point_label)

        self.fps_label = QLabel("FPS: -")
        self.fps_label.setObjectName("status")
        top_layout.addWidget(self.fps_label)

        # 2) 본문: 왼쪽(카메라/궤적) + 오른쪽(버튼들)을 가로로 배치
        body_layout = QHBoxLayout()
        body_layout.setSpacing(12)
        root_layout.addLayout(body_layout)

        body_layout.addLayout(self._build_left_panel(), 3)
        body_layout.addLayout(self._build_right_panel(), 1)

        self.camera_label.clicked.connect(self._on_camera_clicked)

        # 전체 창에 QSS 스타일 적용
        self.setStyleSheet(QSS)

        # 3) 카메라 연결 + 실시간 영상 표시 준비
        self.camera = Camera()
        self.detector = BallDetector()
        self._last_frame_time: float | None = None
        self._fps: float = 0.0
        # 가장 최근 프레임에서 검출된 공 (없으면 None). '시작 위치 등록'
        # 버튼을 눌렀을 때 이 값을 시작점으로 쓴다.
        self._last_detection: BallDetection | None = None
        # 방금 프레임에서 공을 찾긴 했지만 매트 영역 밖이라 무시했는지.
        # '공을 아예 못 찾음'과 구분해서 안내 문구를 다르게 보여주는 데 쓴다.
        self._last_detection_outside_mat = False
        # 실제로 카메라에서 읽어온 프레임의 (너비, 높이). 궤적 결과 패널을
        # 카메라와 같은 좌표계로 그리기 위해 첫 프레임을 받은 뒤 채워진다.
        self._frame_size: tuple[int, int] | None = None

        # HSV 튜닝 다이얼로그 (검출 설정 메뉴에서 연다). 열려 있는 동안은
        # 마스크 미리보기를 카메라 패널에 대신 보여준다.
        self.tuning_dialog: TuningDialog | None = None
        self._mask_preview_active = False
        self._preview_hsv: tuple[list, list] | None = None

        # 프로필/매트 설정 다이얼로그 (설정 메뉴에서 연다)
        self.settings_dialog: SettingsDialog | None = None

        # 측정 결과 저장소 (12단계) + 기록 보기 다이얼로그 (기록 메뉴에서 연다)
        self.storage = Storage()
        self.records_dialog: RecordsDialog | None = None

        self._camera_timer = QTimer(self)
        self._camera_timer.setInterval(CAMERA_POLL_INTERVAL_MS)
        self._camera_timer.timeout.connect(self._update_camera_frame)
        self._camera_timer.start()

        # 시작 상태(IDLE)에 맞게 배지 문구와 버튼 활성화 상태를 맞춘다.
        self._update_status_badge()
        self._update_button_states()
        self._update_start_point_label()
        self._update_stop_point_label()
        self._render_trajectory_panel()

    def _build_menu(self) -> None:
        """상단 메뉴 바에 '설정' 메뉴를 만든다.

        - 검출 설정...: HSV 슬라이더 + 마스크 미리보기로 실시간 튜닝
        - 프로필/매트 설정...: 프로필 목록 관리(선택·추가·삭제·이름변경) +
          매트 크기·매트 밖 필터 설정
        """
        settings_menu = self.menuBar().addMenu("설정")

        tuning_action = settings_menu.addAction("검출 설정...")
        tuning_action.triggered.connect(self._open_tuning_dialog)

        settings_dialog_action = settings_menu.addAction("프로필/매트 설정...")
        settings_dialog_action.triggered.connect(self._open_settings_dialog)

        records_menu = self.menuBar().addMenu("기록")
        records_action = records_menu.addAction("기록 보기...")
        records_action.triggered.connect(self._open_records_dialog)

    def _build_left_panel(self) -> QVBoxLayout:
        """왼쪽 영역: 위(실시간 카메라) / 아래(궤적 결과 + 요약 통계)를 세로로 배치"""
        layout = QVBoxLayout()
        layout.setSpacing(12)

        self.camera_label = CameraLabel("실시간 카메라 화면\n(다음 단계에서 연결)")
        self.camera_label.setObjectName("panel")
        self.camera_label.setMinimumSize(400, 250)
        layout.addWidget(self.camera_label, 1)

        trajectory_row = QHBoxLayout()
        trajectory_row.setSpacing(12)
        layout.addLayout(trajectory_row, 1)

        self.trajectory_label = QLabel("궤적 결과 화면\n(다음 단계에서 연결)")
        self.trajectory_label.setObjectName("panel")
        self.trajectory_label.setMinimumSize(300, 250)
        trajectory_row.addWidget(self.trajectory_label, 3)

        # 요약 통계는 캔버스에 직접 그리지 않고 별도 Qt 라벨로 둬서,
        # 카메라 해상도나 창 크기와 무관하게 글씨가 항상 선명하게 보이게 한다.
        self.stats_label = QLabel(STATS_PLACEHOLDER_TEXT)
        self.stats_label.setObjectName("stats_card")
        self.stats_label.setMinimumWidth(180)
        self.stats_label.setMaximumWidth(260)
        self.stats_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.stats_label.setWordWrap(True)
        trajectory_row.addWidget(self.stats_label, 1)

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
            # 클릭 시 Tracker의 상태 전환을 시도한다 (BUTTON_ACTIONS 매핑 참고)
            button.clicked.connect(
                lambda _checked=False, n=name: self._on_button_clicked(n)
            )
            layout.addWidget(button)
            self._buttons[name] = button

        # 버튼들을 위쪽으로 붙이고 아래는 빈 공간으로 남김
        layout.addStretch(1)
        return layout

    def _on_button_clicked(self, button_name: str) -> None:
        """버튼 클릭 시 호출되는 공통 슬롯. 버튼에 맞는 상태 전환을 시도한다."""
        if button_name == "시작 위치 등록":
            self._register_start_position()
        elif button_name == "저장":
            # 저장은 상태를 바꾸지 않는 동작이라 _update_status_badge()를
            # 뒤에서 또 부르면 안 된다 (그러면 "저장 완료" 임시 문구가
            # 바로 원래 문구로 덮어써진다 - '시작 위치 등록'에서 이미
            # 겪은 버그와 같은 패턴이라 여기도 독립된 분기로 뺐다).
            self._save_result()
        else:
            action = BUTTON_ACTIONS.get(button_name)
            if action == "calibrate":
                # 캘리브레이션 버튼은 다시 눌러도(이미 CALIBRATING이어도) 항상
                # 지금까지 찍은 모서리를 지우고 새로 찍기 시작한다. 이렇게 하면
                # 잘못 찍었을 때 같은 버튼으로 '다시 지정'할 수 있다.
                self.calibration.reset_corners()
                self.tracker.calibrate()
            elif action is not None:
                getattr(self.tracker, action)()
            self._update_status_badge()

        self._update_button_states()
        self._update_start_point_label()
        self._update_stop_point_label()
        self._render_trajectory_panel()

    def _on_camera_clicked(self, x: int, y: int) -> None:
        """카메라 화면 클릭. CALIBRATING 상태일 때만 매트 모서리로 등록한다.

        4번째 점을 찍으면 원근 변환 행렬이 자동으로 만들어지고
        config/settings.json에 저장된다 (재실행 시 자동으로 다시 불러온다).
        """
        if self.tracker.state != TrackerState.CALIBRATING:
            return
        if self.calibration.add_corner(x, y):
            if self.calibration.is_calibrated():
                self.calibration.save()
            self._update_status_badge()

    def _register_start_position(self) -> None:
        """검출된 공의 현재 좌표를 시작점으로 등록한다.

        등록 순간 공이 검출되지 않았다면 등록하지 않고, 상태 배지에
        안내 문구만 잠깐 보여준 뒤 원래 상태 문구로 되돌린다
        (상태 전환은 일어나지 않는다). 공은 보이지만 매트 영역 밖이라
        무시된 경우에는 그 사실을 알 수 있는 문구를 따로 보여준다.
        """
        if self._last_detection is None:
            message = OUTSIDE_MAT_MESSAGE if self._last_detection_outside_mat else NOT_DETECTED_MESSAGE
            self._show_temporary_status(message)
            return

        if self.tracker.register_start_position(self._last_detection.x, self._last_detection.y):
            self._update_status_badge()

    def _save_result(self) -> None:
        """'저장' 버튼: 이번 측정 결과를 SQLite DB에 기록한다.

        캘리브레이션이 안 됐으면(실제 단위 mm를 모르면) 저장하지 않고
        안내만 잠깐 보여준다. 궤적이 너무 짧아(점 2개 미만) 통계를 계산할
        수 없을 때도 마찬가지다. 두 조건을 다 통과하면 시작점/정지점(mm),
        요약 통계(cm·cm/초), 전체 궤적(mm), 현재 활성 검출 프로필 이름을
        저장하고 "저장 완료"를 잠깐 보여준다.
        """
        if not self.calibration.is_calibrated():
            self._show_temporary_status(SAVE_NEEDS_CALIBRATION_MESSAGE)
            return

        real_trajectory = self._trajectory_to_real()
        real_start = (
            self.calibration.pixel_to_real(*self.tracker.start_position)
            if self.tracker.start_position is not None
            else None
        )
        real_stop = (
            self.calibration.pixel_to_real(*self.tracker.stop_position)
            if self.tracker.stop_position is not None
            else None
        )

        stats = compute_trajectory_stats(real_trajectory, real_start, real_stop)
        if stats is None:
            self._show_temporary_status(SAVE_NO_TRAJECTORY_MESSAGE)
            return

        self.storage.save_result(
            profile_name=self._active_profile_name(),
            start_point_mm=real_start,
            stop_point_mm=real_stop,
            total_distance_cm=stats.total_distance / 10,
            duration_s=stats.duration,
            average_speed_cm=stats.average_speed / 10,
            max_speed_cm=stats.max_speed / 10,
            straightness=stats.straightness,
            mat_width_mm=self.calibration.mat_width_mm,
            mat_height_mm=self.calibration.mat_height_mm,
            trajectory_mm=[(p.x, p.y) for p in real_trajectory],
        )
        self._show_temporary_status(SAVE_SUCCESS_MESSAGE)

    def _active_profile_name(self) -> str:
        """지금 활성화된 검출 프로필의 표시 이름을 돌려준다."""
        detection_settings = load_settings().get("detection", {})
        profiles = {
            **DEFAULT_DETECTION_SETTINGS["profiles"],
            **detection_settings.get("profiles", {}),
        }
        active_key = detection_settings.get(
            "active_profile", DEFAULT_DETECTION_SETTINGS["active_profile"]
        )
        profile = profiles.get(active_key, {})
        return profile.get("name", active_key)

    def _show_temporary_status(self, message: str) -> None:
        """상태 배지에 message를 잠깐 보여준 뒤 원래 상태 문구로 되돌린다."""
        self.status_label.setText(f"상태: {message}")
        QTimer.singleShot(NOT_DETECTED_MESSAGE_DURATION_MS, self._update_status_badge)

    def _update_status_badge(self) -> None:
        """상단 상태 배지를 현재 Tracker 상태 문구로 갱신한다.

        FINISHED 상태이고 종료 사유(Tracker.finish_reason)가 있으면
        "상태: 추적 완료 (정지 감지)"처럼 사유도 함께 보여준다.
        CALIBRATING 상태면 몇 번째 모서리를 찍어야 하는지 안내한다.
        """
        text = f"상태: {self.tracker.state.value}"
        if self.tracker.state == TrackerState.FINISHED and self.tracker.finish_reason:
            text += f" ({self.tracker.finish_reason})"
        elif self.tracker.state == TrackerState.CALIBRATING:
            count = len(self.calibration.corners)
            if count < 4:
                text += f" ({count}/4 - {CORNER_NAMES[count]} 클릭)"
            else:
                text += " (4/4 완료)"
        self.status_label.setText(text)

    def _update_button_states(self) -> None:
        """현재 상태에서 허용되지 않는 버튼은 비활성화한다."""
        for name, button in self._buttons.items():
            action = BUTTON_ACTIONS[name]
            button.setEnabled(self.tracker.can(action))

    def _update_start_point_label(self) -> None:
        """등록된 시작점 좌표를 배지에 표시한다. 없으면 '-'."""
        self.start_point_label.setText(f"시작점: {self._format_point(self.tracker.start_position)}")

    def _update_stop_point_label(self) -> None:
        """정지 판정으로 멈춘 좌표를 배지에 표시한다. 없으면 '-'."""
        self.stop_point_label.setText(f"정지점: {self._format_point(self.tracker.stop_position)}")

    def _format_point(self, pixel_point: tuple | None) -> str:
        """좌표 배지에 표시할 문자열을 만든다.

        캘리브레이션이 됐으면 매트 실제 좌표(mm)로 바꿔서 보여주고,
        아직 안 됐으면 픽셀 좌표를 px 단위로 보여준다 (캘리브레이션
        전에도 앱을 계속 쓸 수 있도록 하는 대체 표시).
        """
        if pixel_point is None:
            return "-"
        if self.calibration.is_calibrated():
            real = self.calibration.pixel_to_real(*pixel_point)
            if real is not None:
                return f"({real[0]:.0f}, {real[1]:.0f})mm"
        x, y = pixel_point
        return f"({x}, {y})px"

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
        # (상단 상태 배지는 Tracker 상태 전용이라 여기서는 건드리지 않는다)
        self.detector = BallDetector()

    def _open_settings_dialog(self) -> None:
        """'설정 > 프로필/매트 설정...' 메뉴를 눌렀을 때 설정 창을 연다."""
        if self.settings_dialog is None:
            self.settings_dialog = SettingsDialog(self)
            self.settings_dialog.settings_changed.connect(self._on_settings_changed)
        else:
            # 그 사이 다른 곳(HSV 튜닝 등)에서 바뀐 내용도 반영되도록 다시 불러온다.
            self.settings_dialog.reload_from_settings()

        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def _on_settings_changed(self) -> None:
        """프로필/매트 설정 창에서 뭔가 저장되면 검출기·캘리브레이션을 새로 불러온다."""
        self.detector = BallDetector()
        self.calibration = Calibration()
        # 매트 크기가 바뀌었을 수 있으니(비율이 달라짐) 궤적 패널을 다시 그린다.
        self._render_trajectory_panel()

    def _open_records_dialog(self) -> None:
        """'기록 > 기록 보기...' 메뉴를 눌렀을 때 저장된 측정 기록 창을 연다."""
        if self.records_dialog is None:
            self.records_dialog = RecordsDialog(self.storage, self)
        else:
            # 그 사이 새로 저장되거나 삭제된 기록이 반영되도록 다시 불러온다.
            self.records_dialog.refresh()

        self.records_dialog.show()
        self.records_dialog.raise_()
        self.records_dialog.activateWindow()

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

        # 궤적 결과 패널을 카메라와 같은 좌표계로 그리기 위해 실제 프레임 크기를 기억해둔다.
        # 크기가 처음 정해지거나 바뀔 때만 패널을 다시 그린다 (매 프레임 다시 그리면 낭비).
        new_frame_size = (frame.shape[1], frame.shape[0])
        if self._frame_size != new_frame_size:
            self._frame_size = new_frame_size
            self.camera_label.set_frame_size(self._frame_size)
            self._render_trajectory_panel()

        if self._mask_preview_active and self._preview_hsv is not None:
            # 튜닝 중에는 원본 대신 마스크(검출 영역이 흰색)를 보여준다.
            lower, upper = self._preview_hsv
            mask = build_mask(frame, lower, upper)
            self._last_detection = None
            self._last_detection_outside_mat = False
            self.coord_label.setText("좌표: -")
            self._update_fps()
            self.camera_label.setPixmap(self._mask_to_pixmap(mask))
            return

        # 공 검출 (못 찾아도 예외 없이 None만 돌아온다)
        raw_detection = self.detector.detect(frame)
        detection = raw_detection
        outside_mat = False
        if (
            raw_detection is not None
            and self.calibration.filter_outside_mat
            and not self.calibration.is_inside(raw_detection.x, raw_detection.y)
        ):
            # 매트 영역 밖에서 검출된 건 오검출로 보고 추적/기록에서는 무시한다
            # (검출 안 된 것과 동일하게 처리). 다만 화면에는 "찾았지만 무시했다"는
            # 걸 알 수 있게 경고색으로 표시한다 - 그냥 "-"로만 보이면 공을 아예
            # 못 찾은 것과 구분이 안 돼서, 왜 궤적이 안 그려지는지 알기 어렵다.
            outside_mat = True
            detection = None
        self._last_detection = detection
        self._last_detection_outside_mat = outside_mat
        self._update_coord_label(detection, outside_mat)

        now = time.time()
        if detection is not None:
            self._draw_detection(frame, detection)
            # TRACKING 상태일 때만 실제로 기록된다 (Tracker가 알아서 상태를 확인한다).
            if self.tracker.add_trajectory_point(detection.x, detection.y, now):
                self._render_trajectory_panel()
            finish_reason = self.tracker.update_detection(detection.x, detection.y, now)
        else:
            if outside_mat:
                self._draw_outside_mat_detection(frame, raw_detection)
            finish_reason = self.tracker.update_detection(None, None, now)

        if finish_reason is not None:
            self._on_tracking_finished()

        if self.tracker.start_position is not None:
            self._draw_start_point(frame, self.tracker.start_position)

        if self.tracker.state == TrackerState.CALIBRATING:
            self._draw_calibration_corners(frame, self.calibration.corners)
        if self.calibration.is_calibrated():
            # 4점을 다 찍은 직후(아직 CALIBRATING 상태일 때)도 매트 영역이
            # 바로 확인되도록, 두 그리기를 동시에 할 수 있게 elif가 아닌 if로 둔다.
            self._draw_mat_outline(frame, self.calibration.corners)

        self._update_fps()
        pixmap = self._frame_to_pixmap(frame, self.camera_label.size())
        self.camera_label.setPixmap(pixmap)

    def _on_tracking_finished(self) -> None:
        """정지·이탈 판정으로 TRACKING -> FINISHED 자동 전환이 일어났을 때 UI를 갱신한다."""
        self._update_status_badge()
        self._update_button_states()
        self._update_stop_point_label()
        self._render_trajectory_panel()

    def _update_coord_label(self, detection: BallDetection | None, outside_mat: bool = False) -> None:
        """검출된 공의 중심 좌표를 상태 라벨 옆 배지에 표시한다.

        공을 아예 못 찾았을 때는 "-", 찾긴 했지만 매트 밖이라 무시된
        경우에는 "매트 밖"으로 구분해서 보여준다.
        """
        if detection is not None:
            self.coord_label.setText(f"좌표: {self._format_point((detection.x, detection.y))}")
        elif outside_mat:
            self.coord_label.setText("좌표: 매트 밖")
        else:
            self.coord_label.setText("좌표: -")

    @staticmethod
    def _draw_detection(frame, detection: BallDetection) -> None:
        """검출된 공 위치에 원과 중심점을 그린다. (frame을 그 자리에서 수정)"""
        center = (detection.x, detection.y)
        cv2.circle(frame, center, detection.radius, BALL_CIRCLE_COLOR_BGR, BALL_CIRCLE_THICKNESS)
        cv2.circle(frame, center, BALL_CENTER_RADIUS, BALL_CENTER_COLOR_BGR, -1)

    @staticmethod
    def _draw_outside_mat_detection(frame, detection: BallDetection) -> None:
        """매트 영역 밖이라 무시된 검출을 경고색 원으로 그린다. (frame을 그 자리에서 수정)

        공을 아예 못 찾은 것과 달리, 카메라에는 보이지만 캘리브레이션한
        매트 영역 밖이라 추적/기록에서 제외됐다는 것을 한눈에 알 수
        있게 한다 (매트 영역이 실제 이동 경로보다 좁게 잡혀서 궤적이
        중간에 끊기는 문제를 바로 알아챌 수 있다).
        """
        center = (detection.x, detection.y)
        cv2.circle(frame, center, detection.radius, OUTSIDE_MAT_DETECTION_COLOR_BGR, BALL_CIRCLE_THICKNESS)

    @staticmethod
    def _draw_start_point(frame, position: tuple) -> None:
        """등록된 시작점 위치에 골드 십자가 + 링을 그린다. (frame을 그 자리에서 수정)"""
        cv2.drawMarker(
            frame,
            position,
            START_POINT_COLOR_BGR,
            markerType=cv2.MARKER_CROSS,
            markerSize=START_POINT_MARKER_SIZE,
            thickness=START_POINT_THICKNESS,
        )
        cv2.circle(frame, position, START_POINT_RING_RADIUS, START_POINT_COLOR_BGR, START_POINT_THICKNESS)

    @staticmethod
    def _draw_calibration_corners(frame, corners: list) -> None:
        """캘리브레이션 중 클릭한 매트 모서리를 골드 점 + 번호로 그린다. (frame을 그 자리에서 수정)"""
        for i, (x, y) in enumerate(corners, start=1):
            cv2.circle(frame, (x, y), CALIBRATION_POINT_RADIUS, START_POINT_COLOR_BGR, -1)
            cv2.putText(
                frame,
                str(i),
                (x + 10, y - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                CALIBRATION_FONT_SCALE,
                START_POINT_COLOR_BGR,
                CALIBRATION_FONT_THICKNESS,
                cv2.LINE_AA,
            )

    @staticmethod
    def _draw_mat_outline(frame, corners: list) -> None:
        """캘리브레이션된 매트 영역(모서리 4점)을 얇은 골드 선으로 그린다. (frame을 그 자리에서 수정)"""
        if len(corners) != 4:
            return
        points = np.array(corners, dtype=np.int32)
        cv2.polylines(frame, [points], isClosed=True, color=MAT_OUTLINE_COLOR_BGR, thickness=MAT_OUTLINE_THICKNESS)

    def _show_camera_not_connected(self) -> None:
        """카메라 패널에 안내 문구를 표시하고 FPS·좌표·검출 상태를 초기화한다."""
        self.camera_label.setText(CAMERA_NOT_CONNECTED_TEXT)
        self.fps_label.setText("FPS: -")
        self.coord_label.setText("좌표: -")
        self._last_detection = None
        self._last_detection_outside_mat = False
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

    def _frame_to_pixmap(self, frame, target_size) -> QPixmap:
        """OpenCV 프레임(BGR numpy 배열)을 target_size에 맞는 QPixmap으로 바꾼다."""
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        height, width, channels = rgb_frame.shape
        bytes_per_line = channels * width
        # numpy 배열 메모리가 QImage보다 먼저 해제되지 않도록 .copy()로 복사한다.
        image = QImage(
            rgb_frame.data, width, height, bytes_per_line, QImage.Format_RGB888
        ).copy()
        pixmap = QPixmap.fromImage(image)
        return pixmap.scaled(
            target_size,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

    def _render_trajectory_panel(self) -> None:
        """궤적 리스트를 바탕으로 아래쪽 궤적 결과 캔버스와 오른쪽 통계 패널을 새로 그린다.

        캘리브레이션이 됐으면 매트 실제 비율(가로:세로)에 맞춘 캔버스에
        mm 좌표로 변환해서 그리고, 안 됐으면(또는 아직 카메라 프레임
        크기를 모르면) 기존처럼 카메라 프레임 비율 그대로 픽셀 좌표로
        그린다. 통계 패널은 캔버스와 무관하므로 항상 별도로 갱신한다.
        """
        self._update_stats_panel()

        if self.calibration.is_calibrated():
            canvas = self._build_real_trajectory_canvas()
        elif self._frame_size is not None:
            canvas = self._build_pixel_trajectory_canvas()
        else:
            return

        pixmap = self._frame_to_pixmap(canvas, self.trajectory_label.size())
        self.trajectory_label.setPixmap(pixmap)

    def _build_pixel_trajectory_canvas(self):
        """캘리브레이션이 안 됐을 때: 카메라 프레임과 같은 비율·좌표로 궤적 캔버스를 만든다."""
        width, height = self._frame_size
        canvas = np.full((height, width, 3), TRAJECTORY_BG_COLOR_BGR, dtype=np.uint8)
        points = [(p.x, p.y) for p in self.tracker.trajectory]
        self._paint_trajectory(canvas, points, self.tracker.start_position, self.tracker.stop_position)
        return canvas

    def _build_real_trajectory_canvas(self):
        """캘리브레이션됐을 때: 매트 실제 비율(가로:세로)로 캔버스를 만들고 mm 좌표로 그린다."""
        width = TRAJECTORY_REAL_CANVAS_WIDTH
        height = max(1, round(width * self.calibration.mat_height_mm / self.calibration.mat_width_mm))
        canvas = np.full((height, width, 3), TRAJECTORY_BG_COLOR_BGR, dtype=np.uint8)

        def to_canvas(mm_point):
            mx, my = mm_point
            # 매트 밖 픽셀은 원근 변환상 mat_width_mm/mat_height_mm을 넘는 값으로
            # 계산될 수 있다 (원근 변환은 캘리브레이션 사각형 밖도 수학적으로
            # 계속 외삽하기 때문). filter_outside_mat이 그런 점이 궤적에
            # 기록되는 것 자체는 막아주지만, 혹시 몰라 캔버스 밖으로 그려지지
            # 않도록 여기서도 0~캔버스 크기 범위로 한 번 더 잘라낸다.
            canvas_x = int(mx / self.calibration.mat_width_mm * width)
            canvas_y = int(my / self.calibration.mat_height_mm * height)
            canvas_x = max(0, min(width - 1, canvas_x))
            canvas_y = max(0, min(height - 1, canvas_y))
            return (canvas_x, canvas_y)

        points = [to_canvas(self.calibration.pixel_to_real(p.x, p.y)) for p in self.tracker.trajectory]
        start = to_canvas(self.calibration.pixel_to_real(*self.tracker.start_position)) \
            if self.tracker.start_position is not None else None
        stop = to_canvas(self.calibration.pixel_to_real(*self.tracker.stop_position)) \
            if self.tracker.stop_position is not None else None

        self._paint_trajectory(canvas, points, start, stop)
        return canvas

    def _paint_trajectory(self, canvas, points: list, start_point: tuple | None, stop_point: tuple | None) -> None:
        """캔버스 위에 시작점(골드 점)·경로(라임 선)·현재 위치(흰 점)·정지 위치(골드 링)를 그린다.

        points/start_point/stop_point는 이미 캔버스 좌표계로 변환된
        값이어야 한다 (픽셀 그대로거나, mm을 캔버스 크기로 스케일한 값).
        """
        if start_point is not None:
            cv2.circle(canvas, start_point, TRAJECTORY_START_RADIUS, START_POINT_COLOR_BGR, -1)

        self._draw_trajectory_segments(canvas, points)

        if points:
            cv2.circle(canvas, points[-1], TRAJECTORY_CURRENT_RADIUS, TRAJECTORY_CURRENT_COLOR_BGR, -1)

        if stop_point is not None:
            cv2.circle(canvas, stop_point, TRAJECTORY_STOP_RADIUS, START_POINT_COLOR_BGR, TRAJECTORY_STOP_THICKNESS)

    def _draw_trajectory_segments(self, canvas, points: list) -> None:
        """points(캔버스 좌표계, 픽셀 그대로거나 mm 변환됨)를 선으로 잇는다.

        오검출이 섞였거나 공을 다시 옮겨서 생긴 큰 점프는 선으로 잇지
        않고 구간을 끊어서, 매트를 가로지르는 가짜 직선(부챗살)이 그려지지
        않게 한다. 점프 여부는 항상 '원본 픽셀' 궤적의 이동 거리 기준
        (Tracker.max_jump_distance)으로 판단한다 - 캔버스 좌표가 mm로
        바뀌어도(원근 변환 때문에 픽셀 거리와 mm 거리가 비례하지 않아도)
        판단 기준이 흔들리지 않게 하기 위해서다. points는 항상
        self.tracker.trajectory와 같은 순서·개수로 만들어진다고 가정한다.
        """
        if len(points) < 2:
            return

        px_trajectory = self.tracker.trajectory
        max_jump_distance = self.tracker.max_jump_distance

        segment = [points[0]]
        for i in range(1, len(points)):
            prev_px, curr_px = px_trajectory[i - 1], px_trajectory[i]
            px_distance = ((curr_px.x - prev_px.x) ** 2 + (curr_px.y - prev_px.y) ** 2) ** 0.5
            if px_distance >= max_jump_distance:
                if len(segment) >= 2:
                    self._draw_polyline(canvas, segment)
                segment = [points[i]]
            else:
                segment.append(points[i])

        if len(segment) >= 2:
            self._draw_polyline(canvas, segment)

    @staticmethod
    def _draw_polyline(canvas, segment: list) -> None:
        cv2.polylines(
            canvas,
            [np.array(segment, dtype=np.int32)],
            isClosed=False,
            color=TRAJECTORY_PATH_COLOR_BGR,
            thickness=TRAJECTORY_PATH_THICKNESS,
        )

    def _trajectory_to_real(self) -> list[TrajectoryPoint]:
        """현재 궤적 전체를 매트 실제 좌표(mm)로 변환한다.

        호출하기 전에 self.calibration.is_calibrated()가 True인지
        확인해야 한다 (그래야 pixel_to_real이 항상 값을 돌려준다).
        """
        result = []
        for p in self.tracker.trajectory:
            real_x, real_y = self.calibration.pixel_to_real(p.x, p.y)
            result.append(TrajectoryPoint(x=real_x, y=real_y, timestamp=p.timestamp))
        return result

    def _update_stats_panel(self) -> None:
        """오른쪽 요약 통계 패널을 갱신한다.

        FINISHED 상태이고 캘리브레이션이 됐고 궤적 점이 2개 이상이면
        cm·cm/초 단위로 실제 수치를 보여준다. 캘리브레이션이 안 됐으면
        px 대신 "캘리브레이션 후 표시됩니다" 안내로 대신한다.
        """
        if self.tracker.state != TrackerState.FINISHED:
            self.stats_label.setText(STATS_PLACEHOLDER_TEXT)
            return

        if not self.calibration.is_calibrated():
            self.stats_label.setText(STATS_NEEDS_CALIBRATION_TEXT)
            return

        real_trajectory = self._trajectory_to_real()
        real_start = (
            self.calibration.pixel_to_real(*self.tracker.start_position)
            if self.tracker.start_position is not None
            else None
        )
        real_stop = (
            self.calibration.pixel_to_real(*self.tracker.stop_position)
            if self.tracker.stop_position is not None
            else None
        )

        stats = compute_trajectory_stats(real_trajectory, real_start, real_stop)
        if stats is None:
            self.stats_label.setText(STATS_PLACEHOLDER_TEXT)
            return

        # mm -> cm
        self.stats_label.setText(
            "<b>요약 통계</b><br>"
            f"총 거리: {stats.total_distance / 10:.1f}cm<br>"
            f"소요 시간: {stats.duration:.2f}초<br>"
            f"평균 속도: {stats.average_speed / 10:.1f}cm/초<br>"
            f"최고 속도: {stats.max_speed / 10:.1f}cm/초<br>"
            f"직진성: {stats.straightness:.2f}"
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

    def resizeEvent(self, event) -> None:
        """창 크기가 바뀔 때 궤적 결과 패널을 새 크기에 맞게 다시 그린다.

        카메라 패널은 8ms마다 도는 타이머가 다음 프레임에 알아서 현재
        라벨 크기로 다시 스케일링해서 그리지만(_frame_to_pixmap), 궤적
        패널은 새 궤적 점이 생길 때만 다시 그려지므로, 창 크기 변경
        시점에 즉시 다시 그려주지 않으면 리사이즈 전 크기로 남아 있게
        된다.
        """
        super().resizeEvent(event)
        self._render_trajectory_panel()

    def closeEvent(self, event) -> None:
        """창을 닫을 때 타이머를 멈추고 카메라 장치를 반납한다."""
        self._camera_timer.stop()
        self.camera.release()
        if self.tuning_dialog is not None:
            self.tuning_dialog.close()
        if self.settings_dialog is not None:
            self.settings_dialog.close()
        if self.records_dialog is not None:
            self.records_dialog.close()
        super().closeEvent(event)
