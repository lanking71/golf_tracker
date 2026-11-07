"""
프로그램 상태 관리 모듈 (PROJECT_PLAN.md의 "4. 프로그램 상태 설계" 참고).

여섯 가지 상태를 관리한다.
IDLE(대기) -> CALIBRATING(캘리브레이션 중) -> READY(시작 위치 등록 완료)
-> ARMED(추적 대기: 공이 움직이길 기다리는 중)
-> TRACKING(공 이동 중) -> FINISHED(추적 완료)

'추적 준비'(앱의 ADD 명령)를 누르면 바로 TRACKING이 되지 않고 ARMED가 된다.
ARMED에서는 정지·이탈 판정을 하지 않고 공이 시작점에서 arm_move_distance(px, 캘리브레이션이
있으면 arm_move_distance_mm) 이상 움직이는 순간 자동으로 TRACKING이 된다. 그래서 퍼팅 전에 공이 오래
가만히 있어도 "정지"로 오판해 추적이 끝나버리지 않는다. 추적이 시작된 시각은
공이 마지막으로 가만히 있던 프레임(tracking_started_at)으로 기록해서, 스윙 직후의
시작 시각이 정확하다.

버튼을 눌러 상태를 바꾸고 싶을 때는 각 동작 메서드(calibrate,
register_start_position 등)를 호출한다. 현재 상태에서 그 동작이
허용되지 않으면 아무 것도 바꾸지 않고 False만 반환한다
(예외를 던지지 않는다). 어떤 동작이 지금 허용되는지는 can()으로
미리 물어볼 수 있어서, UI 쪽에서 버튼을 활성/비활성화하는 데 쓴다.

등록된 공의 시작 위치(start_position)와 이동 궤적(trajectory)도
여기서 함께 관리한다. '궤적 초기화'를 누르면 캘리브레이션은 남기고
시작점 + 궤적을 함께 지운다.

'시작 위치 등록'은 IDLE에서도 허용된다 - 캘리브레이션(모서리·변환
행렬)은 config/settings.json에 저장되어 프로그램을 재시작해도 남아
있으므로, 이전에 이미 캘리브레이션을 해뒀다면 매번 다시 캘리브레이션
버튼을 누르지 않고 바로 시작 위치 등록부터 이어서 할 수 있어야 한다.

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

25단계 추가:
- 홀인: 공이 홀컵(고정 물체, src/static_objects.py) 근처에서 사라진 뒤 hole.confirm_duration초
  (기본 0.5초) 동안 다시 나타나지 않으면 "홀인"으로 끝낸다. 끝 위치(stop_position)는 컵 중심이고,
  어느 컵인지는 finish_hole(홀 번호)에 남는다. 그 사이에 공이 다시 보이면(컵을 스치고 지나감,
  립아웃) 추적을 계속한다. 컵과 먼 곳에서 사라지면 예전처럼 lost_duration 뒤 "화면 이탈".
  '어느 컵 근처인지'는 엔진이 넣어 주는 hole_finder 함수가 판단한다 (mm 거리 계산은 엔진 몫).
- 이동 기준을 mm로: arm_move_distance_mm / min_move_distance_mm / max_jump_distance_mm.
  엔진이 mm_to_px 함수(그 위치 근처에서 mm가 몇 px인지)를 넣어 주면 mm 값을 그 위치의 px로 바꿔
  쓰고, 캘리브레이션이 없어서 바꿀 수 없으면 예전 px 값(arm_move_distance 등)을 그대로 쓴다.
  매트 크기·카메라 높이가 현장마다 달라도 같은 mm 값으로 같은 동작을 한다.
"""

from dataclasses import dataclass
from enum import Enum

from src.config import load_settings

# config/settings.json에 "tracking" 항목이 없거나 값이 빠져 있을 때 쓸 기본값
DEFAULT_MIN_MOVE_DISTANCE = 6
# 프레임 간 이 값(px) 이상 떨어지면 일단 오검출 의심 대상이 된다.
# 너무 작으면 빠르게 구르는 공(특히 카메라 실효 FPS가 낮을 때)의
# 정상적인 이동까지 오검출로 오인해 궤적이 거의 안 남는 문제가 생긴다.
# 카메라·매트 크기에 따라 config/settings.json에서 조정한다.
DEFAULT_MAX_JUMP_DISTANCE = 200
DEFAULT_MAX_CONSECUTIVE_JUMPS = 10
DEFAULT_STOP_THRESHOLD = 3
DEFAULT_STOP_DURATION = 1.0
DEFAULT_LOST_DURATION = 2.0
# ARMED(추적 대기) 상태에서 공이 시작점에서 이 거리(px) 이상 움직이면 추적을 시작한다.
# 검출 좌표의 흔들림(보통 1~3px)보다는 크게, 실제 퍼팅의 첫 움직임보다는 작게 잡는다.
# 카메라 해상도·거리에 따라 현장에서 config/settings.json의
# tracking.arm_move_distance로 조정한다.
DEFAULT_ARM_MOVE_DISTANCE = 8

# 25단계: 이동 기준(mm). 캘리브레이션이 있으면 이 값을 쓰고, 없으면 위의 px 값을 쓴다.
# 22단계 보고: 0.75배 화면에서 8px = 약 18mm -> 지금 동작과 비슷하게 맞춘 값이다.
DEFAULT_ARM_MOVE_DISTANCE_MM = 20
DEFAULT_MIN_MOVE_DISTANCE_MM = 10
DEFAULT_MAX_JUMP_DISTANCE_MM = 500
# 25단계: 홀인 확정 시간(초). 공이 컵 근처에서 사라진 뒤 이 시간 동안 다시 안 보이면 홀인
DEFAULT_HOLE_CONFIRM_DURATION = 0.5

# finish_reason에 남는 종료 사유 문구 (상태 배지에 그대로 표시됨)
STOP_REASON = "정지 감지"
LOST_REASON = "화면 이탈"
HOLE_REASON = "홀인"


class TrackerState(Enum):
    """프로그램의 현재 단계. 값(value)은 상태 배지에 그대로 표시되는 문구다."""

    IDLE = "대기 중"
    CALIBRATING = "캘리브레이션 중"
    READY = "시작 위치 등록 완료"
    ARMED = "추적 대기 (공 이동 대기)"
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
            TrackerState.IDLE,
            TrackerState.CALIBRATING,
            TrackerState.READY,
            TrackerState.FINISHED,
        },
        "start_tracking": {TrackerState.READY},
        # 공이 움직이기 시작하면 자동으로 일어나는 전환이라 버튼은 없다 (Tracker 내부용)
        "begin_tracking": {TrackerState.ARMED},
        # 대기 중(ARMED)이든 추적 중(TRACKING)이든 중지해서 READY로 돌아갈 수 있다
        "stop_tracking": {TrackerState.ARMED, TrackerState.TRACKING},
        "finish_tracking": {TrackerState.TRACKING},
        "reset_trajectory": {
            TrackerState.READY,
            TrackerState.ARMED,
            TrackerState.TRACKING,
            TrackerState.FINISHED,
        },
        "save": {TrackerState.FINISHED},
        # 26단계 자동 흐름: 저장된 캘리브레이션이 유효하면 대기(IDLE)에서 바로 READY로 (버튼 없이)
        "auto_ready": {TrackerState.IDLE},
    }

    def __init__(self, settings: dict | None = None, hole_settings: dict | None = None):
        if hole_settings is None and settings is None:
            hole_settings = load_settings().get("hole", {})
        self.state = TrackerState.IDLE
        # 등록된 공의 시작 위치 (x, y) - 픽셀 좌표, 없으면 None
        self.start_position: tuple[int, int] | None = None
        # 기록된 이동 궤적 (TrajectoryPoint 목록), 시간 순서대로 쌓인다
        self.trajectory: list[TrajectoryPoint] = []
        # 정지 판정으로 멈춘 위치 (x, y) - 없으면 None
        self.stop_position: tuple[int, int] | None = None
        # FINISHED로 전환된 사유 ("정지 감지" / "화면 이탈" / "홀인"), 없으면 None
        self.finish_reason: str | None = None
        # 홀인으로 끝났을 때 그 컵의 홀 번호 (아니면 None)
        self.finish_hole: int | None = None
        # 정지로 끝났을 때 공이 멈추기 시작한 시각 (26단계 자리 고쳐 놓기 판단용)
        self.stop_started_at: float | None = None

        # 25단계: 엔진이 넣어 주는 함수들 (없으면 예전 px 기준 동작)
        # - mm_to_px(point, mm): point(px) 근처에서 매트 위 mm 거리가 몇 px인지. 캘리브레이션이 없으면 None
        # - hole_finder(x, y): (x, y)(px)가 어느 홀컵 근처면 (홀 번호, (컵 중심 x, y) px), 아니면 None
        self.mm_to_px = None
        self.hole_finder = None

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
        self._arm_move_distance = loaded.get("arm_move_distance", DEFAULT_ARM_MOVE_DISTANCE)
        # 25단계: mm 기준 이동 거리 (캘리브레이션이 있을 때 위의 px 값 대신 쓴다).
        # 설정에 null을 넣으면 mm 기준을 쓰지 않고 px 값만 쓴다.
        self._arm_move_distance_mm = loaded.get("arm_move_distance_mm", DEFAULT_ARM_MOVE_DISTANCE_MM)
        self._min_move_distance_mm = loaded.get("min_move_distance_mm", DEFAULT_MIN_MOVE_DISTANCE_MM)
        self._max_jump_distance_mm = loaded.get("max_jump_distance_mm", DEFAULT_MAX_JUMP_DISTANCE_MM)
        self._hole_confirm_duration = (hole_settings or {}).get(
            "confirm_duration", DEFAULT_HOLE_CONFIRM_DURATION
        )

        # ARMED(추적 대기)용:
        # - 공이 마지막으로 시작점 근처에 가만히 있던 프레임의 시각 (추적 시작 시각이 된다)
        # - 시작점이 없을 때(궤적 초기화 직후) 대신 기준으로 삼는 '처음 본 공 위치'
        self._armed_still_time: float | None = None
        self._arm_reference: tuple[int, int] | None = None
        # 추적이 시작된(ARMED -> TRACKING) 시각. 아직 시작 전이면 None.
        self.tracking_started_at: float | None = None

        # 직전 점과 너무 멀어서(오검출로 보고) 연속으로 버린 횟수
        self._consecutive_jump_count = 0
        # add_trajectory_point가 (기록 여부와 무관하게) 매번 실제로 관측한
        # 마지막 좌표. 점프 판정 시 '기록된 마지막 점'뿐 아니라 이 값과도
        # 비교해서, 빠르게 계속 움직이는 공을 오검출로 착각하지 않게 한다.
        self._last_seen_position: tuple[int, int] | None = None
        # 정지 판정용: 마지막으로 본 원시 검출 좌표와, 그 위치 근처에서
        # 멈춰 있기 시작한(프레임 간 이동이 stop_threshold 미만이 된) 시각
        self._last_raw_position: tuple[int, int] | None = None
        self._stop_since: float | None = None
        # 이탈 판정용: 공을 못 찾기 시작한 시각
        self._lost_since: float | None = None
        # 홀인 판정용: 공이 사라진 자리가 홀컵 근처였으면 (홀 번호, 컵 중심 px), 아니면 None
        self._lost_hole: tuple | None = None

    # ------------------------------------------------------------------
    # 25단계: mm 기준 이동 거리
    # ------------------------------------------------------------------
    def _distance_px(self, point, px_value: float, mm_value) -> float:
        """이동 기준값을 이 위치(point) 근처의 px로 돌려준다.

        mm 값이 있고 엔진이 mm_to_px를 넣어 줬으며 캘리브레이션이 있으면 mm를 px로 바꾼 값,
        그렇지 않으면 설정의 px 값을 그대로 쓴다.
        """
        if mm_value is not None and self.mm_to_px is not None and point is not None:
            converted = self.mm_to_px(point, float(mm_value))
            if converted is not None:
                return converted
        return px_value

    def arm_move_threshold(self, point) -> float:
        """추적 시작(ARMED -> TRACKING)으로 보는 이동 거리(px). point = 기준 위치."""
        return self._distance_px(point, self._arm_move_distance, self._arm_move_distance_mm)

    def min_move_threshold(self, point) -> float:
        """궤적에 새 점을 기록하는 최소 이동 거리(px). point = 직전 기록점."""
        return self._distance_px(point, self._min_move_distance, self._min_move_distance_mm)

    def max_jump_threshold(self, point) -> float:
        """이 거리(px) 이상 떨어지면 오검출 의심 (궤적 패널이 선을 끊는 기준이기도 하다)."""
        return self._distance_px(point, self.max_jump_distance, self._max_jump_distance_mm)

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

    def enter_ready(self) -> bool:
        """26단계 자동 흐름: IDLE에서 바로 READY로 간다 (캘리브레이션 버튼 생략, 시작점은 아직 없음)."""
        return self._transition("auto_ready", TrackerState.READY)

    def register_start_position(self, x: int, y: int, keep_previous: bool = False) -> bool:
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
        self._clear_attempt(keep_previous)
        return True

    def start_tracking(self, keep_previous: bool = False) -> bool:
        """'추적 준비' 버튼 / 앱의 ADD 명령: ARMED(추적 대기)로 들어간다.

        바로 TRACKING이 되지 않는다. 공이 시작점에서 arm_move_distance 이상
        움직이는 순간 update_detection()이 자동으로 TRACKING으로 바꾼다.
        그 전까지(ARMED)는 정지·이탈 판정을 하지 않으므로 퍼팅 전에 공이 오래
        가만히 있어도 추적이 끝나지 않는다.

        새로운 시도를 시작하는 것이므로, 이전 시도(추적 중지 후 다시 준비한 경우)의
        궤적과 판정 타이머를 모두 정리한다.

        keep_previous=True (26단계 자동 흐름의 '다음 퍼팅 준비'): 이전 퍼팅의 궤적·결과는 화면에
        남겨 두고 타이머만 정리한다. 다음 퍼팅이 실제로 시작되는 순간(TRACK START)에
        _update_armed가 궤적을 새로 시작하므로 그때 지워진다.
        """
        if not self._transition("start_tracking", TrackerState.ARMED):
            return False
        self._clear_attempt(keep_previous)
        return True

    def _clear_attempt(self, keep_previous: bool = False) -> None:
        """한 번의 시도(퍼팅)에 관한 기록·타이머를 모두 비운다. (시작점은 건드리지 않는다)

        keep_previous=True면 화면에 보여줄 이전 결과(궤적·정지점·종료 사유·홀 번호)는 남긴다.
        """
        if not keep_previous:
            self.trajectory = []
            self.stop_position = None
            self.finish_reason = None
            self.finish_hole = None
        self.stop_started_at = None
        self.tracking_started_at = None
        self._lost_hole = None
        self._consecutive_jump_count = 0
        self._last_seen_position = None
        self._last_raw_position = None
        self._stop_since = None
        self._lost_since = None
        self._armed_still_time = None
        self._arm_reference = None

    def add_trajectory_point(self, x: int, y: int, timestamp: float) -> bool:
        """TRACKING 상태일 때, 검출된 공 좌표를 궤적에 기록한다.

        TRACKING 상태가 아니면 기록하지 않는다 (False 반환).
        직전에 '기록된' 점 기준으로:
        - min_move_distance보다 가까우면: 너무 촘촘해지지 않도록 건너뛴다.
        - max_jump_distance보다 멀면: 오검출일 수도, 공이 빠르게 계속
          움직이는 중일 수도 있다. 이번 좌표가 직전에 '실제로 관측한'
          좌표(기록 여부와 무관하게 매번 갱신됨)와는 가까우면, 공이
          부드럽게 계속 움직이는 중이라 마지막 기록점과만 멀어진 것으로
          보고 바로 받아들인다. 그렇지 않으면 오검출로 보고 건너뛰되,
          이런 값이 연속 max_consecutive_jumps번 나오면 공을 실제로
          옮긴 것으로 보고 받아들인다.
        첫 점은 비교 대상이 없으므로 무조건 기록한다.

        (이 '직전 관측 좌표'와의 비교가 없으면, 공이 빨라서 프레임마다
        max_jump_distance를 넘는 상황에서 거부될 때마다 기준점이
        갱신되지 않아 이후 프레임들도 계속 거부되고, 결국 궤적이 거의
        기록되지 않는 문제가 있었다. update_detection()의 정지/이탈
        판정은 매 프레임 기준점을 갱신하므로 이 문제와 무관하게 정상
        동작해서, "정지 감지"는 뜨는데 궤적은 비어 있는 것처럼 보였다.)
        """
        if self.state != TrackerState.TRACKING:
            return False

        if not self.trajectory:
            self._consecutive_jump_count = 0
            self._last_seen_position = (x, y)
            self.trajectory.append(TrajectoryPoint(x=x, y=y, timestamp=timestamp))
            return True

        last = self.trajectory[-1]
        distance = ((x - last.x) ** 2 + (y - last.y) ** 2) ** 0.5
        # 기준값(px)은 직전 기록점 근처에서 계산한다 (캘리브레이션이 있으면 mm 기준)
        max_jump = self.max_jump_threshold((last.x, last.y))
        min_move = self.min_move_threshold((last.x, last.y))

        if distance >= max_jump:
            consistent_with_last_seen = False
            if self._last_seen_position is not None:
                seen_x, seen_y = self._last_seen_position
                seen_distance = ((x - seen_x) ** 2 + (y - seen_y) ** 2) ** 0.5
                consistent_with_last_seen = seen_distance < max_jump

            self._last_seen_position = (x, y)

            if not consistent_with_last_seen:
                self._consecutive_jump_count += 1
                if self._consecutive_jump_count < self._max_consecutive_jumps:
                    return False  # 오검출로 보고 이번 값은 버린다
            # 직전 관측 좌표와 이어지거나(빠른 이동), 연속으로 계속 멀리
            # 떨어진 값이 나왔다(공을 실제로 옮김) - 둘 다 받아들인다.
            self._consecutive_jump_count = 0
        elif distance < min_move:
            self._last_seen_position = (x, y)
            return False
        else:
            self._consecutive_jump_count = 0
            self._last_seen_position = (x, y)

        self.trajectory.append(TrajectoryPoint(x=x, y=y, timestamp=timestamp))
        return True

    def update_detection(self, x: int | None, y: int | None, timestamp: float) -> str | None:
        """매 프레임 호출해 추적 시작(ARMED)과 정지·이탈(TRACKING)을 판정한다.

        공을 찾았으면 (x, y)를, 못 찾았으면 (None, None)을 넘긴다.
        - ARMED: 공이 시작점에서 arm_move_distance 이상 움직이면 TRACKING으로 바꾼다
          (이때는 None을 반환한다. 시작됐는지는 state나 tracking_started_at으로 안다).
        - TRACKING: 조건을 만족해 FINISHED로 전환됐으면 종료 사유
          (STOP_REASON/LOST_REASON/HOLE_REASON)를 반환하고, 아니면 내부 타이머만 갱신하고
          None을 반환한다.
        그 밖의 상태에서는 아무 것도 하지 않는다.
        """
        if self.state == TrackerState.ARMED:
            self._update_armed(x, y, timestamp)
            return None
        if self.state != TrackerState.TRACKING:
            return None

        if x is None or y is None:
            # 공을 못 찾음 -> 이탈 타이머 시작/유지
            if self._lost_since is None:
                self._lost_since = timestamp
                # 사라진 자리(마지막으로 본 위치)가 홀컵 근처인지 이때 한 번만 확인한다
                self._lost_hole = None
                if self.hole_finder is not None and self._last_raw_position is not None:
                    self._lost_hole = self.hole_finder(*self._last_raw_position)
                return None
            lost_for = timestamp - self._lost_since
            if self._lost_hole is not None and lost_for >= self._hole_confirm_duration:
                # 컵 근처에서 사라진 뒤 다시 안 나타남 -> 홀인. 끝 위치는 컵 중심
                hole_number, hole_center = self._lost_hole
                self.stop_position = (int(round(hole_center[0])), int(round(hole_center[1])))
                self.finish_hole = hole_number
                return self._finish_with_reason(HOLE_REASON)
            if lost_for >= self._lost_duration:
                return self._finish_with_reason(LOST_REASON)
            return None

        # 공을 다시 찾았으면 이탈 타이머는 리셋 (컵을 스치고 다시 나타난 경우도 여기서 추적을 이어 간다)
        self._lost_since = None
        self._lost_hole = None

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
            # 26단계: 공이 '멈추기 시작한' 시각. 자동 흐름이 '자리 고쳐 놓기'를 판단할 때 쓴다
            # (정지 판정은 stop_duration만큼 기다린 뒤에 나므로 끝난 시각보다 이 값이 정확하다).
            self.stop_started_at = self._stop_since
            return self._finish_with_reason(STOP_REASON)

        return None

    def _update_armed(self, x: int | None, y: int | None, timestamp: float) -> None:
        """ARMED 상태: 공이 시작점에서 충분히 움직였는지 확인하고, 움직였으면 추적을 시작한다.

        - 공이 안 보이는 프레임은 무시한다 (퍼터·손이 가렸을 수 있다). 이탈 판정도 하지 않는다.
        - 기준 위치는 등록된 시작점이다. 시작점이 없으면(궤적 초기화 직후)
          처음 본 공 위치를 기준으로 삼는다.
        - 기준에서 arm_move_distance 미만이면 '아직 가만히 있음'으로 보고 그 시각을
          기억해 둔다. 움직인 순간의 추적 시작 시각은 이 마지막 '가만히 있던' 시각이다.
        """
        if x is None or y is None:
            return

        reference = self.start_position
        if reference is None:
            if self._arm_reference is None:
                self._arm_reference = (x, y)
            reference = self._arm_reference

        distance = ((x - reference[0]) ** 2 + (y - reference[1]) ** 2) ** 0.5
        if distance < self.arm_move_threshold(reference):
            self._armed_still_time = timestamp
            return

        # 공이 움직였다 -> TRACKING 시작.
        # 추적 시작 시각은 마지막으로 가만히 있던 프레임 (그런 프레임이 없었으면 지금).
        started_at = self._armed_still_time if self._armed_still_time is not None else timestamp
        if not self._transition("begin_tracking", TrackerState.TRACKING):
            return
        self.tracking_started_at = started_at
        self.stop_position = None
        self.finish_reason = None
        self.finish_hole = None
        self._stop_since = None
        self._lost_since = None
        self._lost_hole = None
        self._consecutive_jump_count = 0
        # 궤적은 '출발 위치(가만히 있던 마지막 시각)'와 '지금 위치' 두 점으로 시작한다.
        # (이번 프레임의 add_trajectory_point는 ARMED였을 때 불려서 기록되지 않았으므로
        # 여기서 직접 넣는다.)
        self.trajectory = [
            TrajectoryPoint(x=reference[0], y=reference[1], timestamp=started_at),
            TrajectoryPoint(x=x, y=y, timestamp=timestamp),
        ]
        self._last_seen_position = (x, y)
        self._last_raw_position = (x, y)

    @property
    def lost_hole(self):
        """공이 홀컵 근처에서 사라져 홀인 확인을 기다리는 중이면 (홀 번호, 컵 중심 px), 아니면 None."""
        return self._lost_hole if self._lost_since is not None else None

    @property
    def lost_since(self) -> float | None:
        """추적 중 공이 안 보이기 시작한 시각 (보이고 있으면 None)."""
        return self._lost_since

    def hole_confirm_due(self, now: float) -> bool:
        """홀인 확인 시간(confirm_duration)이 지났는지. 홀인 후보가 아니면 False."""
        return self.lost_hole is not None and now - self._lost_since >= self._hole_confirm_duration

    @property
    def last_seen_position(self):
        """추적 중 마지막으로 공을 본 위치 (px). 없으면 None."""
        return self._last_raw_position

    def finish_at_rim(self, x: float, y: float) -> str | None:
        """26단계(25단계 보완): 홀인 확인 중, 공이 컵 테두리에 붙어 멈춘 것으로 판단됐을 때 '정지'로 끝낸다.

        공이 컵과 한 덩어리로 붙어 보여서 '사라진' 것처럼 보였을 뿐이므로, 홀인이 아니라
        정지(STOP)이고 끝 위치는 엔진이 계산해 준 공 위치(x, y)다.
        """
        if self.state != TrackerState.TRACKING:
            return None
        self.stop_position = (int(round(x)), int(round(y)))
        self.stop_started_at = self._lost_since
        self.finish_hole = None
        return self._finish_with_reason(STOP_REASON)

    def _finish_with_reason(self, reason: str) -> str:
        self.finish_tracking()
        self.finish_reason = reason
        return reason

    def stop_tracking(self) -> bool:
        """'추적 중지' 버튼 / 앱의 ABORT: 대기(ARMED)나 추적(TRACKING)을 수동으로 중지하고 READY로 되돌아간다."""
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
        self._clear_attempt()
        return True
