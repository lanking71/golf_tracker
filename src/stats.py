"""
궤적 요약 통계 계산 모듈.

기록된 궤적에서 총 이동 거리·소요 시간·평균/최고 속도·직진성을
계산한다. 좌표의 단위는 신경 쓰지 않는다 - 호출하는 쪽이 픽셀 좌표를
넘기면 px 단위 통계가 나오고, 캘리브레이션된 실제 좌표(mm)를 넘기면
mm 단위 통계가 나온다 (10단계부터 MainWindow는 캘리브레이션이 됐으면
mm 좌표를 넘기고, 그 결과를 다시 cm로 바꿔서 화면에 보여준다).
"""

from dataclasses import dataclass

from src.tracker import TrajectoryPoint


@dataclass
class TrajectoryStats:
    """궤적 요약 통계. 거리·속도 단위는 입력으로 넘긴 좌표의 단위를 그대로 따른다."""

    total_distance: float
    duration: float
    average_speed: float
    max_speed: float
    straightness: float  # 0~1, 1에 가까울수록 직선에 가깝게 굴러간 것


def compute_trajectory_stats(
    trajectory: list[TrajectoryPoint],
    start_position: tuple[float, float] | None,
    stop_position: tuple[float, float] | None,
) -> TrajectoryStats | None:
    """궤적 데이터로 요약 통계를 계산한다.

    기록된 점이 2개 미만이면(거리를 계산할 수 없음) None을 반환한다.
    """
    if len(trajectory) < 2:
        return None

    total_distance = 0.0
    max_speed = 0.0
    for prev, curr in zip(trajectory, trajectory[1:]):
        segment_distance = ((curr.x - prev.x) ** 2 + (curr.y - prev.y) ** 2) ** 0.5
        segment_time = curr.timestamp - prev.timestamp
        total_distance += segment_distance
        if segment_time > 0:
            max_speed = max(max_speed, segment_distance / segment_time)

    duration = trajectory[-1].timestamp - trajectory[0].timestamp
    average_speed = total_distance / duration if duration > 0 else 0.0

    # 직진성: 시작점 -> 정지점 직선 거리 / 실제로 이동한 총 거리.
    # 시작점/정지점이 따로 없으면(예: 화면 이탈) 궤적의 첫 점/마지막 점으로 대신한다.
    start = start_position if start_position is not None else (trajectory[0].x, trajectory[0].y)
    end = stop_position if stop_position is not None else (trajectory[-1].x, trajectory[-1].y)
    straight_line = ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5
    if total_distance > 0:
        # 부동소수 오차로 1을 살짝 넘는 것을 방지
        straightness = min(straight_line / total_distance, 1.0)
    else:
        straightness = 0.0

    return TrajectoryStats(
        total_distance=total_distance,
        duration=duration,
        average_speed=average_speed,
        max_speed=max_speed,
        straightness=straightness,
    )
