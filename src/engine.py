"""
비전 엔진: 화면(UI) 없이 돌아가는 프로그램의 핵심.

예전에는 카메라 읽기·공 검출·추적·통신·저장 로직이 전부 MainWindow(화면)
안에 들어 있어서, 화면 없이는 프로그램을 돌릴 수 없었다. 이 모듈이 그
로직을 모두 가져와서 "화면 없는 엔진"으로 만든다.

    메인 컨트롤 프로그램  --JSON(TCP)-->  VisionEngine  --> 좌표·거리·속도

- 평소(헤드리스) 실행: 엔진만 돈다 (python main.py)
- 설치·조정할 때(--ui): 같은 엔진 위에 화면(MainWindow)을 얹어서 본다.
  화면은 엔진이 보내는 시그널(frame_processed 등)을 받아 그리기만 하고,
  버튼도 엔진의 메서드를 호출할 뿐이다. 그래서 화면이 있든 없든 동작이 같다.

이 파일은 PySide6.QtWidgets / QtGui를 import하지 않는다 (화면 없는 환경에서도
돌아야 하기 때문). 시그널·타이머·TCP 서버를 위한 QtCore/QtNetwork만 쓴다.

한 프레임이 처리되는 순서 (process_frame):
    카메라 읽기 -> 전처리(회전·축소·왜곡 보정, src/lens.py) -> 공 검출 -> 매트 밖 검출 무시
    -> 궤적 기록·정지/이탈 판정 -> 앱(메인 컨트롤)에 상태 전송 -> (끝났으면) 결과 전송 -> 화면에 알림

22단계부터 엔진의 모든 픽셀 좌표(검출, 시작점, 캘리브레이션 모서리, 화면 표시,
프로토콜 ALIGN corners)는 '전처리가 끝난 프레임' 기준이다. 처리 프레임 크기나
왜곡 보정 값(k1)이 바뀌면 예전에 찍은 모서리는 맞지 않으므로, 엔진이 캘리브레이션을
'안 된 상태'로 돌리고 로그·화면에 다시 하라고 알린다 (_check_processing_changed).

25단계 추가:
- 공 크기를 mm 기준으로 판정한다: 캘리브레이션으로 '그 위치에서 공이 보여야 할 면적'을 계산해
  검출기에 넘긴다 (_expected_ball_area). 흰 홀컵(공의 약 2배)은 크기로 걸러진다.
- 고정 물체(컵·티) 기억 (src/static_objects.py): 캘리브레이션 직후·시작점 등록 직후·엔진 시작
  (저장된 값이 없거나 무효일 때)·'고정 물체 다시 찾기' 버튼에서 scan_duration초 동안 화면 전체를
  지켜보고, 계속 같은 자리에 있던 공 색 물체를 기억한다. 그 자리의 후보는 공으로 보지 않는다.
- 홀인: 공이 홀컵 근처에서 사라지면 Tracker가 "홀인"으로 끝낸다. 컵 근처인지는 _hole_at이 mm로 판단.
- 이동 기준(추적 시작·궤적 기록·점프)을 mm로: Tracker에 _mm_to_px를 넣어 준다.

26단계 추가:
- 자동 흐름 (src/auto_flow.py): 버튼 없이 "공 놓기 → 퍼팅 → 결과 → 공 다시 놓기". 판단은 AutoFlow가 하고
  엔진은 그 결과대로 상태를 바꾸고 앱에 알린다 (_auto_step, _check_readdress).
- 컵 테두리에 붙어 멈춘 공: 홀인 확인 중 컵 자리 덩어리가 '컵 + 공 반 개' 이상으로 커지고 컵 밖으로
  튀어나오면 홀인이 아니라 정지로 끝낸다 (_check_ball_at_rim).
- 캘리브레이션이 있으면 '화면 전체' 검색도 매트 영역(모서리 4점을 감싸는 사각형 + 여유)만 찾는다 (_mat_roi).
"""

import logging
import math
import time
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from src.calibration import Calibration, check_corners, detect_mat_corners
from src.camera import Camera
from src.config import load_settings, save_settings
from src.detector import DEFAULT_DETECTION_SETTINGS, BallDetection, BallDetector, ball_settings
from src.lens import FramePreprocessor
from src.search import EMPTY_ROI, SearchPlan, SearchPlanner, _square_roi
from src.auto_flow import ACTION_ARM, ACTION_DROP, ACTION_READY, AutoFlow
from src.static_objects import (
    MIN_OBJECT_AREA_PX,
    MIN_OBJECT_AREA_RATIO,
    SCAN_AUTO,
    SCAN_MANUAL,
    StaticObjectCollector,
    StaticObjectMap,
    number_holes,
    static_settings,
)
from src.protocol_handler import ProtocolHandler
from src.protocol_server import ProtocolServer
from src.stats import compute_trajectory_stats
from src.storage import Storage
from src.tracker import HOLE_REASON, STOP_REASON, Tracker, TrackerState, TrajectoryPoint

logger = logging.getLogger("engine")

# 카메라를 확인하는 주기(ms). 실제 처리 속도는 카메라 프레임 도착 속도가 정한다.
CAMERA_POLL_INTERVAL_MS = 8
# 카메라가 안 열려 있을 때 다시 연결을 시도하는 간격(초). 장치가 없을 때 열기를 시도하면
# 오래 걸려서, 8ms마다 시도하면 엔진이 거의 멈춰 버린다.
CAMERA_RETRY_INTERVAL_S = 1.0
# 같은 오류가 프레임마다 반복될 때 로그에 남기는 최소 간격(초)
ERROR_LOG_INTERVAL_S = 5.0
# 상태 요약(FPS·처리 시간)을 로그에 남기는 기본 간격(초). 0이면 남기지 않는다.
DEFAULT_STATS_INTERVAL_S = 10

# 25단계: 홀인 판정 기본값 (config/settings.json의 "hole" 항목에 값이 없을 때)
DEFAULT_HOLE_SETTINGS = {
    "enabled": True,
    "capture_radius_mm": 60,  # 공이 컵 중심에서 이 거리(mm) 안에서 사라지면 홀인 후보
    "confirm_duration": 0.5,  # 그 뒤 이 시간(초) 동안 다시 안 보이면 홀인 (src/tracker.py)
    "min_area_ratio": 1.3,  # 고정 물체 면적이 공 예상 면적의 이 배수 이상이면 '홀컵'으로 본다
    # 26단계: 홀인 확인 중 컵 자리 덩어리가 '기억한 컵 면적 + 공 예상 면적 x 이 값' 이상이면 공이 붙은 것
    "stuck_ball_area_fraction": 0.5,
}
# 컵에 붙은 공으로 판단하려면 홀인 확인 중 이 횟수(프레임) 이상 그렇게 보여야 한다 (한 프레임 잡음 무시)
RIM_STUCK_MIN_FRAMES = 3
# 매트 영역 검색: 모서리 4점을 감싸는 사각형에 더하는 여유(px). 매트 가장자리의 공이 잘리지 않게
MAT_ROI_MARGIN_PX = 20
# 자동 흐름: 시작 구역을 찾을 때 반경에 더하는 여유(mm)
ZONE_SEARCH_MARGIN_MM = 50
# 화면 안내에 '자리 고쳐 놓기' 문구를 보여줄 시간(초)
AUTO_NOTICE_S = 3.0

# 공 예상 면적을 기억해 두는 칸 크기(px). 이 칸 안에서는 같은 값을 쓴다 (처리 속도용)
AREA_CACHE_CELL_PX = 16

# 처리 프레임 크기·왜곡 보정이 바뀌어 저장된 캘리브레이션을 쓸 수 없을 때 알리는 문구
CALIBRATION_STALE_MESSAGE = "해상도/왜곡 보정이 바뀌어 캘리브레이션을 다시 해야 합니다"

# start_calibration()이 돌려주는 결과 코드 (23단계)
CALIBRATION_MANUAL = "manual"  # 자동 검출 안 함(기본): 바로 4점 클릭 모드
CALIBRATION_PROPOSED = "proposed"  # 자동 검출 결과를 '제안'으로 보여줌 (아직 저장 안 됨)
CALIBRATION_AUTO_FAILED = "auto_failed"  # 자동 검출 실패: 4점 클릭 모드

# register_start()가 돌려주는 결과 코드
START_OK = "OK"
START_NO_BALL = "no_ball"  # 공이 검출되지 않음
START_OUTSIDE_MAT = "outside_mat"  # 공은 보이지만 매트 밖이라 무시됨
START_BAD_STATE = "bad_state"  # 지금 상태에서는 등록할 수 없음

# save_result()가 돌려주는 결과 코드
SAVE_OK = "OK"
SAVE_NOT_CALIBRATED = "not_calibrated"  # 캘리브레이션 전이라 실제 단위(mm)를 모름
SAVE_NO_TRAJECTORY = "no_trajectory"  # 궤적 점이 2개 미만이라 통계를 못 구함


@dataclass
class FrameResult:
    """한 프레임을 처리한 결과. 화면(UI)은 이걸 받아서 그리기만 한다."""

    # 전처리(회전·축소·왜곡 보정)가 끝난 프레임 (그림을 그리기 전). 그리려면 복사해서 쓸 것
    frame: np.ndarray
    detection: BallDetection | None  # 매트 안에서 인정된 공 (없으면 None)
    raw_detection: BallDetection | None  # 매트 밖이라 무시된 것까지 포함한 원래 검출
    outside_mat: bool  # 공은 찾았지만 매트 밖이라 무시했는지
    now: float  # 이 프레임을 처리한 시각 (time.time())
    # True면 HSV 튜닝 미리보기 중이라 검출·추적을 건너뛴 프레임이다.
    paused: bool = False
    # 검출 디버그 표시가 켜져 있을 때만 채워지는 덩어리 목록 (DetectionCandidate). 면적 측정용
    candidates: list | None = None
    # 이번 프레임의 검색 영역 (24단계, src/search.py). 디버그 표시 때 화면에 그린다
    search_plan: SearchPlan | None = None


class VisionEngine(QObject):
    """카메라·검출·추적·캘리브레이션·저장·앱 통신을 모두 묶어서 돌리는 엔진."""

    # 한 프레임 처리가 끝날 때마다 (화면이 이걸 받아서 그린다)
    frame_processed = Signal(object)
    # 카메라가 없거나 연결이 끊겨서 프레임을 못 읽었을 때
    camera_unavailable = Signal()
    # 카메라 영상 수신 상태가 바뀌었을 때 (True: 영상을 받기 시작/복구, False: 끊김)
    camera_status_changed = Signal(bool)
    # 앱 명령 등으로 상태가 바뀌었을 때 (화면이 버튼·배지를 다시 맞춘다)
    changed = Signal()
    # 궤적에 점이 새로 기록됐을 때
    trajectory_updated = Signal()
    # 공이 움직여서 추적 대기(ARMED)에서 추적(TRACKING)으로 자동 전환됐을 때
    tracking_started = Signal()
    # 정지·이탈로 추적이 자동 종료됐을 때 (종료 사유 문구를 함께 전달)
    tracking_finished = Signal(str)
    # 처리 조건이 바뀌어 저장된 캘리브레이션이 무효가 됐을 때 (알림 문구를 함께 전달)
    calibration_invalidated = Signal(str)
    # 25단계: 고정 물체 수집이 시작되거나(True) 끝났을 때(False) (화면이 안내 문구를 바꾼다)
    static_scan_changed = Signal(bool)
    # 26단계: 자동 흐름이 상태를 바꿨을 때 (자동 준비·자리 고쳐 놓기·공 집어 감 등. 화면이 버튼·배지를 다시 맞춘다)
    auto_changed = Signal()

    def __init__(self, camera=None, parent=None):
        """camera를 넘기지 않으면 실제 USB 카메라를 쓴다 (테스트에서는 가짜 카메라를 넘긴다)."""
        super().__init__(parent)

        self.camera = camera if camera is not None else Camera()
        # 카메라 원본 -> 처리 프레임 (회전·축소·왜곡 보정). 설정은 config/settings.json의 camera
        self.preprocessor = FramePreprocessor(load_settings().get("camera", {}))
        # 검출 디버그 표시(덩어리 면적·지름) 켜짐 여부. 검출기를 새로 만들어도 유지한다.
        self.detection_debug = False
        self.detector = BallDetector()
        self.tracker = Tracker()
        self.calibration = Calibration()
        # 25단계: mm 기준 공 크기·홀인·고정 물체 설정
        self._load_size_settings()
        # Tracker는 캘리브레이션을 모르므로, mm -> px 변환과 '컵 근처인지' 판단을 엔진이 넣어 준다
        self.tracker.mm_to_px = self._mm_to_px
        self.tracker.hole_finder = self._hole_at
        # 크기 판정으로 걸러진 후보 수 (상태 요약 로그용, 구간마다 비운다)
        self._stats_size_rejected = 0
        # 26단계: 자동 흐름 (버튼 없이 공을 놓고 퍼팅). 설정은 config/settings.json의 "auto"
        self.auto = AutoFlow(load_settings().get("auto"))
        # 화면에 잠깐 보여줄 자동 흐름 안내 (문구, 보여주기 시작한 시각)
        self._auto_notice: tuple[str, float] | None = None
        # 컵 테두리에 붙은 공으로 보인 프레임 수와 그때 계산한 공 위치 (홀인 확인 중에만 쓴다)
        self._rim_stuck_frames = 0
        self._rim_ball_position = None
        self.storage = Storage()
        # 24단계: 공을 화면 전체가 아니라 '있을 만한 곳' 주변에서만 찾는다 (src/search.py)
        self.search = SearchPlanner(load_settings().get("search"))
        # 검색 영역을 넓혀 찾았지만 진행 방향·거리가 맞지 않아 버린 후보 수 (로그용)
        self.rejected_candidates = 0

        # 처리 프레임 크기 (폭, 높이). 첫 프레임을 받기 전에는 모른다(None).
        self.frame_size: tuple[int, int] | None = None
        # 마지막으로 캘리브레이션을 검사한 처리 조건 (프레임 크기, k1)
        self._checked_processing: tuple | None = None
        # 캘리브레이션이 무효가 된 이유 문구 (화면이 보여준다). 문제 없으면 None
        self.calibration_warning: str | None = None
        # 23단계: 수동 캘리브레이션 관련 상태 (화면이 읽어서 보여준다)
        # - calibration_proposal: 자동 검출이 '제안'한 4점 (auto_detect가 켜져 있을 때만, 확정 전)
        # - corner_problem: 마지막으로 찍은/옮긴 4점이 왜 거절됐는지 (check_corners의 이유 코드)
        # - calibration_notice: 저장은 됐지만 확인이 필요한 점 (매트 크기와 찍은 모양의 비율 차이)
        self.calibration_proposal: list | None = None
        self.corner_problem: str | None = None
        self.calibration_notice: str | None = None
        # 설정창의 k1 슬라이더를 움직이는 중인지 (그동안은 '처리 조건 바뀜' 로그를 남기지 않는다)
        self._k1_previewing = False

        # 앱(메인 컨트롤)과 JSON으로 통신하는 TCP 서버 + 명령 처리기.
        # 서버가 받은 명령을 핸들러가 해석하고, 핸들러는 화면 버튼과 같은
        # 엔진·Tracker 메서드를 쓴다 (그래서 상태 규칙이 항상 같다).
        self.protocol_server = ProtocolServer(parent=self)
        self.protocol_handler = ProtocolHandler(
            engine=self,
            send=self.protocol_server.send,
            send_to=self.protocol_server.send_to,
            settings=load_settings().get("protocol", {}),
        )
        # 명령은 보낸 앱의 소켓과 함께 핸들러로 가서, 응답이 그 앱에만 돌아간다.
        self.protocol_server.command_received.connect(self.protocol_handler.handle)
        # 새 앱이 접속하면 인사(HELLO)와 현재 상태를 보낸다.
        self.protocol_server.client_connected.connect(self.protocol_handler.on_client_connected)

        # 카메라에서 프레임을 정상적으로 읽고 있는지 (앱의 STATUS.camera로 알려 준다).
        # 아직 첫 프레임을 읽기 전이거나 끊겨 있으면 False.
        self.camera_ok = False
        # 지금까지 처리한 프레임 수 (앱이 프레임 누락·지연을 알 수 있게 STATUS에 실어 보낸다)
        self.frame_count = 0
        # 카메라 재연결 시도 간격(초)과 마지막 시도 시각. 테스트에서는 0으로 줄여 쓴다.
        self.camera_retry_interval = CAMERA_RETRY_INTERVAL_S
        self._last_open_attempt = float("-inf")
        # 마지막으로 알린 카메라 상태 (바뀔 때만 로그·이벤트를 내보내려고 기억한다. None = 아직 모름)
        self._camera_reported: bool | None = None
        # 프레임 처리 중 난 오류 횟수와, 마지막으로 오류를 로그에 남긴 시각
        self.error_count = 0
        self._last_error_log = float("-inf")
        # 상태 요약 로그용 집계 (log_stats가 읽고 비운다)
        self._stats_since = time.perf_counter()
        self._stats_frames = 0
        self._stats_proc_total = 0.0
        self._stats_proc_max = 0.0
        self._stats_pre_total = 0.0  # 그중 전처리(축소·왜곡 보정)에 걸린 시간 합계
        self._stats_detect_total = 0.0  # 그중 공 검출에 걸린 시간 합계 (24단계)
        self._stats_roi_ratio_total = 0.0  # 검색 영역 넓이 / 화면 넓이 합계 (전체 검색 = 1.0)
        self._stats_detect_frames = 0
        self._proc_started = 0.0

        # 가장 최근 프레임의 정보 (앱 명령 처리와 화면이 읽어 간다)
        self.last_raw_frame: np.ndarray | None = None
        # 매트 안에서 인정된 공. '시작 위치 등록'이 이 값을 시작점으로 쓴다.
        self.last_detection: BallDetection | None = None
        # 방금 프레임에서 공을 찾긴 했지만 매트 밖이라 무시했는지
        self.last_detection_outside_mat = False

        # True면 검출·추적을 건너뛰고 프레임만 화면에 넘긴다 (HSV 튜닝 미리보기용)
        self.detection_paused = False

        # timeout이 보내는 값에 영향받지 않도록 lambda로 감싼다 (process_frame의 now와 혼동 방지)
        self._timer = QTimer(self)
        self._timer.setInterval(CAMERA_POLL_INTERVAL_MS)
        self._timer.timeout.connect(lambda: self.process_frame())

        # 상태 요약을 주기적으로 로그에 남기는 타이머 (간격 0이면 만들지 않는다)
        stats_interval = load_settings().get("logging", {}).get(
            "stats_interval_s", DEFAULT_STATS_INTERVAL_S
        )
        self._stats_timer: QTimer | None = None
        if stats_interval and stats_interval > 0:
            self._stats_timer = QTimer(self)
            self._stats_timer.setInterval(int(stats_interval * 1000))
            self._stats_timer.timeout.connect(self.log_stats)

        # 시작하자마자 k1·처리 조건 기록 여부만이라도 먼저 검사한다 (예전 설정 파일은 여기서
        # 무효 처리된다). 프레임 크기는 첫 프레임을 받은 뒤 _check_processing_changed에서 검사한다.
        self._validate_calibration()
        self._validate_static_objects()
        # 저장된 모서리 모양과 매트 크기의 비율이 크게 다르면(크기 오타) 경고를 남긴다.
        self._update_calibration_notice()

    # ------------------------------------------------------------------
    # 시작 / 종료
    # ------------------------------------------------------------------
    def start(self) -> bool:
        """TCP 서버를 열고 프레임 처리를 시작한다.

        반환값: 통신이 꺼져 있거나(설정) 서버를 정상적으로 열었으면 True,
        열어야 하는데 포트를 못 열었으면(이미 사용 중 등) False.
        """
        self.protocol_server.start()
        server = self.protocol_server
        if not server.enabled:
            logger.info("엔진 시작 - 통신 꺼짐 (config/settings.json의 protocol.enabled)")
        elif server.is_listening():
            logger.info("엔진 시작 - 통신 대기 중 (TCP 포트 %d)", server.port)
        else:
            logger.error(
                "엔진 시작 실패 - 통신 포트 %d을(를) 열 수 없습니다. 같은 프로그램이 이미 실행 중이거나 "
                "다른 프로그램이 포트를 쓰고 있을 수 있습니다.",
                server.port,
            )
        self._stats_since = time.perf_counter()
        self._timer.start()
        if self._stats_timer is not None:
            self._stats_timer.start()
        # 화면(있다면)이 통신 상태 문구 등을 다시 그리게 알린다. 23단계 버그 수정:
        # 화면은 엔진이 시작되기 전에 만들어지므로, 이 알림이 없으면 서버가 정상적으로
        # 열렸는데도 처음 그린 "포트 9000 사용 불가" 문구가 그대로 남아 있었다.
        self.changed.emit()
        return (not server.enabled) or server.is_listening()

    def shutdown(self) -> None:
        """프로그램을 끝낼 때 타이머를 멈추고 서버·카메라를 정리한다."""
        self._timer.stop()
        if self._stats_timer is not None:
            self._stats_timer.stop()
        self.protocol_server.stop()
        self.camera.release()
        logger.info("엔진 종료")

    # ------------------------------------------------------------------
    # 프레임 처리 (프로그램의 심장박동)
    # ------------------------------------------------------------------
    def process_frame(self, now: float | None = None) -> None:
        """카메라에서 한 프레임을 읽어 검출·추적·전송까지 처리한다.

        타이머가 주기적으로 부른다. now는 테스트에서 시각을 직접 정해
        넣을 때만 쓰고, 평소에는 현재 시각을 쓴다.

        어떤 일이 있어도 예외를 밖으로 던지지 않는다:
        - 카메라가 없거나 끊기면 camera_unavailable 시그널만 보내고, 1초(camera_retry_interval)
          간격으로 다시 연결을 시도한다.
        - 처리 중 예상 못 한 오류가 나면 로그에 남기고 이 프레임만 건너뛴다. 엔진이 멈추면
          메인 컨트롤이 응답을 못 받게 되므로 한 프레임의 오류로 전체가 죽으면 안 된다.
        """
        if now is None:
            now = time.time()
        self._clock_hint = now
        processed = False
        try:
            processed = self._process_frame(now)
        except Exception:
            self.error_count += 1
            # 같은 오류가 프레임마다 반복돼도 로그가 넘치지 않게 일정 간격으로만 남긴다.
            if now - self._last_error_log >= ERROR_LOG_INTERVAL_S:
                self._last_error_log = now
                logger.exception("프레임 처리 중 오류 (누적 %d번) - 엔진은 계속 동작합니다", self.error_count)

        if processed:
            # 처리 시간은 '프레임을 받은 뒤부터' 잰다. 카메라를 열거나 다음 프레임을 기다리는
            # 시간은 엔진이 어쩔 수 없는 시간이라 섞으면 엔진이 느려졌는지 알 수 없다.
            elapsed = time.perf_counter() - self._proc_started
            self._stats_frames += 1
            self._stats_proc_total += elapsed
            self._stats_proc_max = max(self._stats_proc_max, elapsed)

    def _process_frame(self, now: float) -> bool:
        """프레임 한 장 처리 본체. 프레임을 실제로 처리했으면 True, 카메라에서 못 읽었으면 False."""
        raw = self._read_frame(now)
        if raw is None:
            self._on_no_frame(now)
            return False

        self._proc_started = time.perf_counter()  # 여기부터가 엔진의 처리 시간 (log_stats용)
        # 전처리: 회전 -> 축소 -> 왜곡 보정. 이후 모든 좌표는 이 프레임 기준이다.
        frame = self.preprocessor.process(raw)
        self._stats_pre_total += time.perf_counter() - self._proc_started
        self._check_processing_changed((frame.shape[1], frame.shape[0]))

        # (이름은 예전 그대로 last_raw_frame: '그림을 그리기 전' 프레임이라는 뜻. 전처리는 끝난 상태)
        self.last_raw_frame = frame
        self._set_camera_ok(True)
        self.frame_count += 1

        if self.detection_paused:
            # HSV 튜닝 중에는 검출·추적을 하지 않는다 (화면이 마스크 미리보기를 그린다).
            self.last_detection = None
            self.last_detection_outside_mat = False
            self.frame_processed.emit(FrameResult(frame, None, None, False, now, paused=True))
            return True

        # 공 검출 (못 찾아도 예외 없이 None만 돌아온다)
        # 24단계: 상태에 맞는 검색 영역(시작점·예상 위치 주변) 안에서만 찾는다.
        plan = self._plan_search(now)
        self.detector.debug_enabled = self.detection_debug
        detect_started = time.perf_counter()
        raw_detection = self.detector.detect(
            frame,
            roi=plan.roi,
            prefer=plan.center,
            expected_area=self._expected_ball_area if self.calibration.is_calibrated() else None,
            static_filter=self._static_filter if self.static_objects.objects else None,
        )
        self._stats_size_rejected += self.detector.last_size_rejected
        self._stats_detect_total += time.perf_counter() - detect_started
        self._stats_detect_frames += 1
        self._stats_roi_ratio_total += _roi_ratio(plan.roi, self.frame_size)
        if raw_detection is not None and not self.search.accept(plan, raw_detection.x, raw_detection.y):
            # 공을 놓쳐서 넓힌 영역에서 찾았는데, 공이 가던 방향·거리와 맞지 않는 후보는 버린다
            # (엉뚱한 물체를 공으로 잡지 않기 위해서)
            self.rejected_candidates += 1
            logger.debug("검색 후보 버림 (%d, %d): 예상 진행 방향·거리와 맞지 않음", raw_detection.x, raw_detection.y)
            raw_detection = None
        detection = raw_detection
        outside_mat = False
        if (
            raw_detection is not None
            and self.calibration.filter_outside_mat
            and not self.calibration.is_inside(raw_detection.x, raw_detection.y)
        ):
            # 매트 영역 밖에서 검출된 건 오검출로 보고 추적/기록에서는 무시한다
            # (검출 안 된 것과 동일하게 처리). 다만 화면에는 "찾았지만 무시했다"는
            # 걸 알 수 있게 raw_detection과 outside_mat을 함께 넘긴다 - 그냥
            # 안 보이는 것과 구분이 안 되면 왜 궤적이 안 그려지는지 알기 어렵다.
            outside_mat = True
            detection = None
        self.last_detection = detection
        self.last_detection_outside_mat = outside_mat
        # 검색 계획에 이번 결과(공 위치 또는 못 찾음)를 알려 준다 -> 다음 프레임의 예상 위치·속도
        self.search.observe((detection.x, detection.y) if detection is not None else None, now)

        was_armed = self.tracker.state == TrackerState.ARMED
        finish_reason = None
        if detection is None and self.tracker.lost_hole is not None:
            # 26단계(25단계 보완): 홀인 확인 중. 공이 컵에 들어간 게 아니라 컵 테두리에 붙어 멈췄으면 정지로 끝낸다
            finish_reason = self._check_ball_at_rim(frame, now)
        else:
            self._rim_stuck_frames = 0
        if finish_reason is not None:
            pass
        elif detection is not None:
            # TRACKING 상태일 때만 실제로 기록된다 (Tracker가 알아서 상태를 확인한다).
            if self.tracker.add_trajectory_point(detection.x, detection.y, now):
                self.trajectory_updated.emit()
            finish_reason = self.tracker.update_detection(detection.x, detection.y, now)
        else:
            finish_reason = self.tracker.update_detection(None, None, now)

        if finish_reason is not None and self._check_readdress(finish_reason):
            # 26단계: 퍼팅이 아니라 '자리 고쳐 놓기'였다 -> 결과(RESULT)를 보내지 않고 다시 준비했다
            finish_reason = None

        if was_armed and self.tracker.state == TrackerState.TRACKING:
            # 이번 프레임에서 공이 움직여 추적이 시작됐다 (ARMED -> TRACKING).
            # 앱에는 STATUS(TRACKING)보다 먼저 TRACK START 이벤트를 보낸다.
            logger.info("추적 시작 (시작 위치에서 공이 움직임)")
            if self.static_collector.active:
                # 공이 움직이는 중에는 '가만히 있는 물체'를 제대로 모을 수 없다 -> 수집 취소 (이전 목록 유지)
                self._cancel_static_scan("추적 시작")
            self.protocol_handler.on_tracking_started(self.tracker.tracking_started_at)
            self.tracking_started.emit()
            self.trajectory_updated.emit()

        # 앱에 상태 변화·실시간 좌표를 보낸다 (종료 시에는 STATUS(FINISHED) 다음에 RESULT가 간다).
        self.protocol_handler.on_frame(detection, now)

        if finish_reason is not None:
            self._finish_tracking(finish_reason)

        # 26단계: 자동 흐름 (공이 놓이면 준비, 공을 집어 가면 대기 해제 등)
        self._auto_step(detection, now)

        # 25단계: 고정 물체 수집 중이면 화면 전체 덩어리를 모은다 (수집 시간 동안만)
        self._feed_static_scan(frame, now)

        candidates = list(self.detector.last_candidates) if self.detection_debug else None
        self.frame_processed.emit(
            FrameResult(
                frame, detection, raw_detection, outside_mat, now, candidates=candidates, search_plan=plan
            )
        )
        return True

    def _plan_search(self, now: float) -> SearchPlan:
        """지금 상태(시작점, 어드레스, 추적 중 예상 위치)에 맞는 검색 영역을 정한다 (src/search.py)."""
        addr_px = None
        addr_mm = self.protocol_handler.addr_mm
        if addr_mm is not None:
            addr_px = self.calibration.real_to_pixel(*addr_mm)

        def to_px_radius(center, radius_mm, default_px):
            # 매트 위 mm 반경 -> 그 위치 근처의 픽셀 반경. 캘리브레이션이 없으면 픽셀 기본값.
            radius = self.calibration.mm_radius_to_px(center, radius_mm)
            return radius if radius is not None else default_px

        plan = self.search.plan(
            self.tracker.state,
            self.tracker.start_position,
            addr_px,
            self.frame_size,
            now,
            to_px_radius,
        )

        # 26단계 자동 흐름: 공이 놓이기를 기다릴 때(시작점 없는 READY, 결과가 나온 FINISHED)는
        # 시작 구역 원(반경 + 여유)만 찾는다. anywhere 모드면 매트 영역 전체(아래)에서 찾는다.
        state = self.tracker.state
        waiting = (state == TrackerState.READY and self.tracker.start_position is None) or (
            state == TrackerState.FINISHED
        )
        zone_mm = self.auto_zone_mm()
        if self._auto_active() and waiting and zone_mm is not None and not self.auto.anywhere:
            center = self.calibration.real_to_pixel(*zone_mm)
            if center is not None:
                radius_mm = self.auto.zone_radius_mm + ZONE_SEARCH_MARGIN_MM
                radius = to_px_radius(center, radius_mm, 150)
                roi = _square_roi(center, radius, self.frame_size)
                plan = SearchPlan(roi if roi is not None else EMPTY_ROI, center, radius, "zone")

        # 26단계(0절): '화면 전체' 검색이라도 캘리브레이션이 있으면 매트 영역만 찾는다.
        # 매트 밖은 어차피 매트 밖 필터에서 버려지는데, 색 범위를 넓히면 바닥·벽의 잡티 덩어리가 많아져서
        # 화면 전체를 처리하는 시간이 꽤 길었다 (FPS 30~42).
        # (검색 영역 제한을 끄면(search.enabled = false) 예전처럼 화면 전체를 찾는다 - 비교·문제 해결용)
        if plan.roi is None and self.search.enabled:
            mat_roi = self._mat_roi()
            if mat_roi is not None:
                plan.roi = mat_roi
                if plan.reason == "full":
                    plan.reason = "mat"
        return plan

    def _mat_roi(self):
        """매트 모서리 4점을 감싸는 사각형 + 여유 (x, y, 폭, 높이). 매트 밖 필터를 끄거나 캘리브레이션 전이면 None."""
        if (
            not self.calibration.is_calibrated()
            or not self.calibration.filter_outside_mat
            or len(self.calibration.corners) != 4
            or self.frame_size is None
        ):
            return None
        xs = [c[0] for c in self.calibration.corners]
        ys = [c[1] for c in self.calibration.corners]
        width, height = self.frame_size
        x0 = max(0, min(xs) - MAT_ROI_MARGIN_PX)
        y0 = max(0, min(ys) - MAT_ROI_MARGIN_PX)
        x1 = min(width, max(xs) + MAT_ROI_MARGIN_PX)
        y1 = min(height, max(ys) + MAT_ROI_MARGIN_PX)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return None
        return (int(x0), int(y0), int(x1 - x0), int(y1 - y0))

    # ------------------------------------------------------------------
    # 처리 조건(프레임 크기·왜곡 보정)과 캘리브레이션
    # ------------------------------------------------------------------
    def _check_processing_changed(self, frame_size: tuple[int, int]) -> None:
        """처리 프레임 크기나 k1이 지난 검사 때와 달라졌으면 캘리브레이션을 다시 검사한다.

        매 프레임 불리지만 값 두 개를 비교할 뿐이라 가볍다.
        - 첫 프레임: 지금 쓰고 있는 캘리브레이션이 이 크기에서 찍은 것인지 확인한다.
        - 그 뒤에 바뀜(설정창의 k1 슬라이더 등): 설정 파일에서 다시 불러와서 확인한다.
          값을 원래대로 되돌리면 저장돼 있던 캘리브레이션이 다시 살아난다.
        """
        self.frame_size = frame_size
        key = (frame_size, self.preprocessor.undistort_k1)
        if key == self._checked_processing:
            return
        first_check = self._checked_processing is None
        self._checked_processing = key
        if first_check:
            self._validate_calibration()
            self._validate_static_objects()
            if not self.static_objects.objects and not self._static_checked_once:
                # 저장된 고정 물체가 없거나 무효면, 캘리브레이션이 있을 때 한 번 자동으로 모은다
                self._static_checked_once = True
                if self.calibration.is_calibrated():
                    self.start_static_scan(SCAN_AUTO, now=self._clock_hint)
            return
        self._validate_static_objects()

        if not self._k1_previewing:
            # 슬라이더를 움직이는 동안에는 값마다 로그가 쌓이지 않게 남기지 않는다
            # (슬라이더를 놓거나 저장할 때 end_k1_preview()가 한 번만 남긴다).
            self._log_processing()
        self._reload_calibration_for_processing()

    def _log_processing(self) -> None:
        size = self.frame_size
        size_text = f"{size[0]}x{size[1]}" if size else "첫 프레임 전"
        logger.info(
            "처리 조건 바뀜 - 처리 프레임 %s, 왜곡 보정 k1=%.2f", size_text, self.preprocessor.undistort_k1
        )

    def _reload_calibration_for_processing(self) -> None:
        """설정 파일의 캘리브레이션을 다시 불러와 지금 처리 조건과 맞는지 검사한다."""
        fresh = Calibration()
        if fresh.is_calibrated() and fresh.matches_processing(
            self.frame_size, self.preprocessor.undistort_k1
        ):
            # 저장된 캘리브레이션이 지금 조건과 맞다 (예: k1을 원래 값으로 되돌림) -> 다시 쓴다
            was_stale = self.calibration_warning is not None
            self.calibration = fresh
            self.calibration_warning = None
            if was_stale:
                logger.info("저장된 캘리브레이션이 지금 처리 조건과 맞아 다시 사용합니다")
                self.changed.emit()
            return
        if not self.calibration.is_calibrated():
            # 이미 미보정(무효) 상태면 다시 알릴 필요가 없다 (슬라이더를 끄는 동안 알림이 반복되지 않게)
            return
        self._validate_calibration()

    def _validate_calibration(self) -> None:
        """지금 캘리브레이션이 현재 처리 조건(프레임 크기·k1)에서 찍은 것이 아니면 무효로 만든다."""
        if not self.calibration.is_calibrated():
            return
        k1 = self.preprocessor.undistort_k1
        if self.calibration.matches_processing(self.frame_size, k1):
            return

        saved_size = self.calibration.frame_size
        saved_text = f"{saved_size[0]}x{saved_size[1]}" if saved_size else "기록 없음"
        saved_k1 = self.calibration.undistort_k1
        saved_k1_text = f"{saved_k1:.2f}" if saved_k1 is not None else "기록 없음"
        now_text = f"{self.frame_size[0]}x{self.frame_size[1]}" if self.frame_size else "첫 프레임 전"
        logger.warning(
            "%s (저장: %s, k1=%s / 현재: %s, k1=%.2f) - 캘리브레이션 안 된 상태로 취급합니다",
            CALIBRATION_STALE_MESSAGE,
            saved_text,
            saved_k1_text,
            now_text,
            k1,
        )
        self.calibration.invalidate()
        self.calibration_warning = CALIBRATION_STALE_MESSAGE
        self.calibration_invalidated.emit(CALIBRATION_STALE_MESSAGE)
        self.changed.emit()

    def _save_calibration(self) -> None:
        """캘리브레이션을 지금 처리 조건(프레임 크기·k1)과 함께 저장한다.

        프레임 크기를 아직 모르면(첫 프레임 전) 저장돼 있던 크기를 그대로 둔다.
        """
        frame_size = self.frame_size if self.frame_size is not None else self.calibration.frame_size
        self.calibration.stamp(frame_size, self.preprocessor.undistort_k1)
        self.calibration.save()
        self.calibration_warning = None
        self._update_calibration_notice()
        # 25단계: 캘리브레이션 직후 고정 물체(컵 등)를 자동으로 모은다. 매트(컵 위치의 mm 좌표)가
        # 바뀌었을 수 있기 때문이다. 공이 매트 위에 있을 수 있으니 공 크기 덩어리는 기억하지 않는다.
        self.start_static_scan(SCAN_AUTO, now=self._clock_hint)

    def _update_calibration_notice(self) -> None:
        """찍은 4점 모양과 매트 크기의 비율이 크게 다르면 calibration_notice에 경고를 남긴다 (저장은 막지 않음)."""
        notice = self.calibration.aspect_warning() if self.calibration.is_calibrated() else None
        if notice and notice != self.calibration_notice:
            logger.warning(notice)
        self.calibration_notice = notice

    def set_undistort_k1(self, k1: float, preview: bool = False) -> None:
        """렌즈 왜곡 보정 값(k1)을 바로 바꾼다 (설정창 슬라이더의 실시간 미리보기용).

        settings.json에는 저장하지 않는다 (설정창의 '저장' 버튼이 따로 저장한다).
        다음 프레임부터 바뀐 값으로 처리되고, 캘리브레이션 검사도 그때 다시 한다.
        preview=True(슬라이더를 움직이는 중)이면 '처리 조건 바뀜' 로그를 미뤄 둔다.
        """
        if preview:
            self._k1_previewing = True
        self.preprocessor.set_undistort_k1(k1)

    def end_k1_preview(self) -> None:
        """슬라이더를 놓았거나 저장·취소했을 때: 미뤄 둔 '처리 조건 바뀜' 로그를 한 번만 남긴다."""
        if self._k1_previewing:
            self._k1_previewing = False
            self._log_processing()

    def set_detection_debug(self, enabled: bool) -> None:
        """검출 디버그 표시(덩어리 면적·지름)를 켜거나 끈다."""
        self.detection_debug = bool(enabled)
        if not enabled:
            self.detector.last_candidates = []

    def _finish_tracking(self, reason: str) -> None:
        """정지·이탈·홀인으로 추적이 끝났을 때: 결과를 앱에 보내고 화면에 알린다."""
        if reason == HOLE_REASON:
            last = self.tracker._last_raw_position
            last_mm = self.calibration.pixel_to_real(*last) if last is not None else None
            logger.info(
                "홀인 판정 - %s번 컵, 공이 사라진 위치 %s",
                self.tracker.finish_hole,
                f"({last_mm[0]:.0f}, {last_mm[1]:.0f})mm" if last_mm else f"{last}px",
            )
        logger.info("추적 종료 (%s) - 기록된 점 %d개", reason, len(self.tracker.trajectory))
        self.protocol_handler.on_finished(reason)
        if self._auto_active():
            # 26단계: 다음 퍼팅은 공을 집었다가(또는 움직였다가) 시작 구역에 다시 놓아야 준비된다
            stop = self.tracker.stop_position
            stop_mm = self.calibration.pixel_to_real(*stop) if stop is not None else None
            if stop_mm is not None and self._ball_in_auto_zone(stop_mm):
                self.auto.hold_until_released(stop_mm)
            else:
                # 시작 구역 밖에서 끝남: 시작 구역 검색에는 그 공이 안 보이므로 바로 다음 공을 받을 수 있다
                self.auto.hold_until_released(None)
            if self.auto.save_results:
                status, result_id = self.save_result()
                if status == SAVE_OK:
                    logger.info("자동 흐름: 결과 자동 저장 (id %s)", result_id)
                else:
                    logger.info("자동 흐름: 결과를 저장하지 못함 (%s)", status)
        self.tracking_finished.emit(reason)

    def _read_frame(self, now: float):
        """카메라에서 프레임을 읽는다. 못 읽으면 None.

        카메라가 안 열려 있으면 다시 연결을 시도하는데, 장치가 없을 때 열기를 시도하는 것은
        오래 걸리는 동작이라 camera_retry_interval(기본 1초)마다 한 번만 시도한다
        (예전에는 8ms마다 시도해서 CPU를 낭비했다).
        """
        if not self.camera.is_opened():
            if now - self._last_open_attempt < self.camera_retry_interval:
                return None  # 아직 다시 시도할 때가 아니다
            self._last_open_attempt = now
            if not self.camera.open():
                logger.debug("카메라를 열지 못함")
                self.camera_unavailable.emit()
                return None

        frame = self.camera.read_frame()
        if frame is None:
            # 읽기에 실패했다면 연결이 끊긴 것으로 보고 장치를 반납한 뒤 재연결을 기다린다.
            self.camera.release()
            self._last_open_attempt = now
            self.camera_unavailable.emit()
            return None
        return frame

    def _on_no_frame(self, now: float) -> None:
        """카메라 프레임을 못 얻었을 때: 검출 상태를 비우고 알린다."""
        # 오래된 프레임으로 캘리브레이션·시작점 등록을 하지 않도록 함께 비운다.
        self.last_raw_frame = None
        self.last_detection = None
        self.last_detection_outside_mat = False
        self._set_camera_ok(False)

        # 추적 중에 카메라가 끊겨도 '공이 안 보이는 시간'은 계속 센다. 그래야 끊김이 길어지면
        # lost_duration 뒤에 "화면 이탈"로 끝나서 결과(RESULT)가 나간다 (안 그러면 영원히
        # TRACKING에 머물러서 컨트롤이 결과를 못 받는다).
        if self.tracker.state == TrackerState.TRACKING:
            finish_reason = self.tracker.update_detection(None, None, now)
            if finish_reason is not None:
                self.protocol_handler.on_frame(None, now)  # STATUS(FINISHED)가 RESULT보다 먼저 간다
                self._finish_tracking(finish_reason)

    def _set_camera_ok(self, ok: bool) -> None:
        """카메라 수신 상태를 갱신하고, 바뀌었을 때만 로그·앱 이벤트(CAMERA)·시그널로 알린다."""
        self.camera_ok = ok
        if self._camera_reported == ok:
            return
        self._camera_reported = ok
        if ok:
            logger.info("카메라 영상 수신 시작")
        else:
            logger.warning(
                "카메라 영상을 받을 수 없음 - %.0f초 간격으로 다시 연결을 시도합니다",
                self.camera_retry_interval,
            )
        self.protocol_handler.on_camera_changed(ok)
        self.camera_status_changed.emit(ok)

    def log_stats(self) -> dict:
        """지난 구간의 FPS·프레임 처리 시간 요약을 로그에 남긴다 (stats_interval_s마다 자동 호출).

        엔진이 느려지는지(처리 시간이 프레임 간격보다 길어지는지) 현장에서 확인하는 데
        쓴다. 처리 시간에는 카메라를 열거나 다음 프레임을 기다리는 시간은 포함하지 않는다.
        요약 내용을 dict로도 돌려준다.
        """
        elapsed = time.perf_counter() - self._stats_since
        frames = self._stats_frames
        summary = {
            "frames": frames,
            "fps": frames / elapsed if elapsed > 0 else 0.0,
            "avg_ms": (self._stats_proc_total / frames * 1000) if frames else 0.0,
            "max_ms": self._stats_proc_max * 1000,
            # 처리 시간 중 전처리(축소·왜곡 보정)가 차지한 평균 시간
            "pre_avg_ms": (self._stats_pre_total / frames * 1000) if frames else 0.0,
            # 24단계: 공 검출 시간과 검색 영역 크기 (화면 대비 %, 전체 검색이면 100%)
            "detect_avg_ms": (
                self._stats_detect_total / self._stats_detect_frames * 1000 if self._stats_detect_frames else 0.0
            ),
            "roi_avg_pct": (
                self._stats_roi_ratio_total / self._stats_detect_frames * 100 if self._stats_detect_frames else 0.0
            ),
            "errors": self.error_count,
            # 25단계: 크기 판정(mm 기준 공 크기 등)으로 걸러진 후보 수 (컵 등)
            "size_rejected": self._stats_size_rejected,
        }
        frame_text = f"{self.frame_size[0]}x{self.frame_size[1]}" if self.frame_size else "-"
        logger.info(
            "상태 요약 - FPS %.1f, 처리 시간(프레임을 받은 뒤 전처리~전송) 평균 %.1fms 최대 %.1fms "
            "(그중 전처리 평균 %.1fms, 공 검출 평균 %.1fms, 검색 영역 평균 화면의 %.0f%%%s), "
            "처리 프레임 %s (scale %.2f, k1 %.2f), "
            "카메라 %s, 접속 %d대, 상태 %s, 오류 %d번, 크기로 걸러진 후보 %d개, 고정 물체 %d개",
            summary["fps"],
            summary["avg_ms"],
            summary["max_ms"],
            summary["pre_avg_ms"],
            summary["detect_avg_ms"],
            summary["roi_avg_pct"],
            "" if self.search.enabled else " - 검색 영역 제한 꺼짐",
            frame_text,
            self.preprocessor.process_scale,
            self.preprocessor.undistort_k1,
            "OK" if self.camera_ok else "LOST",
            self.protocol_server.client_count(),
            self.tracker.state.name,
            self.error_count,
            self._stats_size_rejected,
            len(self.static_objects.objects),
        )
        self._stats_since = time.perf_counter()
        self._stats_size_rejected = 0
        self._stats_frames = 0
        self._stats_proc_total = 0.0
        self._stats_proc_max = 0.0
        self._stats_pre_total = 0.0
        self._stats_detect_total = 0.0
        self._stats_roi_ratio_total = 0.0
        self._stats_detect_frames = 0
        return summary

    # ------------------------------------------------------------------
    # 화면 버튼과 앱 명령이 함께 쓰는 동작
    # ------------------------------------------------------------------
    def start_calibration(self) -> str:
        """'캘리브레이션'(또는 '다시 지정') 버튼: 모서리를 모두 지우고 CALIBRATING으로 들어간다.

        23단계부터 흐름이 바뀌었다. 예전에는 녹색 매트 자동 검출이 먼저 돌았는데,
        매트 모양에 따라 틀린 4점으로 '성공'해 버려서(4점이 이미 차 있으니) 클릭이
        모두 무시되는 문제가 있었다. 그래서:
        - calibration.auto_detect가 False(기본): 자동 검출 없이 바로 수동 4점 클릭 모드
          -> CALIBRATION_MANUAL
        - True: 자동 검출 결과를 저장하지 않고 '제안'(calibration_proposal)으로만 둔다.
          화면에서 '이 모서리로 확정'(accept_calibration_proposal)을 누르거나, 그냥
          직접 찍기 시작하면 된다 -> CALIBRATION_PROPOSED
          검출에 실패하면 수동 모드 -> CALIBRATION_AUTO_FAILED
        다시 눌러도(이미 CALIBRATING이어도) 항상 새로 시작한다.
        """
        self.calibration.reset_corners()
        self.calibration_proposal = None
        self.corner_problem = None
        self.calibration_notice = None
        self.tracker.calibrate()
        if not self.calibration.auto_detect:
            return CALIBRATION_MANUAL
        corners = None
        if self.last_raw_frame is not None:
            corners = detect_mat_corners(
                self.last_raw_frame, self.calibration.mat_hsv_lower, self.calibration.mat_hsv_upper
            )
        if corners is None:
            return CALIBRATION_AUTO_FAILED
        self.calibration_proposal = [tuple(c) for c in corners]
        return CALIBRATION_PROPOSED

    def accept_calibration_proposal(self) -> bool:
        """'이 모서리로 확정': 자동 검출 제안을 실제 캘리브레이션으로 적용하고 저장한다."""
        if self.tracker.state != TrackerState.CALIBRATING or self.calibration_proposal is None:
            return False
        proposal = [list(c) for c in self.calibration_proposal]
        self.calibration_proposal = None  # 확정하든 거절되든 제안은 여기서 끝난다
        problem = check_corners(proposal, self.frame_size)
        if problem is not None:
            # 제안이 화면 밖으로 나가는 등 쓸 수 없는 모양이면 저장하지 않고 수동 모드로 남는다.
            self.corner_problem = problem
            return False
        self.calibration.set_corners(proposal)
        self._save_calibration()
        return True

    def reject_calibration_proposal(self) -> None:
        """'직접 지정': 자동 검출 제안을 버리고 수동 4점 클릭 모드로 간다."""
        self.calibration_proposal = None

    def add_calibration_corner(self, x: int, y: int) -> bool:
        """화면에서 클릭한 매트 모서리를 등록한다 (CALIBRATING 상태일 때만). 점이 등록되면 True.

        자동 검출 제안이 떠 있는 상태에서 클릭하면 '직접 지정'으로 보고 제안을 버린다.
        4번째 점을 찍으면 check_corners()로 검사한다 (볼록한 사각형인지, 좌상→우상→우하→좌하
        순서인지, 화면 안인지).
        - 문제가 없으면 원근 변환 행렬을 만들어 config/settings.json에 저장한다.
        - 문제가 있으면 저장하지 않고 4점을 모두 지운 뒤 False를 돌려준다. 이유 코드는
          corner_problem에 남는다 (화면이 이유를 보여주고 다시 찍게 한다).
        """
        if self.tracker.state != TrackerState.CALIBRATING:
            return False
        self.calibration_proposal = None
        if not self.calibration.add_corner(x, y):
            return False  # 이미 4점이 다 찍혀 있음 (옮기려면 드래그)
        self.corner_problem = None
        if len(self.calibration.corners) == 4:
            problem = check_corners([list(c) for c in self.calibration.corners], self.frame_size)
            if problem is not None:
                logger.info("찍은 모서리 4점이 올바르지 않아 저장하지 않음 (%s) - 다시 찍어야 합니다", problem)
                self.corner_problem = problem
                self.calibration.reset_corners()
                return False
            self._save_calibration()
        return True

    def undo_last_corner(self) -> bool:
        """화면 오른쪽 클릭: 마지막으로 찍은 점을 취소한다 (CALIBRATING 상태일 때만)."""
        if self.tracker.state != TrackerState.CALIBRATING:
            return False
        self.corner_problem = None
        self.calibration_notice = None
        return self.calibration.undo_last_corner()

    def move_calibration_corner(self, index: int, x: int, y: int) -> str | None:
        """4점을 다 찍은 뒤 점 하나를 드래그해서 옮긴다 (미세 조정).

        옮긴 모양도 check_corners()로 검사해서, 괜찮으면 행렬을 다시 계산하고 저장한다.
        반환: None = 옮김 / 이유 코드 = 옮기지 않음 ("bad_state", "bad_corners",
        "corners_out_of_frame")
        """
        if self.tracker.state != TrackerState.CALIBRATING or len(self.calibration.corners) != 4:
            return "bad_state"
        if not 0 <= index < 4:
            return "bad_corners"
        corners = [list(c) for c in self.calibration.corners]
        corners[index] = [int(x), int(y)]
        problem = check_corners(corners, self.frame_size)
        if problem is not None:
            return problem
        self.calibration.set_corners(corners)
        self._save_calibration()
        return None

    # 앱의 ALIGN 명령은 매트 모서리를 얻는 방법이 세 가지다. 셋 모두 같은 규칙을 따른다:
    #   1) 상태·크기를 먼저 확인하고  2) 모서리를 얻고(여기서 실패하면 아무것도 바꾸지 않음)
    #   3) 모두 성공한 뒤에만 크기·모서리를 적용·저장하고 CALIBRATING으로 들어간다.
    # (실패했는데 크기만 바뀌거나 기존 캘리브레이션이 지워지는 일이 없도록)
    # 성공하면 "OK", 실패하면 이유 코드를 돌려준다.

    def align_auto(self, width_mm=None, height_mm=None) -> str:
        """녹색 매트를 카메라 영상에서 자동 검출해 캘리브레이션한다.

        이유 코드: "bad_state", "bad_size", "no_frame"(카메라 영상 없음), "mat_not_found"
        """
        size = self._align_precheck(width_mm, height_mm)
        if isinstance(size, str):
            return size

        if self.last_raw_frame is None:
            return "no_frame"
        corners = detect_mat_corners(
            self.last_raw_frame, self.calibration.mat_hsv_lower, self.calibration.mat_hsv_upper
        )
        if corners is None:
            return "mat_not_found"
        self._apply_alignment(size, corners)
        return "OK"

    def align_corners(self, corners, width_mm=None, height_mm=None) -> str:
        """컨트롤이 직접 알려 준 모서리 4점(픽셀 좌표)으로 캘리브레이션한다.

        자동 검출이 실패할 때(조명, 매트 일부 가림) 쓴다. 점은 좌상 -> 우상 -> 우하 -> 좌하
        순서의 카메라 화면 픽셀 좌표여야 하고, 값이 이상하면 적용하지 않는다.
        이유 코드: "bad_state", "bad_size", "bad_corners", "corners_out_of_frame"
        """
        size = self._align_precheck(width_mm, height_mm)
        if isinstance(size, str):
            return size

        frame_size = None
        if self.last_raw_frame is not None:
            frame_size = (self.last_raw_frame.shape[1], self.last_raw_frame.shape[0])
        problem = check_corners(corners, frame_size)
        if problem is not None:
            return problem
        self._apply_alignment(size, corners)
        return "OK"

    def align_saved(self, width_mm=None, height_mm=None) -> str:
        """이미 저장돼 있는 캘리브레이션을 그대로 쓴다 (카메라 영상 없이도 가능).

        카메라와 매트를 옮기지 않았다면 매번 다시 검출할 필요가 없다. 크기가 오면
        저장된 모서리로 새 크기 기준 변환 행렬을 다시 계산한다.
        이유 코드: "bad_state", "bad_size", "not_calibrated"(저장된 캘리브레이션 없음)
        """
        size = self._align_precheck(width_mm, height_mm)
        if isinstance(size, str):
            return size
        if not self.calibration.is_calibrated():
            return "not_calibrated"
        self._apply_alignment(size, None)
        return "OK"

    def _align_precheck(self, width_mm, height_mm):
        """ALIGN 공통 확인: 지금 상태에서 가능한지, 크기가 올바른지.

        통과하면 적용할 (가로, 세로) mm를, 실패하면 이유 코드(문자열)를 돌려준다.
        크기가 안 오면 현재 설정값을 그대로 쓴다.
        """
        if not self.tracker.can("calibrate"):
            return "bad_state"
        width = self.calibration.mat_width_mm if width_mm is None else width_mm
        height = self.calibration.mat_height_mm if height_mm is None else height_mm
        if not (_is_positive_number(width) and _is_positive_number(height)):
            return "bad_size"
        return (width, height)

    def _apply_alignment(self, size: tuple, corners) -> None:
        """검사를 모두 통과한 뒤 크기·모서리를 적용하고 저장한다 (corners가 None이면 기존 모서리 유지)."""
        self.calibration.set_mat_size(*size)
        if corners is not None:
            self.calibration.set_corners(corners)
        self.calibration_proposal = None
        self.corner_problem = None
        self._save_calibration()
        self.tracker.calibrate()

    def register_start(self) -> str:
        """'시작 위치 등록': 지금 검출된 공을 시작점으로 등록한다.

        결과 코드(START_*)를 돌려준다. 공이 검출되지 않았으면 등록하지 않고
        (상태 전환도 없이) START_NO_BALL을, 공은 보이지만 매트 밖이라 무시된
        경우는 START_OUTSIDE_MAT을 돌려준다.
        """
        if self.last_detection is None:
            return START_OUTSIDE_MAT if self.last_detection_outside_mat else START_NO_BALL
        if not self.tracker.register_start_position(self.last_detection.x, self.last_detection.y):
            return START_BAD_STATE
        # 25단계: 시작점에 놓인 공을 뺀 나머지 '가만히 있는 공 색 물체'를 고정 물체로 모은다
        self.start_static_scan(SCAN_AUTO, now=self._clock_hint)
        # 26단계: 처음 한 번 등록한 시작점을 '시작 구역'으로 저장한다 (다음부터는 버튼 없이 이 구역을 쓴다)
        if self.auto.start_zone is None:
            start_mm = self.calibration.pixel_to_real(*self.tracker.start_position)
            if start_mm is not None:
                self.set_start_zone(start_mm)
        return START_OK

    def start_tracking(self) -> bool:
        """'추적 준비' / 앱의 ADD 명령: 추적 대기(ARMED)로 들어간다.

        공이 시작점에서 움직이는 순간 process_frame이 자동으로 추적(TRACKING)을
        시작한다. 허용되는 상태(시작점 등록 후)가 아니면 False.
        """
        return self.tracker.start_tracking()

    def abort(self) -> bool:
        """'추적 중지' / 앱의 ABORT: 추적 대기(ARMED)나 추적(TRACKING)을 멈추고 READY로 돌아간다."""
        if not self.tracker.stop_tracking():
            return False
        self._hold_auto_after_button()
        return True

    def reset(self) -> bool:
        """'궤적 초기화' / 앱의 RESET: 시작점과 궤적을 지우고 READY로 돌아간다 (캘리브레이션은 유지)."""
        if not self.tracker.reset_trajectory():
            return False
        self._hold_auto_after_button()
        return True

    def _hold_auto_after_button(self) -> None:
        """버튼(또는 앱 명령)으로 멈췄을 때: 공을 집었다가 다시 놓기 전에는 자동으로 다시 준비하지 않는다.

        '버튼 동작이 우선'이라서다. 이게 없으면 '추적 중지'를 누르자마자 공이 그대로 있으니
        1초 뒤에 자동 흐름이 다시 추적 대기로 만들어 버린다.
        """
        if not self.auto.enabled:
            return
        ball = self.last_detection
        ball_mm = self.calibration.pixel_to_real(ball.x, ball.y) if ball is not None else None
        self.auto.hold_until_released(ball_mm)

    # ------------------------------------------------------------------
    # 좌표 변환 · 통계 · 저장
    # ------------------------------------------------------------------
    def trajectory_to_real(self) -> list[TrajectoryPoint]:
        """현재 궤적 전체를 매트 실제 좌표(mm)로 변환한다.

        호출하기 전에 calibration.is_calibrated()가 True인지 확인해야 한다
        (그래야 pixel_to_real이 항상 값을 돌려준다).
        """
        result = []
        for p in self.tracker.trajectory:
            real_x, real_y = self.calibration.pixel_to_real(p.x, p.y)
            result.append(TrajectoryPoint(x=real_x, y=real_y, timestamp=p.timestamp))
        return result

    def real_start_stop(self) -> tuple[tuple | None, tuple | None]:
        """시작점·정지점을 mm 좌표로 돌려준다 (없거나 캘리브레이션 전이면 None)."""
        start = (
            self.calibration.pixel_to_real(*self.tracker.start_position)
            if self.tracker.start_position is not None
            else None
        )
        stop = (
            self.calibration.pixel_to_real(*self.tracker.stop_position)
            if self.tracker.stop_position is not None
            else None
        )
        return start, stop

    def current_stats(self):
        """현재 궤적의 요약 통계(mm 기준)를 계산한다.

        캘리브레이션 전이거나 궤적 점이 2개 미만이면 None.
        반환: (TrajectoryStats, 실제좌표 궤적, 시작점mm, 정지점mm) 또는 None
        """
        if not self.calibration.is_calibrated():
            return None
        real_trajectory = self.trajectory_to_real()
        real_start, real_stop = self.real_start_stop()
        stats = compute_trajectory_stats(real_trajectory, real_start, real_stop)
        if stats is None:
            return None
        return stats, real_trajectory, real_start, real_stop

    def save_result(self) -> tuple[str, int | None]:
        """이번 측정 결과를 SQLite DB에 기록한다.

        반환: (결과 코드 SAVE_*, 저장된 행 id 또는 None).
        캘리브레이션이 안 됐으면(실제 단위 mm를 모르면) 저장하지 않고,
        궤적이 너무 짧아(점 2개 미만) 통계를 계산할 수 없을 때도 저장하지 않는다.
        """
        if not self.calibration.is_calibrated():
            return SAVE_NOT_CALIBRATED, None
        current = self.current_stats()
        if current is None:
            return SAVE_NO_TRAJECTORY, None
        stats, real_trajectory, real_start, real_stop = current

        result_id = self.storage.save_result(
            profile_name=self.active_profile_name(),
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
            finish_reason=self.tracker.finish_reason,
            hole=self.tracker.finish_hole,
        )
        return SAVE_OK, result_id

    def profiles(self) -> tuple[str, dict]:
        """검출 프로필 목록을 돌려준다: (지금 활성화된 프로필의 키, {키: 프로필 설정}).

        설정 파일에 없는 기본 프로필(흰 공, 분홍 공, 연두 공)도 함께 들어 있다.
        """
        detection_settings = load_settings().get("detection", {})
        profiles = {
            **DEFAULT_DETECTION_SETTINGS["profiles"],
            **detection_settings.get("profiles", {}),
        }
        active_key = detection_settings.get(
            "active_profile", DEFAULT_DETECTION_SETTINGS["active_profile"]
        )
        return active_key, profiles

    def active_profile_name(self) -> str:
        """지금 활성화된 검출 프로필의 표시 이름을 돌려준다."""
        active_key, profiles = self.profiles()
        return profiles.get(active_key, {}).get("name", active_key)

    def select_profile(self, key: str) -> str:
        """앱의 PROFILE 명령: 공 검출 프로필(공 색 설정)을 바꾼다 (예: 연습용 연두 공 -> 실전용 분홍 공).

        설정 파일에 저장해서 다음 실행에도 유지하고, 검출기를 바로 새로 만든다.
        반환: "OK" / "unknown_profile"(그런 프로필 없음) / "bad_state"(추적 대기·추적 중에는
        검출 조건이 바뀌면 안 되므로 바꿀 수 없다).
        """
        if self.tracker.state in (TrackerState.ARMED, TrackerState.TRACKING):
            return "bad_state"
        _active, profiles = self.profiles()
        if key not in profiles:
            return "unknown_profile"

        settings = load_settings()
        settings.setdefault("detection", {})["active_profile"] = key
        save_settings(settings)
        self.reload_detector()
        return "OK"

    # ------------------------------------------------------------------
    # 26단계: 자동 흐름
    # ------------------------------------------------------------------
    def _auto_active(self) -> bool:
        """자동 흐름이 지금 동작하는지 (켜져 있고 캘리브레이션이 유효해야 한다: 거리 판단을 mm로 하므로)."""
        return self.auto.enabled and self.calibration.is_calibrated()

    def auto_zone_mm(self):
        """지금 쓰는 시작 구역 중심(mm): 앱이 SET_ADDR로 정한 어드레스가 있으면 그것, 없으면 저장된 구역."""
        addr = self.protocol_handler.addr_mm
        return addr if addr is not None else self.auto.start_zone

    def _ball_in_auto_zone(self, ball_mm) -> bool:
        start = self.tracker.start_position
        start_mm = self.calibration.pixel_to_real(*start) if start is not None else None
        return self.auto.in_zone(ball_mm, self.auto_zone_mm(), start_mm)

    def set_auto_enabled(self, enabled: bool) -> None:
        """자동 흐름을 켜거나 끄고 현장 파일에 저장한다 (설정창·앱의 AUTO 명령)."""
        self.auto.enabled = bool(enabled)
        self.auto.reset()
        self._save_auto_settings()
        logger.info("자동 흐름 %s", "켬" if enabled else "끔")
        self.auto_changed.emit()
        self.changed.emit()

    def set_start_zone(self, zone_mm) -> None:
        """시작 구역(mm)을 정하고 현장 파일에 저장한다. None이면 지운다."""
        self.auto.start_zone = (float(zone_mm[0]), float(zone_mm[1])) if zone_mm is not None else None
        self._save_auto_settings()
        if zone_mm is None:
            logger.info("시작 구역을 지움 - 다음 '시작 위치 등록' 때 새로 저장합니다")
        else:
            logger.info("시작 구역 저장: (%.0f, %.0f)mm, 반경 %.0fmm", zone_mm[0], zone_mm[1], self.auto.zone_radius_mm)
        self.auto_changed.emit()

    def _save_auto_settings(self) -> None:
        settings = load_settings()
        settings["auto"] = {**(settings.get("auto") or {}), **self.auto.to_settings()}
        save_settings(settings)

    def auto_guide_text(self, now: float | None = None) -> str | None:
        """화면 안내 띠에 보여줄 자동 흐름 문구 (자동 흐름이 꺼져 있으면 None)."""
        if not self.auto.enabled:
            return None
        if now is None:
            now = time.time()
        if self._auto_notice is not None:
            text, since = self._auto_notice
            if now - since < AUTO_NOTICE_S:
                return text
            self._auto_notice = None
        if not self.calibration.is_calibrated():
            return "자동 모드: 캘리브레이션을 먼저 해 주세요"
        state = self.tracker.state
        if state == TrackerState.ARMED:
            return "준비 완료 - 퍼팅하세요"
        if state == TrackerState.TRACKING:
            return "추적 중..."
        if state == TrackerState.FINISHED:
            reason = self.tracker.finish_reason or "-"
            if self.tracker.finish_hole is not None:
                reason += f" ({self.tracker.finish_hole}번 컵)"
            return f"결과: {reason} - 다음 퍼팅은 공을 시작 구역에 놓으세요"
        if state == TrackerState.READY:
            if not self.auto.can_wait_for_ball(self.auto_zone_mm()) and self.tracker.start_position is None:
                return "시작 위치 등록을 한 번 해 주세요 (자동 모드는 그 자리를 시작 구역으로 기억합니다)"
            return "공을 시작 구역에 놓으세요"
        return None

    def _auto_step(self, detection, now: float) -> None:
        """매 프레임: 자동 흐름의 판단을 받아 실제로 상태를 바꾼다."""
        if not self._auto_active():
            return
        ball_px = (detection.x, detection.y) if detection is not None else None
        ball_mm = self.calibration.pixel_to_real(*ball_px) if ball_px is not None else None
        start = self.tracker.start_position
        start_mm = self.calibration.pixel_to_real(*start) if start is not None else None
        holes_mm = [(h.x_mm, h.y_mm) for h in self.static_objects.holes() if h.x_mm is not None]
        action = self.auto.step(
            self.tracker.state,
            True,
            start is not None,
            ball_px,
            ball_mm,
            now,
            zone_mm=self.auto_zone_mm(),
            start_mm=start_mm,
            inside_mat=ball_px is not None and self.calibration.is_inside(*ball_px),
            holes_mm=holes_mm,
        )
        if action is None:
            return
        if action.kind == ACTION_READY:
            if self.tracker.enter_ready():
                logger.info("자동 흐름: 저장된 캘리브레이션이 유효해서 바로 준비 상태(READY)로 갑니다")
        elif action.kind == ACTION_ARM:
            x, y = action.position_px
            keep = action.keep_previous
            if not (self.tracker.register_start_position(x, y, keep_previous=keep) and self.tracker.start_tracking(keep_previous=keep)):
                return
            self.search.reset()
            logger.info(
                "자동 흐름: 시작점 자동 등록 (%.0f, %.0f)mm -> 추적 대기(ARMED)%s",
                action.position_mm[0],
                action.position_mm[1],
                " - 다음 퍼팅 준비" if keep else "",
            )
            self.protocol_handler.on_auto_init(action.position_mm)
        elif action.kind == ACTION_DROP:
            if self.tracker.reset_trajectory():
                logger.info(
                    "자동 흐름: 추적 대기 중 공이 %.1f초 넘게 안 보여 대기를 풀었습니다 (공을 집어 감)",
                    self.auto.ball_gone_duration,
                )
        self.auto_changed.emit()
        self.changed.emit()

    def _check_readdress(self, reason: str) -> bool:
        """방금 끝난 추적이 '자리 고쳐 놓기'면 결과를 버리고 다시 준비한다. 그랬으면 True."""
        if not self._auto_active() or self.tracker.start_position is None:
            return False
        start_mm = self.calibration.pixel_to_real(*self.tracker.start_position)
        max_distance = 0.0
        for point in self.trajectory_to_real():
            max_distance = max(max_distance, math.hypot(point.x - start_mm[0], point.y - start_mm[1]))
        if not self.auto.is_readdress(
            reason, max_distance, self.tracker.tracking_started_at, self.tracker.stop_started_at
        ):
            return False

        stop = self.tracker.stop_position
        stop_mm = self.calibration.pixel_to_real(*stop) if stop is not None else None
        logger.info(
            "자동 흐름: 자리 고쳐 놓기로 보고 퍼팅을 취소 (최대 %.0fmm 이동, 기준 %.0fmm) - 결과를 보내지 않습니다",
            max_distance,
            self.auto.min_putt_distance_mm,
        )
        self.protocol_handler.on_tracking_cancelled("READDRESS")
        self.auto.reset()
        if stop_mm is not None and self._ball_in_auto_zone(stop_mm):
            # 멈춘 자리가 시작 구역 안 -> 그 자리를 새 시작점으로 바로 다시 준비
            self.tracker.register_start_position(*stop)
            self.tracker.start_tracking()
            self._auto_notice = ("자리 고쳐 놓기로 보고 다시 준비합니다", self._clock_hint or time.time())
        else:
            self.tracker.reset_trajectory()
            self._auto_notice = ("자리 고쳐 놓기로 보고 취소했습니다 - 공을 시작 구역에 놓으세요", self._clock_hint or time.time())
        self.search.reset()
        self.auto_changed.emit()
        self.trajectory_updated.emit()
        return True

    def _check_ball_at_rim(self, frame, now: float) -> str | None:
        """홀인 확인 중: 공이 컵 테두리에 붙어 멈춘 것인지 본다. 그렇다고 판단되면 정지로 끝내고 사유를 돌려준다.

        왜 필요한가: 공과 컵 사이 간격이 약 5px보다 좁으면 블러·모폴로지 때문에 둘이 한 덩어리로 붙는다.
        그 덩어리는 공 크기가 아니라서 공이 '사라진' 것처럼 보이고, 0.5초 뒤 홀인으로 잘못 판정됐다.

        판단: 컵 자리의 덩어리를 찾아서 다음 두 가지를 모두 만족하면 '공이 붙어 있다'고 본다.
          1) 면적 >= 기억한 컵 면적 + 공 예상 면적 x stuck_ball_area_fraction(0.5)
             (컵 크기와 상관없이 '공 반 개 이상만큼 커졌는지'를 본다)
          2) 덩어리의 가장 먼 점이 컵 중심에서 (컵 반경 + 공 반경 x 0.5) 넘게 떨어져 있음
             (공이 컵 '안'으로 떨어져 컵 안이 하얗게 보이는 경우는 컵 밖으로 튀어나오지 않으므로 홀인으로 남는다)
        판정 시점: 홀인 확인 시간(0.5초)이 끝나는 순간, 바로 앞 프레임들이 '연속으로'
        RIM_STUCK_MIN_FRAMES(3)번 이상 그렇게 보였으면 정지로 끝낸다. 중간에 판정하지 않는 이유:
        공이 컵에 떨어지는 순간에도 몇 프레임은 컵 밖으로 튀어나온 덩어리처럼 보일 수 있어서다.
        (그런 경우 공이 컵 안으로 사라지면 연속 횟수가 0으로 돌아가 홀인으로 끝난다)

        끝 위치: 덩어리에서 컵 중심으로부터 가장 먼 점 = 공의 바깥쪽 끝이다. 그 방향으로 (그 거리 - 공 반경)만큼
        간 곳을 공 중심으로 본다 ('덩어리에서 컵 쪽을 뺀 바깥쪽 끝'). 계산이 안 되면 마지막으로 본 공 위치.
        """
        lost = self.tracker.lost_hole
        hole_number = lost[0]
        hole = next((h for h in self.static_objects.holes() if h.hole == hole_number), None)
        ball_area = self._expected_ball_area(hole.x, hole.y) if hole is not None else None
        if hole is None or not ball_area:
            return None
        ball_r = math.sqrt(ball_area / math.pi)
        blob = self.detector.blob_around(frame, (hole.x, hole.y), hole.radius + ball_r * 3)
        if blob is None:
            self._rim_stuck_frames = 0
            return None
        area, points = blob
        offsets = points.astype(float) - (hole.x, hole.y)
        distances = np.hypot(offsets[:, 0], offsets[:, 1])
        far_index = int(distances.argmax())
        far = float(distances[far_index])
        grown = area >= hole.area + ball_area * float(self.hole_settings["stuck_ball_area_fraction"])
        sticks_out = far >= hole.radius + ball_r * 0.5
        if not (grown and sticks_out):
            self._rim_stuck_frames = 0  # 연속으로 보여야 한다
            return None
        self._rim_stuck_frames += 1
        ux, uy = offsets[far_index] / far
        center_distance = max(0.0, far - ball_r)
        self._rim_ball_position = (hole.x + ux * center_distance, hole.y + uy * center_distance)
        if self._rim_stuck_frames < RIM_STUCK_MIN_FRAMES or not self.tracker.hole_confirm_due(now):
            return None
        position = self._rim_ball_position or self.tracker.last_seen_position
        self._rim_stuck_frames = 0
        logger.info(
            "컵 테두리에 붙어 멈춘 공 - %s번 컵 자리 덩어리 %.0fpx² (컵 %.0f + 공 %.0f), 홀인이 아니라 정지로 끝냅니다",
            hole_number,
            area,
            hole.area,
            ball_area,
        )
        return self.tracker.finish_at_rim(*position)

    # ------------------------------------------------------------------
    # 25단계: mm 기준 공 크기 · 고정 물체 · 홀인
    # ------------------------------------------------------------------
    def _load_size_settings(self) -> None:
        """ball / hole / static 설정과 저장된 고정 물체 목록을 불러온다."""
        settings = load_settings()
        self.ball = ball_settings(settings.get("ball"))
        self.hole_settings = {**DEFAULT_HOLE_SETTINGS, **(settings.get("hole") or {})}
        self.static_config = static_settings(settings.get("static"))
        self.static_objects = StaticObjectMap.from_settings(
            settings.get("static_objects"), self.static_config["rim_accept_ratio"]
        )
        # 저장 파일에 있던 목록 (처리 조건이 다시 맞으면 되살리려고 따로 기억해 둔다)
        self._saved_static_objects = self.static_objects
        self.static_collector = StaticObjectCollector(
            self.static_config["scan_duration"], self.static_config["still_tolerance_px"]
        )
        # 엔진 시작 후 첫 프레임에서 '저장된 고정 물체가 없으면 자동 수집'을 한 번만 하기 위한 표시
        self._static_checked_once = getattr(self, "_static_checked_once", False)
        # 지금 처리 중인 프레임 시각 (수집 시작 시각으로 쓴다. 프레임 처리 밖에서 불리면 현재 시각)
        self._clock_hint = getattr(self, "_clock_hint", None)

    def _expected_ball_area(self, x: float, y: float) -> float | None:
        """(x, y) 위치에서 공이 보여야 할 면적(px²). 캘리브레이션이 없으면 None (픽셀 기준 판정).

        화면 전체를 찾을 때는 덩어리(매트 잔털 등)가 많아서 매번 계산하면 느려진다. 예상 면적은
        위치에 따라 천천히 바뀌므로 16px 칸 단위로 한 번만 계산해서 기억해 둔다. 캘리브레이션이나
        공 지름이 바뀌면 기억을 비운다.
        """
        key_owner = (id(self.calibration.matrix), self.ball["diameter_mm"])
        if getattr(self, "_area_cache_owner", None) != key_owner:
            self._area_cache_owner = key_owner
            self._area_cache = {}
        cell = (int(x) // AREA_CACHE_CELL_PX, int(y) // AREA_CACHE_CELL_PX)
        if cell not in self._area_cache:
            center = ((cell[0] + 0.5) * AREA_CACHE_CELL_PX, (cell[1] + 0.5) * AREA_CACHE_CELL_PX)
            self._area_cache[cell] = self.calibration.expected_ball_area_px(center, self.ball["diameter_mm"])
        return self._area_cache[cell]

    def _ball_radius_px(self, x: float, y: float) -> float | None:
        """(x, y) 위치에서 공 반경(px). 예상 면적에서 거꾸로 구한다 (넓이 = 원주율 x 반경²)."""
        area = self._expected_ball_area(x, y) if self.calibration.is_calibrated() else None
        return math.sqrt(area / math.pi) if area else None

    def _static_filter(self, x: float, y: float, ball_ok: bool) -> bool:
        """검출기가 후보마다 묻는다: 고정 물체 자리라서 공으로 보면 안 되는가?"""
        return self.static_objects.is_static_candidate(x, y, ball_ok, self._ball_radius_px(x, y))

    def _mm_to_px(self, point, mm: float) -> float | None:
        """Tracker용: point(px) 근처에서 매트 위 mm 거리가 몇 px인지 (캘리브레이션 없으면 None)."""
        return self.calibration.mm_radius_to_px(point, mm)

    def _hole_at(self, x: float, y: float):
        """Tracker용: 공이 (x, y)(px)에서 사라졌을 때, 그 자리가 홀컵 근처면 (홀 번호, 컵 중심 px).

        '근처' = 컵 중심에서 capture_radius_mm(기본 60mm) 안. 다만 컵이 크게 보이는 현장(컵 반경 +
        공 반경이 60mm보다 큼)에서는 공이 컵 테두리에 닿는 거리까지 넓혀 준다.
        캘리브레이션이 없거나 홀인 판정이 꺼져 있으면 None (예전처럼 '화면 이탈'로 끝난다).
        """
        if not self.hole_settings.get("enabled", True) or not self.calibration.is_calibrated():
            return None
        point_mm = self.calibration.pixel_to_real(x, y)
        if point_mm is None:
            return None
        best = None
        best_distance = float("inf")
        for hole in self.static_objects.holes():
            hole_mm = self.calibration.pixel_to_real(hole.x, hole.y)
            px_per_mm = (self.calibration.mm_radius_to_px((hole.x, hole.y), 100.0) or 0) / 100.0
            if hole_mm is None or px_per_mm <= 0:
                continue
            hole_radius_mm = hole.radius / px_per_mm
            capture = max(
                float(self.hole_settings["capture_radius_mm"]), hole_radius_mm + self.ball["diameter_mm"] / 2
            )
            distance = math.hypot(point_mm[0] - hole_mm[0], point_mm[1] - hole_mm[1])
            if distance <= capture and distance < best_distance:
                best_distance, best = distance, (hole.hole, (hole.x, hole.y))
        return best

    def hole_list(self) -> list[dict]:
        """홀컵 목록 [{id, x_mm, y_mm}] (프로토콜 HELLO.holes용). 캘리브레이션 전이면 mm는 null."""
        result = []
        for hole in self.static_objects.holes():
            mm = self.calibration.pixel_to_real(hole.x, hole.y)
            result.append({
                "id": hole.hole,
                "x_mm": round(mm[0], 1) if mm else None,
                "y_mm": round(mm[1], 1) if mm else None,
            })
        return result

    def _validate_static_objects(self) -> None:
        """저장된 고정 물체가 지금 처리 조건(프레임 크기·k1)에서 모은 것인지 확인한다.

        다르면(해상도·왜곡 보정이 바뀜) 화면 좌표가 맞지 않으므로 쓰지 않는다 (파일의 값은 남겨 둔다:
        조건을 되돌리면 다시 쓸 수 있게).
        """
        saved = self._saved_static_objects
        valid = saved.matches_processing(self.frame_size, self.preprocessor.undistort_k1)
        if valid:
            if self.static_objects is not saved:
                self.static_objects = saved
                logger.info("저장된 고정 물체 %d개를 다시 사용합니다", len(saved.objects))
            return
        if saved.objects and self.static_objects.objects:
            logger.warning(
                "처리 조건(해상도/왜곡 보정)이 바뀌어 저장된 고정 물체 %d개를 쓰지 않습니다 - "
                "'고정 물체 다시 찾기'를 하세요",
                len(saved.objects),
            )
        self.static_objects = StaticObjectMap(rim_accept_ratio=self.static_config["rim_accept_ratio"])

    def forget_static_at(self, x: float, y: float) -> int:
        """(x, y)에 있는 고정 물체 중 홀컵이 아닌 것을 지운다. 지운 개수를 돌려준다.

        사용자가 색 잡기에서 공을 클릭했다면 그 자리에는 공이 있다는 확실한 표시다. 그 자리가
        고정 물체로 기억돼 있으면(예: 공을 둔 채 '고정 물체 다시 찾기'를 눌렀던 경우) 공을 영영
        못 찾으므로 지운다. 홀컵은 공보다 확실히 커서 공과 헷갈릴 일이 없으니 지우지 않는다.
        """
        keep = []
        removed = []
        for obj in self.static_objects.objects:
            if obj.hole is None and math.hypot(x - obj.x, y - obj.y) <= obj.radius + 5:
                removed.append(obj)
            else:
                keep.append(obj)
        if not removed:
            return 0
        self.static_objects.objects = keep
        self._saved_static_objects = self.static_objects
        settings = load_settings()
        settings["static_objects"] = self.static_objects.to_settings()
        save_settings(settings)
        for obj in removed:
            logger.info(
                "공을 클릭한 자리의 고정 물체를 지움 - 위치 (%.0f, %.0f)px, 면적 %.0fpx² (공을 고정 물체로 잘못 기억했던 것)",
                obj.x,
                obj.y,
                obj.area,
            )
        self.changed.emit()
        return len(removed)

    @property
    def static_scan_active(self) -> bool:
        """고정 물체를 모으는 중인지 (화면 안내용)."""
        return self.static_collector.active

    def rescan_static_objects(self) -> bool:
        """'고정 물체 다시 찾기' 버튼: 공을 치운 상태에서 고정 물체를 다시 모은다.

        추적 대기·추적 중에는 할 수 없다 (False). 이 수집은 공 크기 덩어리도 모두 기억하므로
        반드시 공을 매트에서 치우고 눌러야 한다 (시작점이 등록돼 있으면 그 자리는 뺀다).
        """
        if self.tracker.state in (TrackerState.ARMED, TrackerState.TRACKING):
            return False
        return self.start_static_scan(SCAN_MANUAL, now=self._clock_hint)

    def start_static_scan(self, kind: str, now: float | None = None) -> bool:
        """고정 물체 수집을 시작한다 (scan_duration초 뒤 자동으로 끝나고 저장된다)."""
        if not self.static_config.get("enabled", True):
            return False
        if now is None:
            now = time.time()
        exclude = [self.tracker.start_position] if self.tracker.start_position is not None else []
        self.static_collector.start(now, kind, exclude)
        logger.info(
            "고정 물체 수집 시작 (%s, %.1f초)%s",
            "수동" if kind == SCAN_MANUAL else "자동",
            self.static_collector.duration,
            " - 시작점의 공은 뺍니다" if exclude else "",
        )
        self.static_scan_changed.emit(True)
        return True

    def _cancel_static_scan(self, why: str) -> None:
        self.static_collector.cancel()
        logger.info("고정 물체 수집 취소 (%s) - 이전 목록을 그대로 씁니다", why)
        self.static_scan_changed.emit(False)

    def _feed_static_scan(self, frame, now: float) -> None:
        """수집 중이면 이번 프레임의 화면 전체 덩어리를 넣고, 시간이 다 되면 결과를 저장한다."""
        collector = self.static_collector
        if not collector.active:
            return
        calibrated = self.calibration.is_calibrated()
        expected_fn = self._expected_ball_area if calibrated else None
        blobs = self.detector.find_blobs(frame, expected_fn)
        center_area = None
        if calibrated and self.frame_size is not None:
            center_area = self._expected_ball_area(self.frame_size[0] / 2, self.frame_size[1] / 2)
        min_area = center_area * MIN_OBJECT_AREA_RATIO if center_area else MIN_OBJECT_AREA_PX
        inside = self.calibration.is_inside if self.calibration.filter_outside_mat else None
        collector.feed(blobs, now, min_area, inside)
        if collector.done(now):
            self._finish_static_scan()

    def _finish_static_scan(self) -> None:
        objects = self.static_collector.finish(self._ball_radius_px)
        if objects is None:
            logger.warning("고정 물체 수집 실패 - 수집 중 받은 프레임이 너무 적습니다 (이전 목록 유지)")
            self.static_scan_changed.emit(False)
            return
        calibrated = self.calibration.is_calibrated()
        if calibrated:
            # 공보다 훨씬 큰 덩어리(사람 신발 등)는 기억하지 않는다 (static.max_area_ratio 설명 참고)
            max_ratio = float(self.static_config["max_area_ratio"])
            kept = []
            for obj in objects:
                expected = self._expected_ball_area(obj.x, obj.y)
                if expected and obj.area > expected * max_ratio:
                    logger.info(
                        "  고정 물체에서 뺌: 위치 (%.0f, %.0f)px, 면적 %.0fpx² (공 예상 면적의 x%.1f > x%.1f, 신발 등 큰 물체)",
                        obj.x,
                        obj.y,
                        obj.area,
                        obj.area / expected,
                        max_ratio,
                    )
                else:
                    kept.append(obj)
            objects = kept
        number_holes(
            objects,
            self._expected_ball_area if calibrated else (lambda _x, _y: None),
            self.calibration.pixel_to_real,
            float(self.hole_settings["min_area_ratio"]),
        )
        new_map = StaticObjectMap(
            objects,
            self.frame_size,
            self.preprocessor.undistort_k1,
            self.static_config["rim_accept_ratio"],
        )
        self.static_objects = new_map
        self._saved_static_objects = new_map
        settings = load_settings()
        settings["static_objects"] = new_map.to_settings()
        save_settings(settings)

        holes = new_map.holes()
        logger.info("고정 물체 수집 완료 - %d개 (그중 홀컵 %d개)", len(objects), len(holes))
        for obj in objects:
            expected = self._expected_ball_area(obj.x, obj.y) if calibrated else None
            logger.info(
                "  고정 물체%s: 위치 (%.0f, %.0f)px%s, 반경 %.1fpx, 면적 %.0fpx²%s",
                f" (홀 {obj.hole}번)" if obj.hole is not None else "",
                obj.x,
                obj.y,
                f" = ({obj.x_mm:.0f}, {obj.y_mm:.0f})mm" if obj.x_mm is not None else "",
                obj.radius,
                obj.area,
                f" (공 예상 면적의 x{obj.area / expected:.2f})" if expected else "",
            )
        # changed(화면 전체 갱신)를 먼저 보내야, 그 뒤 static_scan_changed가 띄우는 "고정 물체 N개"
        # 안내가 상태 배지 갱신에 바로 덮이지 않는다.
        self.changed.emit()
        self.static_scan_changed.emit(False)

    # ------------------------------------------------------------------
    # 설정 다시 불러오기
    # ------------------------------------------------------------------
    def reload_detector(self) -> None:
        """검출 설정(프로필)이 바뀌었을 때 검출기를 새로 만든다."""
        self.detector = BallDetector()
        self.detector.debug_enabled = self.detection_debug

    def reload_settings(self) -> None:
        """프로필·매트 설정이 바뀌었을 때 검출기와 캘리브레이션을 새로 불러온다."""
        self.reload_detector()
        # 검색 영역 설정도 다시 읽는다 (search.enabled 등)
        self.search = SearchPlanner(load_settings().get("search"))
        self._load_size_settings()
        self.auto = AutoFlow(load_settings().get("auto"))
        # 핸들러는 항상 engine.calibration을 통해 접근하므로 따로 맞출 필요가 없다.
        self.calibration = Calibration()
        # 다시 불러온 캘리브레이션도 지금 처리 조건(프레임 크기·k1)에 맞는지 확인한다.
        self._validate_calibration()
        # 매트 크기가 바뀌었을 수 있으니 찍어 둔 모양과 비율이 맞는지 다시 본다 (오타 경고)
        self._update_calibration_notice()


def _roi_ratio(roi, frame_size) -> float:
    """검색 영역 넓이가 화면 넓이의 몇 배인지 (전체 검색 = 1.0). 로그의 '검색 영역 평균' 계산용."""
    if roi is None or not frame_size:
        return 1.0
    total = frame_size[0] * frame_size[1]
    return (roi[2] * roi[3]) / total if total else 1.0


def _is_positive_number(value) -> bool:
    """0보다 큰 JSON 숫자인지 (True/False는 파이썬에서 int지만 숫자로 보지 않는다)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
