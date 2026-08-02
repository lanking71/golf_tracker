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

반대로 직전 점과 너무 멀리 떨어진 경우(max_jump_distance 이상)는
오검출(잘못 잡힌 다른 물체 등)로 보고 기록하지 않는다. 다만 이런
"멀리 떨어진 값"이 연속 max_consecutive_jumps번 이상 나오면, 오검출이
아니라 공을 실제로 다른 곳에 옮겨 놓은 것으로 보고 그 위치를 새로
받아들인다.

TRACKING 중에는 매 프레임 update_detection()을 호출해 정지·이탈을
판정한다 (PROJECT_PLAN.md "5. 공이 멈춘 것은 어떻게 판단하나요?" 참고).
- 정지: 프레임 간 이동량이 stop_threshold 미만인 상태가
  stop_duration초 이상 유지되면 정지로 보고 FINISHED로 전환한다.
- 이탈: 공 검출 실패가 lost_duration초 이상 지속되면 화면 이탈로 보고
  FINISHED로 전환한다.
전환 사유는 finish_reason에 "정지 감지" / "화면 이탈"로 남는다.
"""

from dataclasses import dataclass
from enum import Enum

from src.config import load_settings

# config/settings.json에 "tracking" 항목이 없거나 값이 빠져 있을 때 쓸 기본값
DEFAULT_MIN_MOVE_DISTANCE = 6
DEFAULT_MAX_JUMP_DISTANCE = 80
DEFAULT_MAX_CONSECUTIVE_JUMPS = 10
DEFAULT_STOP_THRESHOLD = 3
DEFAULT_STOP_DURATION = 1.0
DEFAULT_LOST_DURATION = 2.0

# finish_reason에 남는 종료 사유 문구 (상태 배지에 그대로 표시됨)
STOP_REASON = "정지 감지"
LOST_REASON = "화면 이탈"


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
        # 정지 판정으로 멈춘 위치 (x, y) - 없으면 None
        self.stop_position: tuple[int, int] | None = None
        # FINISHED로 전환된 사유 ("정지 감지" / "화면 이탈"), 없으면 None
        self.finish_reason: str | None = None

        loaded = settings if settings is not None else load_settings().get("tracking", {})
        self._min_move_distance = loaded.get("min_move_distance", DEFAULT_MIN_MOVE_DISTANCE)
        # 궤적 패널이 "선을 잇지 않을" 기준으로도 그대로 쓰므로 공개 속성으로 둔다.
        self.max_jump_distance = loaded.get("max_jump_distance", DEFAULT_MAX_JUMP_DISTANCE)
        self._max_consecutive_jumps = loaded.get(
            "max_consecutive_jumps", DEFAULT_MAX_CONSECUTIVE_JUMPS
        )
        self._stop_threshold = loaded.get("stop_threshold", DEFAULT_STOP_THRESHOLD)
        self._stop_duration = loaded.get("stop_duration", DEFAULT_STOP_DURATION)
        self._lost_duration = loaded.get("lost_duration", DEFAULT_LOST_DURATION)

        # 직전 점과 너무 멀어서(오검출로 보고) 연속으로 버린 횟수
        self._consecutive_jump_count = 0
        # 정지 판정용: 마지막으로 본 원시 검출 좌표와, 그 위치 근처에서
        # 멈춰 있기 시작한(프레임 간 이동이 stop_threshold 미만이 된) 시각
        self._last_raw_position: tuple[int, int] | None = None
        self._stop_since: float | None = None
        # 이탈 판정용: 공을 못 찾기 시작한 시각
        self._lost_since: float | None = None

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

        새 시작점을 등록한다는 것은 새로운 시도(퍼팅)를 시작한다는
        뜻이므로, 이전 시도의 궤적·정지 정보를 모두 정리한다. 이걸
        안 하면 '궤적 초기화'를 누르지 않고 재시도할 때 새 공 위치가
        이전 궤적의 마지막 점과 비교되어 "너무 멀리 떨어진 오검출"로
        계속 무시되는 문제가 있었다 (연속 max_consecutive_jumps번을
        채워야만 다시 기록되기 시작해서, 시도할 때마다 되다 안 되다
        했음).
        """
        if not self._transition("register_start_position", TrackerState.READY):
            return False
        self.start_position = (x, y)
        self.trajectory = []
        self.stop_position = None
        self.finish_reason = None
        self._consecutive_jump_count = 0
        self._last_raw_position = None
        self._stop_since = None
        self._lost_since = None
        return True

    def start_tracking(self) -> bool:
        """'추적 준비' 버튼.

        원래는 공 움직임을 자동으로 감지해서 TRACKING으로 넘어가야 하지만
        (# TODO: 6단계에서 실제 움직임 감지로 교체), 이번 단계는 상태
        전환 자체가 목적이라 버튼을 누르면 바로 TRACKING으로 전환한다.
        정지·이탈 판정에 쓰는 타이머들도 새로 시작하도록 초기화한다.
        """
        if not self._transition("start_tracking", TrackerState.TRACKING):
            return False
        self.stop_position = None
        self.finish_reason = None
        self._last_raw_position = None
        self._stop_since = None
        self._lost_since = None
        return True

    def add_trajectory_point(self, x: int, y: int, timestamp: float) -> bool:
        """TRACKING 상태일 때, 검출된 공 좌표를 궤적에 기록한다.

        TRACKING 상태가 아니면 기록하지 않는다 (False 반환).
        직전에 기록한 점 기준으로:
        - min_move_distance보다 가까우면: 너무 촘촘해지지 않도록 건너뛴다.
        - max_jump_distance보다 멀면: 오검출로 보고 건너뛴다. 단, 이렇게
          멀리 떨어진 값이 연속 max_consecutive_jumps번 나오면 공을 실제로
          옮긴 것으로 보고 이번 값을 새 위치로 받아들인다.
        첫 점은 비교 대상이 없으므로 무조건 기록한다.
        """
        if self.state != TrackerState.TRACKING:
            return False

        if not self.trajectory:
            self._consecutive_jump_count = 0
            self.trajectory.append(TrajectoryPoint(x=x, y=y, timestamp=timestamp))
            return True

        last = self.trajectory[-1]
        distance = ((x - last.x) ** 2 + (y - last.y) ** 2) ** 0.5

        if distance >= self.max_jump_distance:
            self._consecutive_jump_count += 1
            if self._consecutive_jump_count < self._max_consecutive_jumps:
                return False  # 오검출로 보고 이번 값은 버린다
            # 연속으로 계속 멀리 떨어진 값이 나왔다 = 공을 실제로 옮긴 것으로 본다
            self._consecutive_jump_count = 0
        elif distance < self._min_move_distance:
            return False
        else:
            self._consecutive_jump_count = 0

        self.trajectory.append(TrajectoryPoint(x=x, y=y, timestamp=timestamp))
        return True

    def update_detection(self, x: int | None, y: int | None, timestamp: float) -> str | None:
        """TRACKING 상태에서 매 프레임 호출해 정지·이탈 여부를 판정한다.

        공을 찾았으면 (x, y)를, 못 찾았으면 (None, None)을 넘긴다.
        조건을 만족해 FINISHED로 전환됐으면 종료 사유
        (STOP_REASON/LOST_REASON)를 반환하고, 아니면 내부 타이머만
        갱신하고 None을 반환한다.
        """
        if self.state != TrackerState.TRACKING:
            return None

        if x is None or y is None:
            # 공을 못 찾음 -> 이탈 타이머 시작/유지
            if self._lost_since is None:
                self._lost_since = timestamp
            elif timestamp - self._lost_since >= self._lost_duration:
                return self._finish_with_reason(LOST_REASON)
            return None

        # 공을 다시 찾았으면 이탈 타이머는 리셋
        self._lost_since = None

        if self._last_raw_position is not None:
            prev_x, prev_y = self._last_raw_position
            distance = ((x - prev_x) ** 2 + (y - prev_y) ** 2) ** 0.5
            if distance > self._stop_threshold:
                # 의미 있게 움직였다 -> 정지 타이머 리셋
                self._stop_since = None
            elif self._stop_since is None:
                # 방금 정지 상태로 들어왔다 -> 타이머 시작
                self._stop_since = timestamp

        self._last_raw_position = (x, y)

        if self._stop_since is not None and timestamp - self._stop_since >= self._stop_duration:
            self.stop_position = (x, y)
            return self._finish_with_reason(STOP_REASON)

        return None

    def _finish_with_reason(self, reason: str) -> str:
        self.finish_tracking()
        self.finish_reason = reason
        return reason

    def stop_tracking(self) -> bool:
        """'추적 중지' 버튼: 오검출 등 문제가 생겼을 때 수동으로 중지하고 READY로 되돌아간다."""
        return self._transition("stop_tracking", TrackerState.READY)

    def finish_tracking(self) -> bool:
        """공이 멈추거나 화면을 벗어났을 때 호출해서 추적을 정상 종료한다.

        update_detection()이 정지/이탈을 판정하면 자동으로 호출한다.
        지금은 어떤 버튼과도 연결돼 있지 않다.
        """
        return self._transition("finish_tracking", TrackerState.FINISHED)

    def reset_trajectory(self) -> bool:
        """'궤적 초기화' 버튼: 캘리브레이션은 유지하고 시작점 + 이전 경로를 지운 뒤 READY로 돌아간다."""
        if not self._transition("reset_trajectory", TrackerState.READY):
            return False
        self.start_position = None
        self.trajectory = []
        self.stop_position = None
        self.finish_reason = None
        self._consecutive_jump_count = 0
        self._last_raw_position = None
        self._stop_since = None
        self._lost_since = None
        return True
