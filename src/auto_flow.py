"""
자동 흐름 모듈 (26단계): 버튼 없이 "공 놓기 → 퍼팅 → 결과 → 공 다시 놓기"를 반복한다.

예전에는 퍼팅 한 번에 버튼을 세 번 눌러야 했다: "캘리브레이션" → "시작 위치 등록" → "추적 준비".
이제는 이렇게 동작한다.

    지금 상태              조건                                         자동 동작
    IDLE                   저장된 캘리브레이션이 유효                   바로 READY (캘리브레이션 버튼 생략)
    READY                  시작 구역 안에 공이 settle_duration초 가만히  시작점 등록 → ARMED
    ARMED                  공이 arm_move_distance 이상 움직임           TRACKING (예전과 같음, src/tracker.py)
    TRACKING → 정지        짧게(min_putt_distance_mm 미만) 움직이고      퍼팅 아님(자리 고쳐 놓기): 결과를 버리고
                           readdress_timeout초 안에 멈춤                 다시 ARMED (멈춘 곳이 시작 구역 밖이면 READY)
    TRACKING → 끝          정지 / 홀인 / 이탈                            FINISHED (예전과 같음) + 결과 자동 저장
    FINISHED               공을 집었다가(또는 움직였다가) 시작 구역에      다음 퍼팅 준비 → ARMED
                           다시 settle_duration초 가만히 놓음            (이전 궤적은 다음 퍼팅이 시작될 때까지 남김)
    ARMED                  공이 ball_gone_duration초 넘게 안 보임        시작점을 지우고 READY (공을 집어 감)

이 모듈은 '무엇을 할지'만 판단한다 (화면·카메라·Qt를 모른다). 실제 상태 전환과 앱 알림은
엔진(src/engine.py)이 이 판단 결과(AutoAction)를 받아서 한다. 그래서 테스트하기 쉽다.

거리는 모두 매트 mm 좌표로 판단한다 (캘리브레이션이 없으면 자동 흐름은 쉰다).

'다시 놓기' 판단 (release):
    FINISHED 직후에 공이 시작 구역 안에서 멈춰 있을 수 있다 (아주 짧은 퍼팅). 그 공을 '새로 놓인
    공'으로 보면 사람이 아무것도 안 했는데 바로 다음 준비가 돼 버린다. 그래서 끝난 뒤에는
    "공이 한 번 사라지거나(집어 감, RELEASE_GONE_S초 이상) 멈춘 자리에서 RELEASE_MOVE_MM 넘게
    움직였을 때"만 다음 준비를 받는다. 공이 시작 구역 밖에서 멈췄으면 그 공은 시작 구역 검색에
    보이지 않으므로 처음부터 '놓을 준비가 된' 것으로 본다.
    사용자가 '추적 중지'·'궤적 초기화' 버튼(또는 앱의 ABORT/RESET)을 눌렀을 때도 같은 규칙을 쓴다.
    그래야 버튼을 누르자마자 자동 흐름이 다시 준비해 버리지 않는다 (버튼 동작이 우선).
"""

import math
from dataclasses import dataclass

from src.tracker import STOP_REASON, TrackerState

# config/settings.json의 "auto" 항목에 값이 없을 때 쓰는 기본값
DEFAULT_AUTO_SETTINGS = {
    "enabled": True,  # 자동 흐름 켜기/끄기
    "start_zone": None,  # 저장된 시작 구역 중심 {"x_mm":.., "y_mm":..} (현장 파일에 저장)
    "start_zone_radius_mm": 300,  # 시작 구역 반경
    "anywhere": False,  # True면 매트 위 어디든(홀컵 주변 제외) 공이 멈추면 시작점으로 본다
    "settle_duration": 1.0,  # 공이 이 시간(초) 동안
    "settle_tolerance_mm": 5,  # 이 거리 안에서 가만히 있어야 "놓였다"
    "min_putt_distance_mm": 100,  # 이보다 짧게 움직이고 멈추면 퍼팅이 아니라 '자리 고쳐 놓기'
    "readdress_timeout": 1.5,  # 움직이기 시작해서 이 시간(초) 안에 멈춰야 '자리 고쳐 놓기'
    "ball_gone_duration": 3.0,  # 추적 대기 중 공이 이 시간(초) 넘게 안 보이면 대기를 푼다
    "save_results": True,  # 자동 모드에서 결과를 DB에 자동 저장
}

# 끝난 뒤 '공을 집어 갔다'고 보는 시간(초): 이만큼 연속으로 안 보이면 다시 놓을 수 있다
RELEASE_GONE_S = 0.3
# 끝난 뒤 '공을 움직였다'고 보는 거리(mm): 멈춘 자리에서 이만큼 넘게 움직이면 다시 놓을 수 있다
RELEASE_MOVE_MM = 20.0
# anywhere 모드에서 홀컵 주변 이 거리(mm) 안은 시작점으로 보지 않는다 (컵에 들어간 공·컵 옆 공)
HOLE_EXCLUDE_MM = 150.0

# 자동 동작 종류
ACTION_READY = "ready"  # IDLE → READY
ACTION_ARM = "arm"  # 시작점 등록 + ARMED
ACTION_DROP = "drop"  # 공을 집어 감: 시작점을 지우고 READY


def auto_settings(loaded: dict | None) -> dict:
    """설정 파일의 auto 항목과 기본값을 합친다."""
    return {**DEFAULT_AUTO_SETTINGS, **(loaded or {})}


@dataclass
class AutoAction:
    """엔진에게 '이걸 하라'고 알려 주는 판단 결과."""

    kind: str  # ACTION_READY / ACTION_ARM / ACTION_DROP
    position_px: tuple | None = None  # ACTION_ARM: 시작점으로 등록할 공 위치 (px)
    position_mm: tuple | None = None  # 같은 위치 (mm, 로그·앱 알림용)
    keep_previous: bool = False  # ACTION_ARM: 이전 퍼팅의 궤적·결과를 화면에 남길지 (FINISHED에서 올 때)


def _distance(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


class AutoFlow:
    """매 프레임 지금 상태와 공 위치를 보고 자동으로 할 일을 정한다."""

    def __init__(self, settings: dict | None = None):
        merged = auto_settings(settings)
        self.enabled = bool(merged["enabled"])
        zone = merged.get("start_zone")
        self.start_zone: tuple[float, float] | None = None
        if isinstance(zone, dict) and zone.get("x_mm") is not None and zone.get("y_mm") is not None:
            self.start_zone = (float(zone["x_mm"]), float(zone["y_mm"]))
        self.zone_radius_mm = float(merged["start_zone_radius_mm"])
        self.anywhere = bool(merged["anywhere"])
        self.settle_duration = float(merged["settle_duration"])
        self.settle_tolerance_mm = float(merged["settle_tolerance_mm"])
        self.min_putt_distance_mm = float(merged["min_putt_distance_mm"])
        self.readdress_timeout = float(merged["readdress_timeout"])
        self.ball_gone_duration = float(merged["ball_gone_duration"])
        self.save_results = bool(merged["save_results"])
        self.reset()

    def reset(self) -> None:
        """기억한 판단 상태를 모두 지운다."""
        # '가만히 있음' 판단: 기준 위치(mm)와 그 자리에 있기 시작한 시각
        self._settle_anchor: tuple[float, float] | None = None
        self._settle_since: float | None = None
        # 다시 놓기 대기 (끝난 뒤·버튼 조작 뒤): True면 공을 집거나 움직여야 다음 준비를 받는다
        self.hold = False
        self._hold_reference: tuple[float, float] | None = None
        self._gone_since: float | None = None  # 공이 안 보이기 시작한 시각 (다시 놓기 / 공 집어 감 판단)

    # ------------------------------------------------------------------
    # 시작 구역
    # ------------------------------------------------------------------
    def in_zone(self, ball_mm, zone_mm, start_mm=None) -> bool:
        """공이 시작 구역(저장 구역 또는 앱이 정한 어드레스, 또는 지금 시작점) 반경 안인지."""
        for center in (zone_mm, start_mm):
            if center is not None and _distance(ball_mm, center) <= self.zone_radius_mm:
                return True
        return False

    def eligible(self, ball_mm, zone_mm, start_mm, inside_mat: bool, holes_mm) -> bool:
        """이 위치에 놓인 공을 시작점으로 받아도 되는지."""
        if self.in_zone(ball_mm, zone_mm, start_mm):
            return True
        if self.anywhere and inside_mat:
            # 매트 위 어디든 되지만, 홀컵 근처는 안 된다 (컵에 들어간 공, 컵 옆에 멈춘 공을 시작점으로 잡지 않게)
            return all(_distance(ball_mm, hole) > HOLE_EXCLUDE_MM for hole in holes_mm)
        return False

    def can_wait_for_ball(self, zone_mm) -> bool:
        """공을 기다릴 곳이 있는지 (시작 구역도 없고 anywhere도 아니면 사용자가 한 번 등록해야 한다)."""
        return zone_mm is not None or self.anywhere

    # ------------------------------------------------------------------
    # '가만히 있음' / '다시 놓기' / '공 집어 감'
    # ------------------------------------------------------------------
    def _update_settle(self, ball_mm, now: float) -> bool:
        """공이 settle_duration 동안 settle_tolerance_mm 안에 계속 있었는지.

        같은 자리에서 '연속으로' 검출될 때만 센다. 한 프레임이라도 못 찾거나 기준에서 벗어나면 처음부터
        다시 센다 (공을 놓는 손·발이 지나가며 잠깐 생긴 덩어리를 공으로 오해하지 않게).
        """
        if ball_mm is None:
            self._settle_anchor = None
            self._settle_since = None
            return False
        if self._settle_anchor is None or _distance(ball_mm, self._settle_anchor) > self.settle_tolerance_mm:
            self._settle_anchor = ball_mm
            self._settle_since = now
            return False
        return now - self._settle_since >= self.settle_duration

    def hold_until_released(self, reference_mm) -> None:
        """'다시 놓기' 대기를 건다. reference_mm: 지금 공이 있는 자리 (모르면 None = 공이 사라지면 풀림)."""
        self.hold = True
        self._hold_reference = reference_mm
        self._gone_since = None
        self._settle_anchor = None
        self._settle_since = None

    def _update_release(self, ball_mm, now: float) -> None:
        """다시 놓기 대기 중: 공이 사라졌거나(집어 감) 멈춘 자리에서 움직였으면 대기를 푼다."""
        if ball_mm is None:
            if self._gone_since is None:
                self._gone_since = now
            elif now - self._gone_since >= RELEASE_GONE_S:
                self.hold = False
            return
        self._gone_since = None
        if self._hold_reference is None or _distance(ball_mm, self._hold_reference) > RELEASE_MOVE_MM:
            self.hold = False

    # ------------------------------------------------------------------
    # 매 프레임 판단
    # ------------------------------------------------------------------
    def step(
        self,
        state: TrackerState,
        calibrated: bool,
        has_start: bool,
        ball_px,
        ball_mm,
        now: float,
        zone_mm=None,
        start_mm=None,
        inside_mat: bool = True,
        holes_mm=(),
    ) -> AutoAction | None:
        """이번 프레임에 자동으로 할 일을 돌려준다 (없으면 None).

        ball_px / ball_mm: 이번 프레임에 찾은 공 위치 (없으면 None). 검색 영역이 시작 구역으로
            좁혀져 있으면 그 안의 공만 들어온다.
        zone_mm: 시작 구역 중심 (앱의 어드레스가 있으면 그것, 없으면 저장된 구역). start_mm: 지금 시작점.
        """
        if not self.enabled or not calibrated:
            return None

        if state == TrackerState.IDLE:
            # 저장된 캘리브레이션이 유효 → 캘리브레이션 버튼 없이 바로 준비 상태로
            return AutoAction(ACTION_READY)

        if state == TrackerState.ARMED:
            # 퍼터·몸이 공을 잠깐 가려도 대기는 유지한다. ball_gone_duration 넘게 안 보이면 공을 집어 간 것
            if ball_mm is None:
                if self._gone_since is None:
                    self._gone_since = now
                elif now - self._gone_since > self.ball_gone_duration:
                    self._gone_since = None
                    return AutoAction(ACTION_DROP)
            else:
                self._gone_since = None
            return None

        if state in (TrackerState.READY, TrackerState.FINISHED):
            if self.hold:
                self._update_release(ball_mm, now)
                if self.hold:
                    return None
            settled = self._update_settle(ball_mm, now)
            if not settled:
                return None
            if not self.eligible(ball_mm, zone_mm, start_mm if has_start else None, inside_mat, holes_mm):
                return None
            self._settle_anchor = None
            self._settle_since = None
            return AutoAction(
                ACTION_ARM,
                position_px=ball_px,
                position_mm=ball_mm,
                keep_previous=state == TrackerState.FINISHED,
            )

        # CALIBRATING(모서리를 찍는 중), TRACKING: 자동으로 할 일 없음
        self._gone_since = None
        return None

    def is_readdress(self, reason: str, max_distance_mm: float, tracking_started_at, stop_started_at) -> bool:
        """방금 끝난 추적이 퍼팅이 아니라 '자리 고쳐 놓기'였는지.

        조건: 정지로 끝났고, 시작점에서 가장 멀리 간 거리가 min_putt_distance_mm 미만이고,
        움직이기 시작해서 readdress_timeout초 안에 멈추기 시작했다.
        (홀인·화면 이탈은 퍼팅으로 본다)
        """
        if not self.enabled or reason != STOP_REASON:
            return False
        if max_distance_mm >= self.min_putt_distance_mm:
            return False
        if tracking_started_at is None or stop_started_at is None:
            return False
        return stop_started_at - tracking_started_at <= self.readdress_timeout

    def to_settings(self) -> dict:
        """현장 파일에 저장할 auto 항목 (지금 값)."""
        return {
            "enabled": self.enabled,
            "start_zone": (
                {"x_mm": round(self.start_zone[0], 1), "y_mm": round(self.start_zone[1], 1)}
                if self.start_zone is not None
                else None
            ),
            "start_zone_radius_mm": self.zone_radius_mm,
            "anywhere": self.anywhere,
            "settle_duration": self.settle_duration,
            "settle_tolerance_mm": self.settle_tolerance_mm,
            "min_putt_distance_mm": self.min_putt_distance_mm,
            "readdress_timeout": self.readdress_timeout,
            "ball_gone_duration": self.ball_gone_duration,
            "save_results": self.save_results,
        }
