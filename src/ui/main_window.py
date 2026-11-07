"""
볼 트래커 - 설정·확인용 메인 창

17단계부터 이 창은 "엔진(src/engine.py)을 들여다보는 화면"일 뿐이다.
카메라 읽기·공 검출·추적·저장·앱 통신은 모두 VisionEngine이 하고, 이 창은
엔진이 보내는 시그널(frame_processed 등)을 받아 그리기만 하며, 버튼은 엔진
메서드를 호출한다. 화면 없이 엔진만 돌릴 때(python main.py)는 이 창이 필요 없다.
화면과 함께 실행하려면 python main.py --ui 로 시작한다.

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

import logging
import time

import cv2
import numpy as np

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.calibration import CORNER_NAMES
from src.config import load_settings, save_settings
from src.detector import (
    auto_color_range,
    sample_ball_pixels,
    DEFAULT_DETECTION_SETTINGS,
    BallDetection,
    build_mask,
)
from src.engine import (
    CALIBRATION_AUTO_FAILED,
    SAVE_NO_TRAJECTORY,
    SAVE_NOT_CALIBRATED,
    SAVE_OK,
    START_NO_BALL,
    START_OK,
    START_OUTSIDE_MAT,
    FrameResult,
    VisionEngine,
)
from src.protocol_server import DEFAULT_ENABLED as DEFAULT_PROTOCOL_ENABLED
from src.protocol_server import DEFAULT_PORT as DEFAULT_PROTOCOL_PORT
from src.tracker import TrackerState
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
# 녹색 매트 자동 검출에 실패했을 때 보여줄 안내 문구 (이후 4점 클릭으로 진행)
AUTO_CALIBRATION_FAILED_MESSAGE = "매트 자동 검출 실패 - 모서리 4점을 클릭하세요"

# --- 23단계: 수동 4점 캘리브레이션 ---
# 4점을 다 찍은 뒤, 화면에서 점과 이 거리(화면 픽셀) 안을 누르면 그 점을 잡아 드래그할 수 있다
CORNER_DRAG_RADIUS_SCREEN_PX = 15
# 돋보기: 화면에 보이는 영상을 몇 배로 확대할지, 돋보기 창 크기(px)
MAGNIFIER_ZOOM = 3
MAGNIFIER_SIZE = 180
MAGNIFIER_CROSS_COLOR_BGR = (0, 0, 255)  # 돋보기 가운데 십자선 (빨강)
# 자동 검출 '제안' 모서리 표시 색 (하늘색 - 확정된 골드 모서리와 구분)
PROPOSAL_COLOR_BGR = (255, 200, 0)
# 드래그 중인 점 표시 색 (빨강)
DRAG_COLOR_BGR = (0, 0, 255)
logger = logging.getLogger("ui")

# 24단계: 화면에서 공을 클릭해 색 잡기 - 안내 띠 문구
COLOR_PICK_GUIDE_TEXT = "색 추출: 공의 가운데를 클릭하세요 (돋보기로 정확히) · 오른쪽 클릭: 취소"
# 24단계: 검색 영역(ROI) 표시 색 (디버그 표시를 켰을 때만 그린다)
SEARCH_AREA_COLOR_BGR = (0, 200, 255)  # 주황
SEARCH_AREA_LOST_COLOR_BGR = (0, 0, 255)  # 공을 놓쳐 넓힌 상태는 빨강

# 캘리브레이션 안내 띠 높이(px). 숨겨져 있어도 이 높이만큼 자리를 비워 둔다 (화면 크기 고정용)
GUIDE_LABEL_HEIGHT = 64
# 4점이 거절된 이유 코드 -> 화면에 보여줄 문구
CORNER_PROBLEM_TEXTS = {
    "bad_corners": "점 순서(좌상 → 우상 → 우하 → 좌하)나 사각형 모양이 올바르지 않습니다",
    "corners_out_of_frame": "화면 밖에 찍힌 점이 있습니다",
}

# 매트 영역 밖에서 검출됐지만 무시된 공 표시 색 (경고색)
OUTSIDE_MAT_DETECTION_COLOR_BGR = (0, 100, 255)  # 주황빛 빨강

# '저장' 버튼 관련 안내 문구
SAVE_NEEDS_CALIBRATION_MESSAGE = "캘리브레이션이 필요합니다"
SAVE_NO_TRAJECTORY_MESSAGE = "저장할 궤적이 없습니다"
SAVE_SUCCESS_MESSAGE = "저장 완료"

# 카메라 화면 격자선 (렌즈 왜곡 보정 때 직선인지 보기 쉽게). 가로·세로 각각 몇 칸으로 나눌지
GRID_DIVISIONS = 10
GRID_COLOR_BGR = (255, 255, 0)  # 하늘색 (매트 녹색·공 색과 잘 구분됨)
GRID_THICKNESS = 1

# 검출 디버그 표시 (덩어리 면적·지름 글자).
#   흰색 = 공 조건 통과, 주황 = 크기 때문에 걸러짐 (컵 등), 분홍 = 모양(원형도) 때문에 걸러짐,
#   하늘색 = 고정 물체(컵·티) 자리라 뺌
DEBUG_ACCEPTED_COLOR_BGR = (255, 255, 255)
DEBUG_REJECTED_COLOR_BGR = (0, 165, 255)
DEBUG_SHAPE_REJECTED_COLOR_BGR = (203, 102, 255)
DEBUG_STATIC_REJECTED_COLOR_BGR = (255, 200, 120)
# 25단계: 고정 물체(컵·티)를 그리는 파란 원 (디버그 표시를 켰을 때)
STATIC_OBJECT_COLOR_BGR = (255, 80, 0)
# 고정 물체 수집 중 안내 문구
STATIC_SCAN_GUIDE_TEXT = (
    "고정 물체(컵·티)를 찾는 중입니다 (약 3초) - 공과 손을 움직이지 마세요. "
    "'고정 물체 다시 찾기'는 공을 매트에서 치운 뒤 누르세요."
)
STATIC_SCAN_BUTTON_TEXT = "고정 물체 다시 찾기"
STATIC_SCANNING_BUTTON_TEXT = "고정 물체 찾는 중..."
DEBUG_FONT_SCALE = 0.5
DEBUG_FONT_THICKNESS = 1

# 카메라가 없을 때, 다시 연결됐는지 확인하는 문구
CAMERA_NOT_CONNECTED_TEXT = "카메라를 연결해주세요"


class MainWindow(QMainWindow):
    """볼 트래커 설정·확인용 메인 창. 엔진(VisionEngine)을 들여다보고 조작한다."""

    def __init__(self, engine: VisionEngine):
        super().__init__()

        self.setWindowTitle("볼 트래커")
        self.resize(1100, 650)

        # 실제 일(카메라·검출·추적·통신·저장)은 전부 엔진이 한다. 이 창은 엔진이
        # 보내는 시그널을 받아 그리고, 버튼을 누르면 엔진 메서드를 호출할 뿐이다.
        self.engine = engine
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

        # 앱(Flutter) 통신 상태 (꺼짐 / 대기 중 / 접속 N대 / 포트 사용 불가)
        self.app_label = QLabel("앱: -")
        self.app_label.setObjectName("status")
        top_layout.addWidget(self.app_label)
        self._update_app_label()

        self.fps_label = QLabel("FPS: -")
        self.fps_label.setObjectName("status")
        top_layout.addWidget(self.fps_label)

        # 1-1) 경고 띠: 해상도/왜곡 보정이 바뀌어 캘리브레이션을 다시 해야 할 때만 보인다
        self.warning_label = QLabel("")
        self.warning_label.setObjectName("warning")
        self.warning_label.setWordWrap(True)
        self.warning_label.hide()
        root_layout.addWidget(self.warning_label)

        # 1-2) 캘리브레이션 안내 띠: CALIBRATING 상태에서만 보인다 (몇 번째 점을 어디에 찍을지 등)
        self.guide_label = QLabel("")
        self.guide_label.setObjectName("guide")
        self.guide_label.setWordWrap(True)
        # 안내 띠가 나타나고 사라질 때 카메라 화면 크기가 바뀌지 않도록 자리를 항상 확보해 둔다.
        # - 높이 고정: 두 줄(거절 이유 + 안내)까지 들어가는 높이
        # - retainSizeWhenHidden: 숨겨도 자리는 그대로 남긴다 (빈 공간으로 보임)
        # 카메라 화면 크기가 바뀌면 클릭하려던 위치가 순간 움직여서 모서리를 잘못 찍을 수 있다.
        self.guide_label.setFixedHeight(GUIDE_LABEL_HEIGHT)
        guide_policy = self.guide_label.sizePolicy()
        guide_policy.setRetainSizeWhenHidden(True)
        self.guide_label.setSizePolicy(guide_policy)
        self.guide_label.hide()
        root_layout.addWidget(self.guide_label)

        # 2) 본문: 왼쪽(카메라/궤적) + 오른쪽(버튼들)을 가로로 배치
        body_layout = QHBoxLayout()
        body_layout.setSpacing(12)
        root_layout.addLayout(body_layout)

        body_layout.addLayout(self._build_left_panel(), 3)
        body_layout.addLayout(self._build_right_panel(), 1)

        self.camera_label.clicked.connect(self._on_camera_clicked)
        self.camera_label.dragged.connect(self._on_camera_dragged)
        self.camera_label.released.connect(self._on_camera_released)
        self.camera_label.right_clicked.connect(self._on_camera_right_clicked)
        self.camera_label.hovered.connect(self._on_camera_hovered)
        self.camera_label.left.connect(self._on_camera_left)

        # 돋보기: 카메라 화면 위에 겹쳐 띄우는 작은 창 (캘리브레이션 중 마우스 주변을 확대)
        self.magnifier = QLabel(self.camera_label)
        self.magnifier.setObjectName("magnifier")
        self.magnifier.setFixedSize(MAGNIFIER_SIZE, MAGNIFIER_SIZE)
        self.magnifier.hide()
        # 마우스가 가리키는 프레임 좌표 / 드래그로 옮기는 중인 점 (번호, 위치)
        self._hover_point: tuple[int, int] | None = None
        self._drag_index: int | None = None
        self._drag_point: tuple[int, int] | None = None
        # 마지막으로 화면에 그린 프레임 (돋보기가 이걸 확대한다)
        self._last_drawn_frame = None
        # 24단계: 색 추출 모드 (검출 설정 창의 "화면에서 공 클릭해 색 잡기")
        self._pick_mode = False
        self._pick_add = False  # True면 '추가 샘플' (이전 샘플과 합침)
        self._color_samples: list = []

        # 전체 창에 QSS 스타일 적용
        self.setStyleSheet(QSS)

        # 3) 실시간 영상 표시 준비 (FPS 계산은 화면에 보여주려고 이 창이 한다)
        self._last_frame_time: float | None = None
        self._fps: float = 0.0
        # 실제로 카메라에서 읽어온 프레임의 (너비, 높이). 궤적 결과 패널을
        # 카메라와 같은 좌표계로 그리기 위해 첫 프레임을 받은 뒤 채워진다.
        self._frame_size: tuple[int, int] | None = None

        # HSV 튜닝 다이얼로그 (검출 설정 메뉴에서 연다). 슬라이더 값이 정해져
        # 있는 동안(_preview_hsv)은 엔진의 검출을 멈추고 마스크 미리보기를
        # 카메라 패널에 대신 보여준다.
        self.tuning_dialog: TuningDialog | None = None
        self._preview_hsv: tuple[list, list] | None = None

        # 프로필/매트 설정 다이얼로그 (설정 메뉴에서 연다)
        self.settings_dialog: SettingsDialog | None = None
        # 카메라 화면에 격자선을 그릴지 (설정창의 '격자선 표시'로 켠다)
        self._show_grid = False

        # 측정 결과 기록 보기 다이얼로그 (기록 메뉴에서 연다)
        self.records_dialog: RecordsDialog | None = None

        # 엔진 시그널 연결: 엔진이 알려 주면 이 창은 그리기만 한다.
        engine.frame_processed.connect(self._on_frame_processed)
        engine.camera_unavailable.connect(self._show_camera_not_connected)
        engine.changed.connect(self._refresh_after_engine_change)
        engine.trajectory_updated.connect(self._render_trajectory_panel)
        engine.tracking_started.connect(self._refresh_after_engine_change)
        engine.tracking_finished.connect(self._on_tracking_finished)
        engine.protocol_server.client_count_changed.connect(self._update_app_label)
        engine.calibration_invalidated.connect(self._update_warning_label)
        engine.static_scan_changed.connect(self._on_static_scan_changed)

        # 시작 상태(IDLE)에 맞게 배지 문구와 버튼 활성화 상태를 맞춘다.
        self._update_status_badge()
        self._update_button_states()
        self._update_start_point_label()
        self._update_stop_point_label()
        self._render_trajectory_panel()
        self._update_warning_label()

    # 엔진의 부품에 self.tracker처럼 짧게 접근하기 위한 통로.
    # (엔진이 설정을 다시 불러오면 calibration 객체가 새로 바뀌므로, 객체를 이 창에
    # 저장해 두지 않고 매번 엔진에서 꺼내 쓴다.)
    @property
    def tracker(self):
        return self.engine.tracker

    @property
    def calibration(self):
        return self.engine.calibration

    @property
    def storage(self):
        return self.engine.storage

    @property
    def protocol_server(self):
        return self.engine.protocol_server

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

        # 23단계: 캘리브레이션 중에만 보이는 버튼들 (상태 전환이 아니라 모서리 지정 보조 동작)
        #   - 이 모서리로 확정 / 직접 지정: 자동 검출 '제안'이 떠 있을 때만
        #   - 다시 지정: 찍은 점을 모두 지우고 처음부터 (캘리브레이션 버튼을 다시 누르는 것과 같음)
        self._calibration_buttons: dict[str, QPushButton] = {}
        for name, handler in (
            ("이 모서리로 확정", self._accept_proposal),
            ("직접 지정", self._reject_proposal),
            ("다시 지정", self._restart_calibration),
        ):
            button = QPushButton(name)
            button.clicked.connect(lambda _checked=False, h=handler: h())
            button.hide()
            layout.addWidget(button)
            self._calibration_buttons[name] = button
        self._calibration_buttons["이 모서리로 확정"].setObjectName("primary")

        # 25단계: 고정 물체(컵·티) 다시 찾기. 공을 치운 상태에서 누르면 약 3초 동안 다시 모은다.
        # (상태 전환 버튼이 아니라서 BUTTON_ACTIONS와 따로 둔다)
        self.static_scan_button = QPushButton(STATIC_SCAN_BUTTON_TEXT)
        self.static_scan_button.clicked.connect(self._rescan_static_objects)
        layout.addWidget(self.static_scan_button)

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
                # 엔진이 모서리를 지우고 4점 클릭 모드로 들어간다 (23단계부터 기본은 수동).
                # 자동 검출을 켜 둔 경우에는 결과를 '제안'으로만 보여주고 저장하지 않는다.
                # 다시 눌러도 항상 새로 시작한다.
                auto_ok = self.engine.start_calibration() != CALIBRATION_AUTO_FAILED
            elif action == "start_tracking":
                self.engine.start_tracking()
            elif action is not None:
                getattr(self.tracker, action)()
            self._update_status_badge()
            if action == "calibrate" and not auto_ok:
                # 상태 배지를 먼저 갱신한 뒤 안내 문구를 잠깐 덮어 보여준다.
                self._show_temporary_status(AUTO_CALIBRATION_FAILED_MESSAGE)

        self._update_button_states()
        self._update_start_point_label()
        self._update_stop_point_label()
        self._render_trajectory_panel()

    def _on_camera_clicked(self, x: int, y: int) -> None:
        """카메라 화면 왼쪽 클릭. CALIBRATING 상태일 때만 쓴다.

        - 4점을 다 찍은 상태: 점 근처를 누르면 그 점을 '잡는다' (드래그로 옮기기 시작)
        - 아직 4점 미만: 매트 모서리로 등록한다. 4번째 점을 찍으면 엔진이 모양을 검사해서
          괜찮으면 원근 변환 행렬을 만들어 저장하고, 이상하면 저장하지 않고 이유를 남긴다.
        """
        if self._pick_mode:
            self._pick_color_at(x, y)
            return
        if self.tracker.state != TrackerState.CALIBRATING:
            return
        if len(self.calibration.corners) == 4:
            scale = self.camera_label.display_scale() or 1.0
            index = self.calibration.nearest_corner(x, y, CORNER_DRAG_RADIUS_SCREEN_PX * scale)
            if index is not None:
                self._drag_index = index
                self._drag_point = (x, y)
            return
        self.engine.add_calibration_corner(x, y)
        self._after_calibration_edit()

    def _on_camera_dragged(self, x: int, y: int) -> None:
        """점을 잡은 채 움직이는 중: 화면에만 새 위치를 보여준다 (놓을 때 저장)."""
        if self._drag_index is not None:
            self._drag_point = (x, y)

    def _on_camera_released(self, x: int, y: int) -> None:
        """잡은 점을 놓음: 엔진이 새 위치를 검사하고, 괜찮으면 행렬을 다시 계산해 저장한다."""
        if self._drag_index is None:
            return
        index = self._drag_index
        self._drag_index = None
        self._drag_point = None
        problem = self.engine.move_calibration_corner(index, x, y)
        self.engine.corner_problem = problem if problem in CORNER_PROBLEM_TEXTS else None
        self._after_calibration_edit()

    def _on_camera_right_clicked(self) -> None:
        """오른쪽 클릭: 마지막으로 찍은 점을 취소한다 (색 추출 모드에서는 색 추출 취소)."""
        if self._pick_mode:
            self._cancel_color_pick()
            return
        self._drag_index = None
        self._drag_point = None
        if self.engine.undo_last_corner():
            self._after_calibration_edit()

    def _on_camera_hovered(self, x: int, y: int) -> None:
        self._hover_point = (x, y)
        self._update_magnifier()

    def _on_camera_left(self) -> None:
        self._hover_point = None
        self.magnifier.hide()

    def _accept_proposal(self) -> None:
        """'이 모서리로 확정': 자동 검출 제안을 저장한다."""
        self.engine.accept_calibration_proposal()
        self._after_calibration_edit()

    def _reject_proposal(self) -> None:
        """'직접 지정': 제안을 버리고 4점을 직접 찍는다."""
        self.engine.reject_calibration_proposal()
        self._after_calibration_edit()

    def _rescan_static_objects(self) -> None:
        """'고정 물체 다시 찾기': 엔진이 약 3초 동안 가만히 있는 공 색 물체(컵·티)를 다시 모은다."""
        if not self.engine.rescan_static_objects():
            self._show_temporary_status("추적 대기·추적 중에는 고정 물체를 다시 찾을 수 없습니다")

    def _on_static_scan_changed(self, active: bool) -> None:
        """고정 물체 수집이 시작되거나 끝났을 때: 버튼 글자와 안내 띠를 바꾼다."""
        self.static_scan_button.setText(STATIC_SCANNING_BUTTON_TEXT if active else STATIC_SCAN_BUTTON_TEXT)
        self.static_scan_button.setEnabled(not active)
        self._update_calibration_guide()
        if not active:
            count = len(self.engine.static_objects.objects)
            holes = len(self.engine.static_objects.holes())
            self._show_temporary_status(f"고정 물체 {count}개 (홀컵 {holes}개)")

    def _restart_calibration(self) -> None:
        """'다시 지정': 찍은 점을 모두 지우고 처음부터 (캘리브레이션 버튼과 같음)."""
        self._on_button_clicked("캘리브레이션")

    def _after_calibration_edit(self) -> None:
        """모서리를 찍거나 고친 뒤 상태 배지·안내 띠·경고 띠·궤적 패널을 다시 맞춘다."""
        self._update_status_badge()
        self._update_calibration_guide()
        self._update_warning_label()
        self._render_trajectory_panel()

    def _update_calibration_guide(self) -> None:
        """캘리브레이션 안내 띠와 보조 버튼(확정/직접 지정/다시 지정)을 지금 상태에 맞게 보여준다."""
        calibrating = self.tracker.state == TrackerState.CALIBRATING
        has_proposal = calibrating and self.engine.calibration_proposal is not None
        self._calibration_buttons["이 모서리로 확정"].setVisible(has_proposal)
        self._calibration_buttons["직접 지정"].setVisible(has_proposal)
        self._calibration_buttons["다시 지정"].setVisible(calibrating)
        if self._pick_mode:
            # 색 추출 모드 안내가 캘리브레이션 안내보다 먼저다
            if self.guide_label.text() != COLOR_PICK_GUIDE_TEXT:
                self.guide_label.setText(COLOR_PICK_GUIDE_TEXT)
            if self.guide_label.isHidden():
                self.guide_label.show()
            return
        if not calibrating:
            if self.engine.static_scan_active:
                # 25단계: 고정 물체 수집 중 안내
                if self.guide_label.text() != STATIC_SCAN_GUIDE_TEXT:
                    self.guide_label.setText(STATIC_SCAN_GUIDE_TEXT)
                if self.guide_label.isHidden():
                    self.guide_label.show()
            elif not self.guide_label.isHidden():
                self.guide_label.hide()
            self.magnifier.hide()
            return

        count = len(self.calibration.corners)
        size_text = f"{self.calibration.mat_width_mm:g}x{self.calibration.mat_height_mm:g}mm"
        if has_proposal:
            text = (
                "자동 검출 제안(하늘색 선)을 확인하세요. 맞으면 '이 모서리로 확정', "
                "아니면 '직접 지정'을 누르거나 바로 모서리를 클릭하세요."
            )
        elif count < 4:
            text = (
                f"좌상 → 우상 → 우하 → 좌하 순서로 실제 측정 영역({size_text})의 모서리를 클릭하세요 "
                f"({count}/4) · 오른쪽 클릭: 마지막 점 취소"
            )
        else:
            text = (
                "4점 완료·저장됨 - 점을 드래그해 미세 조정할 수 있습니다 · 오른쪽 클릭: 마지막 점 취소 · "
                "다음 단계: '시작 위치 등록'"
            )
        problem = self.engine.corner_problem
        if problem:
            text = f"저장하지 않음: {CORNER_PROBLEM_TEXTS.get(problem, problem)}. 다시 찍어 주세요.\n" + text
        if self.guide_label.text() != text:
            self.guide_label.setText(text)
        if self.guide_label.isHidden():
            self.guide_label.show()

    def _update_magnifier(self) -> None:
        """캘리브레이션 중 마우스 주변을 확대해서 돋보기 창에 보여준다 (모서리를 정확히 찍기 위해).

        화면에 보이는 영상 기준 MAGNIFIER_ZOOM배로 보이도록, 처리 프레임에서 작은 사각형을
        잘라 돋보기 크기로 키운다. 가운데 빨간 십자선이 지금 클릭될 위치다.
        """
        point = self._drag_point or self._hover_point
        frame = self._last_drawn_frame
        scale = self.camera_label.display_scale()
        magnifier_on = self.tracker.state == TrackerState.CALIBRATING or self._pick_mode
        if not magnifier_on or point is None or frame is None or not scale:
            self.magnifier.hide()
            return
        # 돋보기 창 한 변이 프레임에서 몇 픽셀에 해당하는지 (화면 배율 x 확대 배율로 나눔)
        half = max(4, int(MAGNIFIER_SIZE * scale / MAGNIFIER_ZOOM / 2))
        x, y = point
        # 가장자리에서도 크기가 일정하도록 테두리를 검은색으로 채운 뒤 자른다
        padded = cv2.copyMakeBorder(frame, half, half, half, half, cv2.BORDER_CONSTANT, value=(0, 0, 0))
        crop = padded[y : y + 2 * half, x : x + 2 * half]
        zoomed = cv2.resize(crop, (MAGNIFIER_SIZE, MAGNIFIER_SIZE), interpolation=cv2.INTER_NEAREST)
        center = MAGNIFIER_SIZE // 2
        cv2.line(zoomed, (center, 0), (center, MAGNIFIER_SIZE), MAGNIFIER_CROSS_COLOR_BGR, 1)
        cv2.line(zoomed, (0, center), (MAGNIFIER_SIZE, center), MAGNIFIER_CROSS_COLOR_BGR, 1)
        rgb = cv2.cvtColor(zoomed, cv2.COLOR_BGR2RGB)
        image = QImage(rgb.data, MAGNIFIER_SIZE, MAGNIFIER_SIZE, 3 * MAGNIFIER_SIZE, QImage.Format_RGB888).copy()
        self.magnifier.setPixmap(QPixmap.fromImage(image))

        # 돋보기 창은 마우스 오른쪽 아래에 띄우고, 화면 밖으로 나가면 반대편으로 옮긴다
        label_point = self.camera_label.to_label_point(point)
        if label_point is None:
            self.magnifier.hide()
            return
        offset = 24
        mx = int(label_point[0]) + offset
        my = int(label_point[1]) + offset
        if mx + MAGNIFIER_SIZE > self.camera_label.width():
            mx = int(label_point[0]) - offset - MAGNIFIER_SIZE
        if my + MAGNIFIER_SIZE > self.camera_label.height():
            my = int(label_point[1]) - offset - MAGNIFIER_SIZE
        self.magnifier.move(max(0, mx), max(0, my))
        if self.magnifier.isHidden():
            self.magnifier.show()
        self.magnifier.raise_()

    def _register_start_position(self) -> None:
        """엔진에게 검출된 공의 현재 좌표를 시작점으로 등록하게 한다.

        등록 순간 공이 검출되지 않았다면 등록하지 않고, 상태 배지에
        안내 문구만 잠깐 보여준 뒤 원래 상태 문구로 되돌린다
        (상태 전환은 일어나지 않는다). 공은 보이지만 매트 영역 밖이라
        무시된 경우에는 그 사실을 알 수 있는 문구를 따로 보여준다.
        """
        result = self.engine.register_start()
        if result == START_OK:
            self._update_status_badge()
        elif result == START_OUTSIDE_MAT:
            self._show_temporary_status(OUTSIDE_MAT_MESSAGE)
        elif result == START_NO_BALL:
            self._show_temporary_status(NOT_DETECTED_MESSAGE)
        # START_BAD_STATE: 버튼이 이미 비활성화돼 있어 여기까지 오지 않는다

    def _save_result(self) -> None:
        """'저장' 버튼: 엔진에게 이번 측정 결과를 SQLite DB에 기록하게 한다.

        캘리브레이션이 안 됐거나 궤적이 너무 짧아(점 2개 미만) 통계를 계산할
        수 없으면 저장하지 않고 안내만 잠깐 보여준다. 저장에 성공하면
        "저장 완료"를 잠깐 보여준다.
        """
        status, _result_id = self.engine.save_result()
        if status == SAVE_OK:
            self._show_temporary_status(SAVE_SUCCESS_MESSAGE)
        elif status == SAVE_NOT_CALIBRATED:
            self._show_temporary_status(SAVE_NEEDS_CALIBRATION_MESSAGE)
        elif status == SAVE_NO_TRAJECTORY:
            self._show_temporary_status(SAVE_NO_TRAJECTORY_MESSAGE)

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
            if self.tracker.finish_hole is not None:
                text += f" ({self.tracker.finish_reason} - {self.tracker.finish_hole}번 컵)"
            else:
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
            self.tuning_dialog.pick_requested.connect(self._start_color_pick)
        else:
            self.tuning_dialog.refresh_profiles(profiles, active_profile)

        self.tuning_dialog.show()
        self.tuning_dialog.raise_()
        self.tuning_dialog.activateWindow()

    def _on_tuning_values_changed(self, lower: list, upper: list) -> None:
        """튜닝 창 슬라이더가 움직일 때마다 마스크 미리보기에 쓸 범위를 갱신한다.

        미리보기 중에는 엔진이 검출·추적을 멈추고 프레임만 넘겨 준다.
        """
        self._preview_hsv = (lower, upper)
        self.engine.detection_paused = True

    def _on_tuning_dialog_closed(self) -> None:
        """튜닝 창이 닫히면 마스크 미리보기·색 추출 모드를 끄고 엔진의 검출을 다시 켠다."""
        self._preview_hsv = None
        self._pick_mode = False
        self.engine.detection_paused = False
        self._update_calibration_guide()

    # ------------------------------------------------------------------
    # 24단계: 화면에서 공을 클릭해 색(HSV 범위) 잡기
    # ------------------------------------------------------------------
    def _start_color_pick(self, add: bool) -> None:
        """색 추출 모드로 들어간다. 카메라 화면에 마스크 대신 영상을 보여주고 공 클릭을 기다린다."""
        self._pick_mode = True
        self._pick_add = add
        # 검출을 멈추고 프레임만 받는다 (튜닝 미리보기와 같음)
        self.engine.detection_paused = True
        if self.tuning_dialog is not None:
            self.tuning_dialog.set_picking(True)
        self._update_calibration_guide()

    def _cancel_color_pick(self) -> None:
        self._pick_mode = False
        if self.tuning_dialog is not None:
            self.tuning_dialog.set_picking(False)
        if self._preview_hsv is None:
            self.engine.detection_paused = False
        self._update_calibration_guide()

    def _pick_color_at(self, x: int, y: int) -> None:
        """색 추출 모드에서 공을 클릭함: 주변 공 픽셀로 범위를 만들어 튜닝 창에 채운다 (저장은 아직)."""
        frame = self.engine.last_raw_frame
        self._pick_mode = False
        self._update_calibration_guide()
        if frame is None or self.tuning_dialog is None:
            return
        sample = sample_ball_pixels(frame, (x, y))
        if sample is None:
            self.tuning_dialog.apply_color_result(None, len(self._color_samples))
            return
        if not self._pick_add:
            self._color_samples = []
        self._color_samples.append(sample)
        corners = self.calibration.corners if len(self.calibration.corners) == 4 else None
        # 25단계: 캘리브레이션이 있으면 그 위치의 공 예상 면적과 비교해서 차이가 크면 경고한다
        expected = None
        if self.calibration.is_calibrated():
            expected = self.calibration.expected_ball_area_px((x, y), self.engine.ball["diameter_mm"])
        result = auto_color_range(frame, self._color_samples, corners, expected)
        if result is not None:
            logger.info(
                "공 색 추출 (샘플 %d개): HSV %s ~ %s (여유 x%g), 공 %s 면적 %.0fpx² 지름 %.1fpx 원형도 %.2f, "
                "매트 위 다른 덩어리 %d개, 면적 제안 %d ~ %d",
                len(self._color_samples),
                result.hsv_lower,
                result.hsv_upper,
                result.margin_scale,
                "찾음" if result.ball_found else "못 찾음",
                result.ball_area,
                result.ball_diameter,
                result.ball_circularity,
                result.other_blobs,
                result.suggested_min_area,
                result.suggested_max_area,
            )
            if result.area_ratio is not None:
                logger.info(
                    "  공 예상 면적 %.0fpx² 대비 x%.2f%s",
                    result.expected_area,
                    result.area_ratio,
                    " - " + result.area_warning if result.area_warning else "",
                )
        self.tuning_dialog.apply_color_result(result, len(self._color_samples))

    def _on_tuning_profile_saved(self, key: str, profile: dict) -> None:
        """'저장'을 누르면 config/settings.json에 반영하고 검출기를 새로 만든다."""
        settings = load_settings()
        detection_settings = settings.setdefault("detection", {})
        profiles = detection_settings.setdefault("profiles", {})
        profiles[key] = profile
        detection_settings["active_profile"] = key
        save_settings(settings)

        # 저장한 설정을 바로 검출에 쓰도록 엔진이 검출기를 새로 불러온다.
        # (상단 상태 배지는 Tracker 상태 전용이라 여기서는 건드리지 않는다)
        self.engine.reload_detector()

    def _open_settings_dialog(self) -> None:
        """'설정 > 프로필/매트 설정...' 메뉴를 눌렀을 때 설정 창을 연다."""
        if self.settings_dialog is None:
            self.settings_dialog = SettingsDialog(self)
            self.settings_dialog.settings_changed.connect(self._on_settings_changed)
            self.settings_dialog.protocol_settings_changed.connect(
                self._on_protocol_settings_changed
            )
            # 렌즈 왜곡 보정 슬라이더: 엔진의 전처리 값을 바로 바꿔 카메라 영상에 반영 (미리보기)
            # preview=True: 슬라이더를 움직이는 동안은 '처리 조건 바뀜' 로그를 쌓지 않는다
            self.settings_dialog.lens_k1_preview.connect(
                lambda k1: self.engine.set_undistort_k1(k1, preview=True)
            )
            self.settings_dialog.lens_k1_preview_finished.connect(self.engine.end_k1_preview)
            self.settings_dialog.grid_toggled.connect(self._on_grid_toggled)
            self.settings_dialog.detection_debug_toggled.connect(self.engine.set_detection_debug)
        else:
            # 그 사이 다른 곳(HSV 튜닝 등)에서 바뀐 내용도 반영되도록 다시 불러온다.
            self.settings_dialog.reload_from_settings()

        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def _on_settings_changed(self) -> None:
        """프로필/매트 설정 창에서 뭔가 저장되면 엔진이 검출기·캘리브레이션을 새로 불러온다."""
        self.engine.reload_settings()
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

    def _on_frame_processed(self, result: FrameResult) -> None:
        """엔진이 한 프레임 처리를 마칠 때마다 불리는 함수. 결과를 화면에 그린다.

        카메라 읽기·검출·추적·앱 전송은 이미 엔진이 끝냈다. 여기서는 엔진이 넘겨 준
        원본 프레임 위에 공 윤곽·시작점·매트 테두리를 그려서 보여주기만 한다.
        """
        raw_frame = result.frame

        # 궤적 결과 패널을 카메라와 같은 좌표계로 그리기 위해 실제 프레임 크기를 기억해둔다.
        # 크기가 처음 정해지거나 바뀔 때만 패널을 다시 그린다 (매 프레임 다시 그리면 낭비).
        new_frame_size = (raw_frame.shape[1], raw_frame.shape[0])
        if self._frame_size != new_frame_size:
            self._frame_size = new_frame_size
            self.camera_label.set_frame_size(self._frame_size)
            self._render_trajectory_panel()

        if result.paused and self._pick_mode:
            # 색 추출 모드: 마스크 대신 실제 영상을 보여줘야 공을 보고 클릭할 수 있다
            self.coord_label.setText("좌표: -")
            self._update_fps()
            self._last_drawn_frame = raw_frame
            if self._hover_point is not None:
                self._update_magnifier()
            self.camera_label.setPixmap(self._frame_to_pixmap(raw_frame, self.camera_label.size()))
            return

        if result.paused and self._preview_hsv is not None:
            # 튜닝 중에는 원본 대신 마스크(검출 영역이 흰색)를 보여준다.
            lower, upper = self._preview_hsv
            mask = build_mask(raw_frame, lower, upper)
            self.coord_label.setText("좌표: -")
            self._update_fps()
            self.camera_label.setPixmap(self._mask_to_pixmap(mask))
            return

        # 엔진이 넘긴 원본은 다른 곳(캘리브레이션 등)에서도 쓰므로, 그림은 복사본에 그린다.
        frame = raw_frame.copy()

        self._update_coord_label(result.detection, result.outside_mat)
        if self._show_grid:
            # 격자선은 가장 먼저(맨 아래에) 그려서 공·모서리 표시를 가리지 않게 한다
            self._draw_grid(frame)
        if result.candidates is not None:
            # 디버그 표시를 켰을 때만: 이번 프레임의 검색 영역(24단계)과 덩어리 정보,
            # 고정 물체(25단계, 파란 원)
            self._draw_static_objects(frame, self.engine.static_objects.objects)
            self._draw_search_area(frame, result.search_plan)
            self._draw_debug_candidates(frame, result.candidates)
        if result.detection is not None:
            self._draw_detection(frame, result.detection)
        elif result.outside_mat:
            # 공은 보이지만 매트 밖이라 무시된 검출은 경고색으로 표시한다.
            self._draw_outside_mat_detection(frame, result.raw_detection)

        if self.tracker.start_position is not None:
            self._draw_start_point(frame, self.tracker.start_position)

        corners = list(self.calibration.corners)
        if self._drag_index is not None and self._drag_point is not None and len(corners) == 4:
            # 드래그 중인 점은 아직 저장 전이지만 새 위치로 미리 보여준다
            corners[self._drag_index] = self._drag_point
        if self.tracker.state == TrackerState.CALIBRATING:
            if self.engine.calibration_proposal is not None:
                self._draw_proposal(frame, self.engine.calibration_proposal)
            self._draw_calibration_corners(frame, corners)
            if self._drag_index is not None and self._drag_point is not None:
                cv2.circle(frame, self._drag_point, CALIBRATION_POINT_RADIUS + 4, DRAG_COLOR_BGR, 2)
        if self.calibration.is_calibrated():
            # 4점을 다 찍은 직후(아직 CALIBRATING 상태일 때)도 매트 영역이
            # 바로 확인되도록, 두 그리기를 동시에 할 수 있게 elif가 아닌 if로 둔다.
            self._draw_mat_outline(frame, corners)

        self._update_fps()
        self._update_warning_label()
        self._update_calibration_guide()
        # 돋보기는 그림(모서리 점 등)이 그려진 프레임을 확대한다
        self._last_drawn_frame = frame
        if self._hover_point is not None or self._drag_point is not None:
            self._update_magnifier()
        pixmap = self._frame_to_pixmap(frame, self.camera_label.size())
        self.camera_label.setPixmap(pixmap)

    def _on_tracking_finished(self, *_reason) -> None:
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
    def _draw_proposal(frame, corners: list) -> None:
        """자동 검출 '제안' 모서리를 하늘색 선과 번호로 그린다 (아직 저장 전)."""
        points = np.array(corners, dtype=np.int32)
        cv2.polylines(frame, [points], isClosed=True, color=PROPOSAL_COLOR_BGR, thickness=2)
        for i, (x, y) in enumerate(corners, start=1):
            cv2.circle(frame, (int(x), int(y)), CALIBRATION_POINT_RADIUS, PROPOSAL_COLOR_BGR, 2)
            cv2.putText(
                frame,
                f"{i}?",
                (int(x) + 10, int(y) + 24),
                cv2.FONT_HERSHEY_SIMPLEX,
                CALIBRATION_FONT_SCALE,
                PROPOSAL_COLOR_BGR,
                CALIBRATION_FONT_THICKNESS,
                cv2.LINE_AA,
            )

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
    def _draw_grid(frame) -> None:
        """카메라 화면에 가로·세로 격자선을 그린다. 매트 테두리가 격자선과 나란하면 왜곡이 펴진 것."""
        height, width = frame.shape[:2]
        for i in range(1, GRID_DIVISIONS):
            x = round(width * i / GRID_DIVISIONS)
            y = round(height * i / GRID_DIVISIONS)
            cv2.line(frame, (x, 0), (x, height - 1), GRID_COLOR_BGR, GRID_THICKNESS)
            cv2.line(frame, (0, y), (width - 1, y), GRID_COLOR_BGR, GRID_THICKNESS)

    @staticmethod
    def _draw_static_objects(frame, objects: list) -> None:
        """고정 물체(컵·티)를 파란 원으로 그린다. 홀컵이면 옆에 H1, H2...처럼 홀 번호를 쓴다."""
        for obj in objects:
            center = (int(round(obj.x)), int(round(obj.y)))
            cv2.circle(frame, center, max(2, int(round(obj.radius))), STATIC_OBJECT_COLOR_BGR, 2)
            if obj.hole is not None:
                cv2.putText(
                    frame,
                    f"H{obj.hole}",
                    (center[0] - 10, center[1] - int(obj.radius) - 6),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    STATIC_OBJECT_COLOR_BGR,
                    2,
                    cv2.LINE_AA,
                )

    @staticmethod
    def _draw_debug_candidates(frame, candidates: list) -> None:
        """검출 디버그: 색 범위에 맞은 덩어리마다 면적(px²)·지름(px)·원형도를 글자로 쓴다.

        흰 글자 = 공 조건을 통과한 덩어리, 주황 = 크기 때문에 걸러짐, 분홍 = 모양 때문에 걸러짐,
        하늘색 = 고정 물체 자리라 뺌.
        25단계: 캘리브레이션이 있으면 '예상 대비 면적 비율'(예: x1.02, 컵은 x2.10)도 함께 쓴다.
        공이 x1.0 근처면 정상이다. 공인데 주황이면 ball.area_ratio_min/max(또는 캘리브레이션 전이면
        min_area/max_area)를 이 숫자에 맞게 고치면 된다.
        """
        colors = {
            None: DEBUG_ACCEPTED_COLOR_BGR,
            "size": DEBUG_REJECTED_COLOR_BGR,
            "shape": DEBUG_SHAPE_REJECTED_COLOR_BGR,
            "static": DEBUG_STATIC_REJECTED_COLOR_BGR,
        }
        for c in candidates:
            color = colors.get(c.reject, DEBUG_REJECTED_COLOR_BGR)
            cv2.circle(frame, (c.x, c.y), max(2, round(c.diameter / 2)), color, 1)
            text = f"A{c.area:.0f} D{c.diameter:.1f} C{c.circularity:.2f}"
            if c.area_ratio is not None:
                text += f" x{c.area_ratio:.2f}"
            cv2.putText(
                frame,
                text,
                (c.x + round(c.diameter / 2) + 4, c.y - 4),
                cv2.FONT_HERSHEY_SIMPLEX,
                DEBUG_FONT_SCALE,
                color,
                DEBUG_FONT_THICKNESS,
                cv2.LINE_AA,
            )

    @staticmethod
    def _draw_search_area(frame, plan) -> None:
        """검색 영역(ROI)을 얇은 사각형 + 원으로 그린다. 전체 검색이면 왼쪽 위에 글자로만 알린다."""
        if plan is None:
            return
        color = SEARCH_AREA_LOST_COLOR_BGR if plan.widened else SEARCH_AREA_COLOR_BGR
        if plan.roi is not None:
            x, y, w, h = plan.roi
            if w > 0 and h > 0:
                cv2.rectangle(frame, (x, y), (x + w - 1, y + h - 1), color, 1)
        if plan.center is not None and plan.radius is not None:
            center = (int(plan.center[0]), int(plan.center[1]))
            cv2.circle(frame, center, int(plan.radius), color, 1)
            cv2.drawMarker(frame, center, color, cv2.MARKER_CROSS, 10, 1)
        # OpenCV 글꼴은 한글을 못 쓰므로 영문으로 표시한다:
        # full = 전체, start = 시작점 주변, addr = 어드레스 주변, track = 예상 위치, lost = 놓쳐서 넓힘
        cv2.putText(frame, f"ROI: {plan.reason}", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)

    def _on_grid_toggled(self, enabled: bool) -> None:
        self._show_grid = bool(enabled)

    def _update_warning_label(self, *_args) -> None:
        """엔진에 캘리브레이션 경고가 있으면 경고 띠를 보여주고, 없으면 숨긴다.

        - calibration_warning: 처리 조건이 바뀌어 캘리브레이션이 무효가 됨 (다시 해야 함)
        - calibration_notice: 매트 크기와 찍은 모양의 비율이 크게 다름 (크기 오타일 수 있음)
        """
        warning = self.engine.calibration_warning
        notice = self.engine.calibration_notice
        text = ""
        if warning:
            text = f"{warning} - '캘리브레이션' 버튼을 눌러 다시 지정하세요"
        elif notice:
            text = notice
        if text:
            if self.warning_label.text() != text:
                self.warning_label.setText(text)
            if self.warning_label.isHidden():
                self.warning_label.show()
        elif not self.warning_label.isHidden():
            self.warning_label.hide()

    @staticmethod
    def _draw_mat_outline(frame, corners: list) -> None:
        """캘리브레이션된 매트 영역(모서리 4점)을 얇은 골드 선으로 그린다. (frame을 그 자리에서 수정)"""
        if len(corners) != 4:
            return
        points = np.array(corners, dtype=np.int32)
        cv2.polylines(frame, [points], isClosed=True, color=MAT_OUTLINE_COLOR_BGR, thickness=MAT_OUTLINE_THICKNESS)

    def _show_camera_not_connected(self) -> None:
        """카메라 패널에 안내 문구를 표시하고 FPS·좌표 표시를 초기화한다.

        (검출 상태 자체는 엔진이 이미 비웠다. 여기서는 화면 표시만 정리한다.)
        """
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

        segment = [points[0]]
        for i in range(1, len(points)):
            prev_px, curr_px = px_trajectory[i - 1], px_trajectory[i]
            px_distance = ((curr_px.x - prev_px.x) ** 2 + (curr_px.y - prev_px.y) ** 2) ** 0.5
            # 25단계: 기준은 그 위치 근처의 px 값 (캘리브레이션이 있으면 max_jump_distance_mm 기준)
            if px_distance >= self.tracker.max_jump_threshold((prev_px.x, prev_px.y)):
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

        # 통계 계산은 엔진이 한다 (점이 2개 미만이면 None)
        current = self.engine.current_stats()
        if current is None:
            self.stats_label.setText(STATS_PLACEHOLDER_TEXT)
            return
        stats = current[0]

        # 25단계: 종료 사유 (홀인이면 컵 번호)
        reason = self.tracker.finish_reason or "-"
        if self.tracker.finish_hole is not None:
            reason += f" ({self.tracker.finish_hole}번 컵)"

        # mm -> cm
        self.stats_label.setText(
            "<b>요약 통계</b><br>"
            f"결과: {reason}<br>"
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

    def _update_app_label(self, *_args) -> None:
        """상단의 앱 통신 상태 문구를 서버의 현재 상태로 갱신한다."""
        server = self.protocol_server
        if not server.enabled:
            text = "앱: 꺼짐"
        elif not server.is_listening():
            # 같은 포트를 다른 프로그램이 쓰고 있을 때 등
            text = f"앱: 포트 {server.port} 사용 불가"
        elif server.client_count() > 0:
            text = f"앱: 접속 {server.client_count()}대 (포트 {server.port})"
        else:
            text = f"앱: 대기 중 (포트 {server.port})"
        self.app_label.setText(text)

    def _on_protocol_settings_changed(self) -> None:
        """설정창에서 앱 통신 설정이 저장되면 서버를 새 설정으로 다시 시작한다."""
        protocol = load_settings().get("protocol", {})
        ok = self.protocol_server.apply_settings(
            protocol.get("enabled", DEFAULT_PROTOCOL_ENABLED),
            protocol.get("port", DEFAULT_PROTOCOL_PORT),
        )
        self._update_app_label()
        if not ok:
            QMessageBox.warning(
                self,
                "앱 통신",
                f"포트 {self.protocol_server.port}을(를) 열 수 없습니다.\n"
                "다른 프로그램이 이미 사용 중일 수 있습니다. 다른 포트를 지정해 주세요.",
            )

    def _refresh_after_engine_change(self) -> None:
        """앱 명령 등으로 엔진 상태가 바뀌었을 때, 버튼을 눌렀을 때와 같게 화면을 갱신한다."""
        # 엔진이 시작될 때도 이 알림이 온다 -> 통신 상태 문구("포트 사용 불가" 등)도 다시 맞춘다
        self._update_app_label()
        self._update_calibration_guide()
        self._update_status_badge()
        self._update_button_states()
        self._update_start_point_label()
        self._update_stop_point_label()
        self._render_trajectory_panel()

    def closeEvent(self, event) -> None:
        """창을 닫을 때 열려 있는 대화상자를 닫는다.

        엔진(카메라·타이머·통신 서버) 정리는 프로그램이 끝날 때 src/app.py가
        engine.shutdown()으로 한다 (창을 닫는 것 = 프로그램 종료).
        """
        if self.tuning_dialog is not None:
            self.tuning_dialog.close()
        if self.settings_dialog is not None:
            self.settings_dialog.close()
        if self.records_dialog is not None:
            self.records_dialog.close()
        super().closeEvent(event)
