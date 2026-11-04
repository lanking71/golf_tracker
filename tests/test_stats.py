"""
궤적 통계 계산(src/stats.py) 테스트. 카메라·Qt 없이 순수 파이썬으로 실행된다.
"""

import unittest

from src.stats import compute_point_series, compute_trajectory_stats
from src.tracker import TrajectoryPoint


def points(*items):
    """(x, y, 시각) 목록을 TrajectoryPoint 목록으로 만든다."""
    return [TrajectoryPoint(x=x, y=y, timestamp=t) for x, y, t in items]


class PointSeriesTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(compute_point_series([]), [])

    def test_single_point(self):
        self.assertEqual(compute_point_series(points((5, 6, 10.0))), [(5, 6, 0.0, 0.0, 0.0)])

    def test_cumulative_distance_and_speed(self):
        # (0,0) -> (3,4): 거리 5, 1초 -> 속도 5 / (3,4) -> (3,10): 거리 6, 2초 -> 속도 3
        series = compute_point_series(points((0, 0, 10.0), (3, 4, 11.0), (3, 10, 13.0)))
        self.assertEqual(
            series,
            [
                (0, 0, 0.0, 0.0, 0.0),
                (3, 4, 1.0, 5.0, 5.0),
                (3, 10, 3.0, 11.0, 3.0),
            ],
        )

    def test_last_cumulative_distance_equals_total_distance(self):
        trajectory = points((0, 0, 1.0), (30, 40, 1.5), (30, 100, 2.0), (90, 180, 3.0))
        series = compute_point_series(trajectory)
        stats = compute_trajectory_stats(trajectory, None, None)
        self.assertAlmostEqual(series[-1][3], stats.total_distance)
        self.assertAlmostEqual(series[-1][2], stats.duration)

    def test_same_timestamp_gives_zero_speed_without_error(self):
        series = compute_point_series(points((0, 0, 1.0), (10, 0, 1.0)))
        self.assertEqual(series[1][4], 0.0)
        self.assertEqual(series[1][3], 10.0)


if __name__ == "__main__":
    unittest.main()
