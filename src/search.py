"""
공 검색 영역(ROI) 계획 모듈 (24단계).

예전에는 매 프레임 화면 전체에서 공을 찾았다. 그러면
- 공과 비슷한 다른 물체(예: 흰 컵)를 공으로 잡기 쉽고 (잡음에 약함)
- 화면 전체를 처리하니 느리다.
그래서 "공이 있을 만한 곳" 주변만 찾는다. 이 모듈은 매 프레임 '어디를 찾을지'를 정하고,
찾은 후보가 '공이 갈 만한 곳'에 있는지 확인한다. 실제 색 검출은 src/detector.py가 한다.

상태별 검색 영역 (반경은 매트 위 mm를 캘리브레이션으로 픽셀로 바꿔 쓴다):

    IDLE / CALIBRATING / FINISHED  전체 화면
    READY (시작점 없음)            전체 화면. 앱이 어드레스(SET_ADDR)를 정했으면 그 주변
    READY (시작점 있음)            시작점 주변 idle_radius_mm. 공이 안 보이는 프레임이
                                   lost_frames_full번 넘게 이어지면 전체 화면
                                   (공을 다른 곳에 옮겨 놓고 시작점을 다시 등록할 수 있게)
    ARMED                          시작점 주변 idle_radius_mm (전체로 넓히지 않음: 다른 물체를
                                   공으로 잡으면 퍼팅 시작으로 잘못 판단하기 때문)
    TRACKING                       예상 위치 = 직전 위치 + 직전 속도 x 지난 시간.
                                   반경 = track_radius_mm + 속도에 비례한 여유.
                                   공을 놓친 프레임마다 반경을 1.5배로 키우고,
                                   lost_frames_full번 넘게 놓치면 전체 화면

TRACKING 중 공을 놓쳤다가(반경을 넓힌 상태) 다시 찾은 후보는, 예상 진행 방향과
거리가 맞는지 확인하고(accept) 맞지 않으면 버린다. 넓은 영역에서 엉뚱한 물체를
공으로 잡지 않기 위해서다.

검색 영역 계산은 전부 처리 프레임(전처리 후) 픽셀 좌표 기준이다.
"""

import math
from dataclasses import dataclass

from src.tracker import TrackerState

# config/settings.json의 "search" 항목에 값이 없을 때 쓸 기본값
DEFAULT_SEARCH_SETTINGS = {
    "enabled": True,
    "idle_radius_mm": 150,  # 시작점·어드레스 주변 반경
    "track_radius_mm": 120,  # 추적 중 예상 위치 주변 기본 반경
    "lost_frames_full": 10,  # 이 횟수보다 많이 연속으로 놓치면 전체 화면 검색
}
# 캘리브레이션이 없어서 mm를 픽셀로 바꿀 수 없을 때 쓰는 픽셀 반경
DEFAULT_IDLE_RADIUS_PX = 150
DEFAULT_TRACK_RADIUS_PX = 120
# 공을 놓친 프레임마다 반경을 몇 배로 키울지
LOST_GROWTH = 1.5
# 속도에 비례한 여유: 반경에 (속도 x 지난 시간 x 이 값)을 더한다.
# 예상 위치는 '같은 속도로 직진'으로 계산하므로, 공이 감속하거나 휘면 그만큼 어긋난다.
SPEED_MARGIN_RATIO = 0.5
# 속도를 갱신할 때 새 값의 비율 (지수 이동 평균: 검출 좌표가 1~2px 흔들려도 속도가 튀지 않게)
VELOCITY_SMOOTHING = 0.6
# 두 검출 사이 간격이 이보다 길면(초) 속도 계산에 쓰지 않는다 (오래된 위치라 믿을 수 없음)
MAX_VELOCITY_GAP_S = 0.3
# 방향 검사: 공이 이 속도(px/s)보다 빠를 때만 진행 방향을 따진다 (거의 멈춘 공은 방향이 의미 없음)
DIRECTION_CHECK_MIN_SPEED = 60.0
# 방향 검사: 직전 위치 -> 후보 방향과 진행 방향 사이 각도의 코사인이 이 값 이상이어야 한다 (약 60도 이내)
DIRECTION_MIN_COS = 0.5
# 방향 검사 예외: 직전 위치에서 기본 반경의 이 비율 안이면 방향과 상관없이 받아들인다
# (멈추는 중인 공은 좌표가 몇 픽셀 뒤로 흔들릴 수 있다)
NEAR_LAST_RATIO = 0.25


# '아무 데도 찾지 않음'을 뜻하는 검색 영역 (폭·높이 0)
EMPTY_ROI = (0, 0, 0, 0)


@dataclass
class SearchPlan:
    """이번 프레임에서 어디를 찾을지. roi가 None이면 화면 전체."""

    roi: tuple[int, int, int, int] | None  # (x, y, 폭, 높이) 처리 프레임 좌표
    center: tuple[float, float] | None = None  # 검색 중심 (예상 위치). 후보가 여럿이면 여기 가까운 것
    radius: float | None = None  # 검색 반경(px)
    reason: str = "full"  # 왜 이 영역인지 (화면 표시·로그용): full / start / addr / track / lost
    widened: bool = False  # 공을 놓쳐서 넓힌 상태인지 (이때 찾은 후보는 방향 검사를 한다)
    base_radius: float | None = None  # 넓히기 전 기본 반경(px) - 방향 검사의 '바로 근처' 기준


def search_settings(loaded: dict | None) -> dict:
    """설정 파일의 search 항목과 기본값을 합친다 (값이 없어도 동작하게)."""
    return {**DEFAULT_SEARCH_SETTINGS, **(loaded or {})}


class SearchPlanner:
    """매 프레임 검색 영역을 정하고, 공의 위치·속도를 기억하는 클래스."""

    def __init__(self, settings: dict | None = None):
        merged = search_settings(settings)
        self.enabled = bool(merged["enabled"])
        self.idle_radius_mm = float(merged["idle_radius_mm"])
        self.track_radius_mm = float(merged["track_radius_mm"])
        self.lost_frames_full = int(merged["lost_frames_full"])
        self.reset()

    def reset(self) -> None:
        """기억한 위치·속도를 모두 지운다 (새 퍼팅을 시작할 때 등)."""
        self.last_position: tuple[float, float] | None = None  # 마지막으로 공을 찾은 위치(px)
        self.last_time: float | None = None  # 그 시각
        self.velocity: tuple[float, float] = (0.0, 0.0)  # 속도(px/초)
        self.lost_frames = 0  # 연속으로 공을 못 찾은 프레임 수
        self._last_state: TrackerState | None = None

    # ------------------------------------------------------------------
    # 검색 영역 정하기
    # ------------------------------------------------------------------
    def plan(self, state, start_position, addr_position, frame_size, now: float, to_px_radius) -> SearchPlan:
        """이번 프레임의 검색 영역을 정한다.

        state: 지금 Tracker 상태
        start_position: 등록된 시작점(px) 또는 None
        addr_position: 앱이 정한 어드레스 위치(px로 바꾼 값) 또는 None
        frame_size: (폭, 높이)
        to_px_radius(center, radius_mm, default_px): 매트 위 mm 반경을 그 위치 근처의 픽셀 반경으로
            바꾸는 함수 (캘리브레이션이 없으면 default_px를 돌려준다)
        """
        if state != self._last_state:
            # 상태가 바뀌면 놓친 횟수는 새로 센다. (ARMED -> TRACKING일 때 위치·속도는 이어서 쓴다)
            if state not in (TrackerState.ARMED, TrackerState.TRACKING):
                self.last_position = None
                self.last_time = None
                self.velocity = (0.0, 0.0)
            self.lost_frames = 0
            self._last_state = state

        if not self.enabled or frame_size is None:
            return SearchPlan(None)

        if state == TrackerState.TRACKING and self.last_position is not None:
            return self._plan_tracking(frame_size, now, to_px_radius)

        if state in (TrackerState.READY, TrackerState.ARMED):
            center, reason = None, ""
            if start_position is not None:
                center, reason = (float(start_position[0]), float(start_position[1])), "start"
            elif addr_position is not None and state == TrackerState.READY:
                center, reason = (float(addr_position[0]), float(addr_position[1])), "addr"
            if center is not None:
                # READY에서는 공을 계속 못 찾으면 전체로 넓힌다 (공을 옮겨 놓고 다시 등록할 수 있게)
                if state == TrackerState.READY and self.lost_frames > self.lost_frames_full:
                    return SearchPlan(None, reason="full")
                radius = to_px_radius(center, self.idle_radius_mm, DEFAULT_IDLE_RADIUS_PX)
                return SearchPlan(_square_roi(center, radius, frame_size), center, radius, reason)

        return SearchPlan(None)

    def _plan_tracking(self, frame_size, now: float, to_px_radius) -> SearchPlan:
        """추적 중: 예상 위치 주변. 놓친 횟수만큼 반경을 키우고, 너무 많이 놓치면 전체."""
        elapsed = max(0.0, now - (self.last_time if self.last_time is not None else now))
        vx, vy = self.velocity
        # 예상 위치 = 직전 위치 + 속도 x 지난 시간 (같은 속도로 직진한다고 가정)
        predicted = (self.last_position[0] + vx * elapsed, self.last_position[1] + vy * elapsed)
        speed = math.hypot(vx, vy)
        base = to_px_radius(self.last_position, self.track_radius_mm, DEFAULT_TRACK_RADIUS_PX)
        base_radius = base
        radius = base + speed * elapsed * SPEED_MARGIN_RATIO
        widened = self.lost_frames > 0
        radius *= LOST_GROWTH ** self.lost_frames
        if self.lost_frames > self.lost_frames_full:
            # 너무 오래 놓침: 전체 화면에서 찾는다 (그래도 방향 검사는 한다)
            return SearchPlan(None, predicted, None, "lost", widened=True, base_radius=base_radius)
        reason = "lost" if widened else "track"
        roi = _square_roi(predicted, radius, frame_size)
        if roi is None:
            # 예상 위치가 화면 밖으로 완전히 나감: 아무 데도 찾지 않는다 (전체 검색으로 바꾸면
            # 화면 안의 엉뚱한 물체를 공으로 잡을 수 있다). 계속 놓치면 위의 전체 검색으로 넘어간다.
            roi = EMPTY_ROI
        return SearchPlan(roi, predicted, radius, reason, widened, base_radius)

    # ------------------------------------------------------------------
    # 후보 확인 + 결과 기억
    # ------------------------------------------------------------------
    def accept(self, plan: SearchPlan, x: float, y: float) -> bool:
        """넓힌 영역에서 찾은 후보가 공이 갈 만한 곳인지 확인한다. 아니면 False (버림).

        - 평소(넓히지 않음) 또는 추적 중이 아니면 항상 True
        - 예상 위치에서 검색 반경보다 멀면 False (전체 검색일 때는 반경 검사 생략)
        - 공이 움직이고 있었다면, 직전 위치 -> 후보 방향이 진행 방향과 대략 같아야 한다 (약 60도 이내)
          단, 직전 위치 바로 근처(기본 반경의 25% 안)는 방향과 상관없이 받아들인다 (멈추는 중일 수 있음)
        """
        if not plan.widened or self.last_position is None:
            return True
        if plan.center is not None and plan.radius is not None:
            if math.hypot(x - plan.center[0], y - plan.center[1]) > plan.radius:
                return False
        vx, vy = self.velocity
        speed = math.hypot(vx, vy)
        if speed < DIRECTION_CHECK_MIN_SPEED:
            return True
        dx, dy = x - self.last_position[0], y - self.last_position[1]
        distance = math.hypot(dx, dy)
        if distance == 0:
            return True
        if plan.base_radius is not None and distance <= plan.base_radius * NEAR_LAST_RATIO:
            return True
        cos = (dx * vx + dy * vy) / (distance * speed)
        return cos >= DIRECTION_MIN_COS

    def observe(self, position, now: float) -> None:
        """이번 프레임 결과를 기억한다. position은 공 위치(px) 또는 None(못 찾음)."""
        if position is None:
            self.lost_frames += 1
            return
        x, y = float(position[0]), float(position[1])
        if self.last_position is not None and self.last_time is not None:
            gap = now - self.last_time
            if 0 < gap <= MAX_VELOCITY_GAP_S:
                new_v = ((x - self.last_position[0]) / gap, (y - self.last_position[1]) / gap)
                if self.lost_frames > 0:
                    # 놓쳤다가 다시 찾은 경우는 그 사이 평균 속도라 그대로 쓴다
                    self.velocity = new_v
                else:
                    a = VELOCITY_SMOOTHING
                    self.velocity = (
                        a * new_v[0] + (1 - a) * self.velocity[0],
                        a * new_v[1] + (1 - a) * self.velocity[1],
                    )
            elif gap > MAX_VELOCITY_GAP_S:
                self.velocity = (0.0, 0.0)
        self.last_position = (x, y)
        self.last_time = now
        self.lost_frames = 0


def _square_roi(center, radius: float, frame_size) -> tuple[int, int, int, int] | None:
    """중심·반경으로 정사각형 검색 영역을 만들고 화면 안으로 자른다. 화면과 겹치지 않으면 None(전체)."""
    width, height = frame_size
    r = max(1.0, float(radius))
    x0 = max(0, int(math.floor(center[0] - r)))
    y0 = max(0, int(math.floor(center[1] - r)))
    x1 = min(width, int(math.ceil(center[0] + r)))
    y1 = min(height, int(math.ceil(center[1] + r)))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return (x0, y0, x1 - x0, y1 - y0)
