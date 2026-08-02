"""
궤적 요약 통계 계산 모듈.

기록된 궤적(Tracker.trajectory)에서 총 이동 거리·소요 시간·평균/최고
속도·직진성을 계산한다.

지금은 픽셀 좌표를 그대로 쓰지만(px, px/초), 9~10단계에서 매트
캘리브레이션이 들어오면 실제 거리 단위(mm 등)로 바꿔야 한다. 그때
가서 이 파일의 계산 로직을 건드리지 않고, pixels_per_unit(1픽셀이
실제로 몇 mm인지)만 넘기면 되도록 단위 변환을 처음부터 분리해뒀다.
"""

from dataclasses import dataclass

from src.tracker import TrajectoryPoint


@dataclass
class TrajectoryStats:
    """궤적 요약 통계.

    pixels_per_unit을 기본값(1.0)으로 계산하면 픽셀 단위 그대로이고,
    캘리브레이션 이후 실제 mm당 픽셀 값을 넘기면 total_distance·
    average_speed·max_speed가 그 단위로 바뀐다. duration(초)과
    straightness(비율)는 단위 변환과 무관하다.
    """

    total_distance: float
    duration: float
    average_speed: float
    max_speed: float
    straightness: float  # 0~1, 1에 가까울수록 직선에 가깝게 굴러간 것


def compute_trajectory_stats(
    trajectory: list[TrajectoryPoint],
    start_position: tuple[int, int] | None,
    stop_position: tuple[int, int] | None,
    pixels_per_unit: float = 1.0,
) -> TrajectoryStats | None:
    """궤적 데이터로 요약 통계를 계산한다.

    기록된 점이 2개 미만이면(거리를 계산할 수 없음) None을 반환한다.
    """
    if len(trajectory) < 2:
        return None

    total_distance_px = 0.0
    max_speed_px = 0.0
    for prev, curr in zip(trajectory, trajectory[1:]):
        segment_distance = ((curr.x - prev.x) ** 2 + (curr.y - prev.y) ** 2) ** 0.5
        segment_time = curr.timestamp - prev.timestamp
        total_distance_px += segment_distance
        if segment_time > 0:
            max_speed_px = max(max_speed_px, segment_distance / segment_time)

    duration = trajectory[-1].timestamp - trajectory[0].timestamp
    average_speed_px = total_distance_px / duration if duration > 0 else 0.0

    # 직진성: 시작점 -> 정지점 직선 거리 / 실제로 이동한 총 거리.
    # 시작점/정지점이 따로 없으면(예: 화면 이탈) 궤적의 첫 점/마지막 점으로 대신한다.
    start = start_position if start_position is not None else (trajectory[0].x, trajectory[0].y)
    end = stop_position if stop_position is not None else (trajectory[-1].x, trajectory[-1].y)
    straight_line_px = ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5
    if total_distance_px > 0:
        # 부동소수 오차로 1을 살짝 넘는 것을 방지
        straightness = min(straight_line_px / total_distance_px, 1.0)
    else:
        straightness = 0.0

    scale = pixels_per_unit if pixels_per_unit else 1.0
    return TrajectoryStats(
        total_distance=total_distance_px / scale,
        duration=duration,
        average_speed=average_speed_px / scale,
        max_speed=max_speed_px / scale,
        straightness=straightness,
    )
