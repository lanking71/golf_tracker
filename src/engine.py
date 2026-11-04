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
    카메라 읽기 -> 공 검출 -> 매트 밖 검출 무시 -> 궤적 기록·정지/이탈 판정
    -> 앱(메인 컨트롤)에 상태 전송 -> (끝났으면) 결과 전송 -> 화면에 알림
"""

import time
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from src.calibration import Calibration, detect_mat_corners
from src.camera import Camera
from src.config import load_settings
from src.detector import DEFAULT_DETECTION_SETTINGS, BallDetection, BallDetector
from src.protocol_handler import ProtocolHandler
from src.protocol_server import ProtocolServer
from src.stats import compute_trajectory_stats
from src.storage import Storage
from src.tracker import Tracker, TrackerState, TrajectoryPoint

# 카메라를 확인하는 주기(ms). 실제 처리 속도는 카메라 프레임 도착 속도가 정한다.
CAMERA_POLL_INTERVAL_MS = 8

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

    frame: np.ndarray  # 카메라 원본 프레임 (그림을 그리기 전). 그리려면 복사해서 쓸 것
    detection: BallDetection | None  # 매트 안에서 인정된 공 (없으면 None)
    raw_detection: BallDetection | None  # 매트 밖이라 무시된 것까지 포함한 원래 검출
    outside_mat: bool  # 공은 찾았지만 매트 밖이라 무시했는지
    now: float  # 이 프레임을 처리한 시각 (time.time())
    # True면 HSV 튜닝 미리보기 중이라 검출·추적을 건너뛴 프레임이다.
    paused: bool = False


class VisionEngine(QObject):
    """카메라·검출·추적·캘리브레이션·저장·앱 통신을 모두 묶어서 돌리는 엔진."""

    # 한 프레임 처리가 끝날 때마다 (화면이 이걸 받아서 그린다)
    frame_processed = Signal(object)
    # 카메라가 없거나 연결이 끊겨서 프레임을 못 읽었을 때
    camera_unavailable = Signal()
    # 앱 명령 등으로 상태가 바뀌었을 때 (화면이 버튼·배지를 다시 맞춘다)
    changed = Signal()
    # 궤적에 점이 새로 기록됐을 때
    trajectory_updated = Signal()
    # 정지·이탈로 추적이 자동 종료됐을 때 (종료 사유 문구를 함께 전달)
    tracking_finished = Signal(str)

    def __init__(self, camera=None, parent=None):
        """camera를 넘기지 않으면 실제 USB 카메라를 쓴다 (테스트에서는 가짜 카메라를 넘긴다)."""
        super().__init__(parent)

        self.camera = camera if camera is not None else Camera()
        self.detector = BallDetector()
        self.tracker = Tracker()
        self.calibration = Calibration()
        self.storage = Storage()

        # 앱(메인 컨트롤)과 JSON으로 통신하는 TCP 서버 + 명령 처리기.
        # 서버가 받은 명령을 핸들러가 해석하고, 핸들러는 화면 버튼과 같은
        # 엔진·Tracker 메서드를 쓴다 (그래서 상태 규칙이 항상 같다).
        self.protocol_server = ProtocolServer(parent=self)
        self.protocol_handler = ProtocolHandler(
            engine=self,
            send=self.protocol_server.send,
            settings=load_settings().get("protocol", {}),
        )
        self.protocol_server.command_received.connect(self.protocol_handler.handle)

        # 가장 최근 프레임의 정보 (앱 명령 처리와 화면이 읽어 간다)
        self.last_raw_frame: np.ndarray | None = None
        # 매트 안에서 인정된 공. '시작 위치 등록'이 이 값을 시작점으로 쓴다.
        self.last_detection: BallDetection | None = None
        # 방금 프레임에서 공을 찾긴 했지만 매트 밖이라 무시했는지
        self.last_detection_outside_mat = False

        # True면 검출·추적을 건너뛰고 프레임만 화면에 넘긴다 (HSV 튜닝 미리보기용)
        self.detection_paused = False

        self._timer = QTimer(self)
        self._timer.setInterval(CAMERA_POLL_INTERVAL_MS)
        self._timer.timeout.connect(self.process_frame)

    # ------------------------------------------------------------------
    # 시작 / 종료
    # ------------------------------------------------------------------
    def start(self) -> bool:
        """TCP 서버를 열고 프레임 처리를 시작한다.

        반환값: 통신이 꺼져 있거나(설정) 서버를 정상적으로 열었으면 True,
        열어야 하는데 포트를 못 열었으면(이미 사용 중 등) False.
        """
        self.protocol_server.start()
        self._timer.start()
        return (not self.protocol_server.enabled) or self.protocol_server.is_listening()

    def shutdown(self) -> None:
        """프로그램을 끝낼 때 타이머를 멈추고 서버·카메라를 정리한다."""
        self._timer.stop()
        self.protocol_server.stop()
        self.camera.release()

    # ------------------------------------------------------------------
    # 프레임 처리 (프로그램의 심장박동)
    # ------------------------------------------------------------------
    def process_frame(self, now: float | None = None) -> None:
        """카메라에서 한 프레임을 읽어 검출·추적·전송까지 처리한다.

        타이머가 주기적으로 부른다. now는 테스트에서 시각을 직접 정해
        넣을 때만 쓰고, 평소에는 현재 시각을 쓴다.
        카메라가 없거나 끊겨도 예외를 던지지 않고 camera_unavailable
        시그널만 보낸 뒤, 다음 호출 때 다시 연결을 시도한다.
        """
        if not self.camera.is_opened():
            if not self.camera.open():
                self._on_camera_unavailable()
                return

        frame = self.camera.read_frame()
        if frame is None:
            # 읽기에 실패했다면 연결이 끊긴 것으로 보고 장치를 반납한다.
            self.camera.release()
            self._on_camera_unavailable()
            return

        if now is None:
            now = time.time()
        self.last_raw_frame = frame

        if self.detection_paused:
            # HSV 튜닝 중에는 검출·추적을 하지 않는다 (화면이 마스크 미리보기를 그린다).
            self.last_detection = None
            self.last_detection_outside_mat = False
            self.frame_processed.emit(FrameResult(frame, None, None, False, now, paused=True))
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
            # 걸 알 수 있게 raw_detection과 outside_mat을 함께 넘긴다 - 그냥
            # 안 보이는 것과 구분이 안 되면 왜 궤적이 안 그려지는지 알기 어렵다.
            outside_mat = True
            detection = None
        self.last_detection = detection
        self.last_detection_outside_mat = outside_mat

        if detection is not None:
            # TRACKING 상태일 때만 실제로 기록된다 (Tracker가 알아서 상태를 확인한다).
            if self.tracker.add_trajectory_point(detection.x, detection.y, now):
                self.trajectory_updated.emit()
            finish_reason = self.tracker.update_detection(detection.x, detection.y, now)
        else:
            finish_reason = self.tracker.update_detection(None, None, now)

        # 앱에 상태 변화·실시간 좌표를 보낸다 (종료 시에는 STATUS(FINISHED) 다음에 RESULT가 간다).
        self.protocol_handler.on_frame(detection, now)

        if finish_reason is not None:
            self.protocol_handler.on_finished(finish_reason)
            self.tracking_finished.emit(finish_reason)

        self.frame_processed.emit(FrameResult(frame, detection, raw_detection, outside_mat, now))

    def _on_camera_unavailable(self) -> None:
        """카메라 프레임을 못 얻었을 때: 검출 상태를 비우고 화면에 알린다."""
        # 오래된 프레임으로 캘리브레이션·시작점 등록을 하지 않도록 함께 비운다.
        self.last_raw_frame = None
        self.last_detection = None
        self.last_detection_outside_mat = False
        self.camera_unavailable.emit()

    # ------------------------------------------------------------------
    # 화면 버튼과 앱 명령이 함께 쓰는 동작
    # ------------------------------------------------------------------
    def start_calibration(self) -> bool:
        """'캘리브레이션' 버튼: 모서리를 지우고 CALIBRATING으로 들어가 자동 검출을 시도한다.

        성공하면 바로 저장하고 True, 실패하면(매트가 안 보이거나 조명 문제)
        False를 돌려준다 - 이때는 화면에서 모서리 4점을 직접 클릭하게 된다.
        다시 눌러도(이미 CALIBRATING이어도) 항상 새로 시작해서, 잘못 찍었을
        때 같은 버튼으로 '다시 지정'할 수 있다.
        """
        self.calibration.reset_corners()
        self.tracker.calibrate()
        auto_ok = self.last_raw_frame is not None and self.calibration.auto_calibrate(
            self.last_raw_frame
        )
        if auto_ok:
            self.calibration.save()
        return auto_ok

    def add_calibration_corner(self, x: int, y: int) -> bool:
        """화면에서 클릭한 매트 모서리를 등록한다 (CALIBRATING 상태일 때만).

        4번째 점을 찍으면 원근 변환 행렬이 만들어지고 config/settings.json에
        저장된다 (재실행 시 자동으로 다시 불러온다). 점이 등록되면 True.
        """
        if self.tracker.state != TrackerState.CALIBRATING:
            return False
        if not self.calibration.add_corner(x, y):
            return False
        if self.calibration.is_calibrated():
            self.calibration.save()
        return True

    def align_auto(self, width_mm=None, height_mm=None) -> str:
        """앱의 ALIGN 명령: 녹색 매트를 자동 검출해 캘리브레이션한다.

        크기(width_mm/height_mm)가 오면 함께 적용한다. 성공하면 "OK", 실패하면
        이유 코드("bad_state", "bad_size", "no_frame", "mat_not_found")를 돌려준다.
        모두 성공한 뒤에만 상태를 바꾼다 (실패했는데 크기만 바뀌는 일이 없도록).
        """
        if not self.tracker.can("calibrate"):
            return "bad_state"

        width = self.calibration.mat_width_mm if width_mm is None else width_mm
        height = self.calibration.mat_height_mm if height_mm is None else height_mm
        if not (_is_positive_number(width) and _is_positive_number(height)):
            return "bad_size"

        if self.last_raw_frame is None:
            return "no_frame"
        corners = detect_mat_corners(
            self.last_raw_frame, self.calibration.mat_hsv_lower, self.calibration.mat_hsv_upper
        )
        if corners is None:
            return "mat_not_found"

        self.calibration.set_mat_size(width, height)
        self.calibration.set_corners(corners)
        self.calibration.save()
        self.tracker.calibrate()
        return "OK"

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
        return START_OK

    def start_tracking(self) -> bool:
        """'추적 준비' / 앱의 ADD 명령. 허용되는 상태(시작점 등록 후)가 아니면 False."""
        return self.tracker.start_tracking()

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
        )
        return SAVE_OK, result_id

    def active_profile_name(self) -> str:
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

    # ------------------------------------------------------------------
    # 설정 다시 불러오기
    # ------------------------------------------------------------------
    def reload_detector(self) -> None:
        """검출 설정(프로필)이 바뀌었을 때 검출기를 새로 만든다."""
        self.detector = BallDetector()

    def reload_settings(self) -> None:
        """프로필·매트 설정이 바뀌었을 때 검출기와 캘리브레이션을 새로 불러온다."""
        self.detector = BallDetector()
        # 핸들러는 항상 engine.calibration을 통해 접근하므로 따로 맞출 필요가 없다.
        self.calibration = Calibration()


def _is_positive_number(value) -> bool:
    """0보다 큰 JSON 숫자인지 (True/False는 파이썬에서 int지만 숫자로 보지 않는다)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
