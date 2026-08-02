"""
프로그램 상태 관리 모듈 (PROJECT_PLAN.md의 "4. 프로그램 상태 설계" 참고).

다섯 가지 상태를 관리한다.
IDLE(대기) -> CALIBRATING(캘리브레이션 중) -> READY(시작 위치 등록 완료)
-> TRACKING(공 이동 중) -> FINISHED(추적 완료)

버튼을 눌러 상태를 바꾸고 싶을 때는 각 동작 메서드(calibrate,
register_start_position 등)를 호출한다. 현재 상태에서 그 동작이
허용되지 않으면 아무 것도 바꾸지 않고 False만 반환한다
(예외를 던지지 않는다). 어떤 동작이 지금 허용되는지는 can()으로
미리 물어볼 수 있어서, UI 쪽에서 버튼을 활성/비활성화하는 데 쓴다.

등록된 공의 시작 위치(start_position)와 이동 궤적(trajectory)도
여기서 함께 관리한다. '궤적 초기화'를 누르면 캘리브레이션은 남기고
시작점 + 궤적을 함께 지운다.

궤적 기록 시 직전 점과 너무 가까우면(config/settings.json의
tracking.min_move_distance 미만) 기록하지 않아서, 점이 촘촘하게
쌓여 무거워지는 것을 막는다.
"""

from dataclasses import dataclass
from enum import Enum

from src.config import load_settings

# config/settings.json에 "tracking" 항목이 없거나 값이 빠져 있을 때 쓸 기본값
DEFAULT_MIN_MOVE_DISTANCE = 6


class TrackerState(Enum):
    """프로그램의 현재 단계. 값(value)은 상태 배지에 그대로 표시되는 문구다."""

    IDLE = "대기 중"
    CALIBRATING = "캘리브레이션 중"
    READY = "시작 위치 등록 완료"
    TRACKING = "공 이동 중"
    FINISHED = "추적 완료"


@dataclass
class TrajectoryPoint:
    """궤적에 기록된 점 하나 (프레임 좌표 기준, 단위: 픽셀)."""

    x: int
    y: int
    timestamp: float  # time.time() 값


class Tracker:
    """다섯 가지 상태를 관리하는 상태 기계(state machine)."""

    # 동작 이름 -> 그 동작이 허용되는 현재 상태 집합.
    # 버튼 활성화 여부(UI)와 실제 상태 전환 허용 여부가 항상 같은 기준을
    # 쓰도록 이 dict 하나에서만 관리한다.
    ALLOWED_STATES = {
        "calibrate": {
            TrackerState.IDLE,
            TrackerState.CALIBRATING,
            TrackerState.READY,
            TrackerState.FINISHED,
        },
        "register_start_position": {
            TrackerState.CALIBRATING,
            TrackerState.READY,
            TrackerState.FINISHED,
        },
        "start_tracking": {TrackerState.READY},
        "stop_tracking": {TrackerState.TRACKING},
        "finish_tracking": {TrackerState.TRACKING},
        "reset_trajectory": {
            TrackerState.READY,
            TrackerState.TRACKING,
            TrackerState.FINISHED,
        },
        "save": {TrackerState.FINISHED},
    }

    def __init__(self, settings: dict | None = None):
        self.state = TrackerState.IDLE
        # 등록된 공의 시작 위치 (x, y) - 픽셀 좌표, 없으면 None
        self.start_position: tuple[int, int] | None = None
        # 기록된 이동 궤적 (TrajectoryPoint 목록), 시간 순서대로 쌓인다
        self.trajectory: list[TrajectoryPoint] = []

        loaded = settings if settings is not None else load_settings().get("tracking", {})
        self._min_move_distance = loaded.get("min_move_distance", DEFAULT_MIN_MOVE_DISTANCE)

    def can(self, action: str) -> bool:
        """지금 상태에서 이 동작을 해도 되는지 확인한다. (버튼 활성화 여부에 사용)"""
        return self.state in self.ALLOWED_STATES.get(action, set())

    def _transition(self, action: str, to: TrackerState) -> bool:
        if not self.can(action):
            return False
        self.state = to
        return True

    def calibrate(self) -> bool:
        """'캘리브레이션' 버튼: 매트 모서리 지정을 시작한다."""
        return self._transition("calibrate", TrackerState.CALIBRATING)

    def register_start_position(self, x: int, y: int) -> bool:
        """'시작 위치 등록' 버튼: 검출된 공 좌표를 시작점(원점)으로 저장한다.

        공이 검출되지 않았을 때 호출하지 않는 것은(좌표를 모르니까)
        호출하는 쪽(UI)의 책임이다. 여기서는 상태 전환 허용 여부만 본다.
        """
        if not self._transition("register_start_position", TrackerState.READY):
            return False
        self.start_position = (x, y)
        return True

    def start_tracking(self) -> bool:
        """'추적 준비' 버튼.

        원래는 공 움직임을 자동으로 감지해서 TRACKING으로 넘어가야 하지만
        (# TODO: 6~7단계에서 실제 움직임 감지로 교체), 이번 단계는 상태
        전환 자체가 목적이라 버튼을 누르면 바로 TRACKING으로 전환한다.
        """
        return self._transition("start_tracking", TrackerState.TRACKING)

    def add_trajectory_point(self, x: int, y: int, timestamp: float) -> bool:
        """TRACKING 상태일 때, 검출된 공 좌표를 궤적에 기록한다.

        TRACKING 상태가 아니면 기록하지 않는다 (False 반환).
        직전에 기록한 점과 min_move_distance 미만으로 가까우면
        너무 촘촘해지지 않도록 기록을 건너뛴다 (첫 점은 무조건 기록).
        """
        if self.state != TrackerState.TRACKING:
            return False

        if self.trajectory:
            last = self.trajectory[-1]
            distance = ((x - last.x) ** 2 + (y - last.y) ** 2) ** 0.5
            if distance < self._min_move_distance:
                return False

        self.trajectory.append(TrajectoryPoint(x=x, y=y, timestamp=timestamp))
        return True

    def stop_tracking(self) -> bool:
        """'추적 중지' 버튼: 오검출 등 문제가 생겼을 때 수동으로 중지하고 READY로 되돌아간다."""
        return self._transition("stop_tracking", TrackerState.READY)

    def finish_tracking(self) -> bool:
        """공이 멈추거나 화면을 벗어났을 때 호출해서 추적을 정상 종료한다.

        # TODO: 7단계(정지·이탈 판정)에서 실제로 연결한다.
        지금은 어떤 버튼과도 연결돼 있지 않다.
        """
        return self._transition("finish_tracking", TrackerState.FINISHED)

    def reset_trajectory(self) -> bool:
        """'궤적 초기화' 버튼: 캘리브레이션은 유지하고 시작점 + 이전 경로를 지운 뒤 READY로 돌아간다."""
        if not self._transition("reset_trajectory", TrackerState.READY):
            return False
        self.start_position = None
        self.trajectory = []
        return True
