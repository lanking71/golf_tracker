"""
Tracker(상태 기계) 단위 테스트. 카메라·Qt 없이 순수 파이썬으로 실행된다.

실행 (프로젝트 루트, 가상환경을 켠 상태에서):
    python -m unittest discover -s tests -v
"""

import unittest

from src.tracker import STOP_REASON, Tracker, TrackerState

SETTINGS = {
    "min_move_distance": 6,
    "max_jump_distance": 200,
    "max_consecutive_jumps": 10,
    "stop_threshold": 3,
    "stop_duration": 0.5,
    "lost_duration": 2.0,
    "arm_move_distance": 8,
}


def make_ready_tracker(start=(100, 100)) -> Tracker:
    """시작점이 등록된 READY 상태의 Tracker를 만든다."""
    tracker = Tracker(SETTINGS)
    assert tracker.register_start_position(*start)
    assert tracker.state == TrackerState.READY
    return tracker


class StateTransitionTests(unittest.TestCase):
    def test_start_tracking_goes_to_armed_not_tracking(self):
        tracker = make_ready_tracker()
        self.assertTrue(tracker.start_tracking())
        self.assertEqual(tracker.state, TrackerState.ARMED)

    def test_start_tracking_only_from_ready(self):
        tracker = Tracker(SETTINGS)
        self.assertFalse(tracker.start_tracking())  # IDLE
        tracker = make_ready_tracker()
        tracker.start_tracking()
        self.assertFalse(tracker.start_tracking())  # 이미 ARMED

    def test_allowed_actions_in_armed(self):
        tracker = make_ready_tracker()
        tracker.start_tracking()
        self.assertTrue(tracker.can("stop_tracking"))
        self.assertTrue(tracker.can("reset_trajectory"))
        # 대기 중에는 캘리브레이션·시작점 재등록·저장을 할 수 없다
        self.assertFalse(tracker.can("calibrate"))
        self.assertFalse(tracker.can("register_start_position"))
        self.assertFalse(tracker.can("save"))

    def test_stop_and_reset_from_armed(self):
        tracker = make_ready_tracker()
        tracker.start_tracking()
        self.assertTrue(tracker.stop_tracking())
        self.assertEqual(tracker.state, TrackerState.READY)
        self.assertIsNotNone(tracker.start_position)  # 시작점은 남는다

        tracker.start_tracking()
        self.assertTrue(tracker.reset_trajectory())
        self.assertEqual(tracker.state, TrackerState.READY)
        self.assertIsNone(tracker.start_position)


class ArmedBehaviorTests(unittest.TestCase):
    def test_long_wait_does_not_finish(self):
        """퍼팅 전에 공이 오래 가만히 있어도 '정지'로 끝나지 않는다 (이전의 버그)."""
        tracker = make_ready_tracker()
        tracker.start_tracking()
        for i in range(200):  # 10초
            self.assertIsNone(tracker.update_detection(100, 100, i * 0.05))
        self.assertEqual(tracker.state, TrackerState.ARMED)

    def test_missing_ball_does_not_finish(self):
        tracker = make_ready_tracker()
        tracker.start_tracking()
        for i in range(200):
            self.assertIsNone(tracker.update_detection(None, None, i * 0.05))
        self.assertEqual(tracker.state, TrackerState.ARMED)

    def test_jitter_below_threshold_stays_armed(self):
        tracker = make_ready_tracker()
        tracker.start_tracking()
        tracker.update_detection(100, 100, 1.0)
        tracker.update_detection(105, 103, 1.05)  # 거리 약 5.8 < 8
        self.assertEqual(tracker.state, TrackerState.ARMED)

    def test_move_over_threshold_starts_tracking(self):
        tracker = make_ready_tracker()
        tracker.start_tracking()
        tracker.update_detection(100, 100, 1.0)
        tracker.update_detection(100, 100, 1.05)  # 마지막으로 가만히 있던 시각
        tracker.update_detection(110, 100, 1.10)  # 10px 이동 >= 8

        self.assertEqual(tracker.state, TrackerState.TRACKING)
        self.assertEqual(tracker.tracking_started_at, 1.05)
        self.assertEqual(len(tracker.trajectory), 2)
        self.assertEqual((tracker.trajectory[0].x, tracker.trajectory[0].y), (100, 100))
        self.assertEqual(tracker.trajectory[0].timestamp, 1.05)
        self.assertEqual((tracker.trajectory[1].x, tracker.trajectory[1].y), (110, 100))

    def test_first_frame_already_moved_uses_current_time(self):
        """가만히 있던 프레임을 한 번도 못 봤으면(첫 프레임부터 움직임) 지금 시각을 쓴다."""
        tracker = make_ready_tracker()
        tracker.start_tracking()
        tracker.update_detection(150, 100, 2.0)
        self.assertEqual(tracker.state, TrackerState.TRACKING)
        self.assertEqual(tracker.tracking_started_at, 2.0)

    def test_without_start_position_uses_first_seen_ball(self):
        """궤적 초기화 직후처럼 시작점이 없으면 처음 본 공 위치를 기준으로 삼는다."""
        tracker = make_ready_tracker()
        tracker.reset_trajectory()
        tracker.start_tracking()
        tracker.update_detection(300, 300, 1.0)  # 기준이 됨
        tracker.update_detection(302, 300, 1.05)
        self.assertEqual(tracker.state, TrackerState.ARMED)
        tracker.update_detection(320, 300, 1.10)
        self.assertEqual(tracker.state, TrackerState.TRACKING)
        self.assertEqual((tracker.trajectory[0].x, tracker.trajectory[0].y), (300, 300))

    def test_stop_detection_works_after_tracking_starts(self):
        tracker = make_ready_tracker()
        tracker.start_tracking()
        tracker.update_detection(100, 100, 1.0)
        tracker.update_detection(130, 100, 1.05)  # 추적 시작
        reason = None
        for i in range(1, 30):  # 가만히 -> 0.5초 뒤 정지 판정
            reason = tracker.update_detection(130, 100, 1.05 + i * 0.05)
            if reason:
                break
        self.assertEqual(reason, STOP_REASON)
        self.assertEqual(tracker.state, TrackerState.FINISHED)

    def test_rearm_clears_previous_attempt(self):
        """추적 중지 후 다시 준비하면 이전 시도의 궤적이 남지 않는다."""
        tracker = make_ready_tracker()
        tracker.start_tracking()
        tracker.update_detection(100, 100, 1.0)
        tracker.update_detection(140, 100, 1.05)
        self.assertGreater(len(tracker.trajectory), 0)

        tracker.stop_tracking()
        tracker.start_tracking()
        self.assertEqual(tracker.trajectory, [])
        self.assertIsNone(tracker.tracking_started_at)

    def test_trajectory_not_recorded_while_armed(self):
        tracker = make_ready_tracker()
        tracker.start_tracking()
        self.assertFalse(tracker.add_trajectory_point(100, 100, 1.0))
        self.assertEqual(tracker.trajectory, [])

    def test_default_arm_distance_when_not_configured(self):
        tracker = Tracker({})  # arm_move_distance 설정이 없으면 기본값(8px)
        tracker.register_start_position(100, 100)
        tracker.start_tracking()
        tracker.update_detection(107, 100, 1.0)  # 7px
        self.assertEqual(tracker.state, TrackerState.ARMED)
        tracker.update_detection(108, 100, 1.05)  # 8px
        self.assertEqual(tracker.state, TrackerState.TRACKING)


if __name__ == "__main__":
    unittest.main()
