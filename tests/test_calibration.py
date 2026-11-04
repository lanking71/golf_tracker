"""
매트 모서리 검증(src/calibration.py check_corners) 테스트. 카메라·Qt 없이 순수 파이썬으로 실행된다.

컨트롤이 ALIGN에 직접 실어 보내는 corners가 잘못되면 캘리브레이션이 망가지므로,
적용하기 전에 걸러내는 규칙을 하나씩 확인한다.
"""

import unittest

from src.calibration import check_corners

# 좌상 -> 우상 -> 우하 -> 좌하 (화면에서 시계 방향)
GOOD = [(100, 150), (1000, 100), (1020, 620), (90, 600)]
FRAME = (1280, 720)


class CheckCornersTests(unittest.TestCase):
    def test_valid_corners(self):
        self.assertIsNone(check_corners(GOOD))
        self.assertIsNone(check_corners(GOOD, FRAME))

    def test_accepts_lists_and_floats(self):
        self.assertIsNone(check_corners([[100.4, 150.0], [1000, 100], [1020.5, 620], [90, 600]], FRAME))

    def test_wrong_number_of_points(self):
        self.assertEqual(check_corners(GOOD[:3]), "bad_corners")
        self.assertEqual(check_corners(GOOD + [(500, 500)]), "bad_corners")
        self.assertEqual(check_corners([]), "bad_corners")

    def test_not_a_list(self):
        for value in (None, "abcd", 123, {"a": 1}):
            self.assertEqual(check_corners(value), "bad_corners", value)

    def test_bad_point_shape_or_values(self):
        self.assertEqual(check_corners([[1], [2, 3], [4, 5], [6, 7]]), "bad_corners")
        self.assertEqual(check_corners([(100, 150, 0), (1000, 100), (1020, 620), (90, 600)]), "bad_corners")
        self.assertEqual(check_corners([("a", 150), (1000, 100), (1020, 620), (90, 600)]), "bad_corners")
        self.assertEqual(check_corners([(True, 150), (1000, 100), (1020, 620), (90, 600)]), "bad_corners")
        self.assertEqual(check_corners([(None, 150), (1000, 100), (1020, 620), (90, 600)]), "bad_corners")
        self.assertEqual(check_corners([(float("nan"), 150), (1000, 100), (1020, 620), (90, 600)]), "bad_corners")
        self.assertEqual(check_corners([(float("inf"), 150), (1000, 100), (1020, 620), (90, 600)]), "bad_corners")

    def test_counter_clockwise_order_rejected(self):
        # 좌상, 좌하, 우하, 우상 (반대 방향) -> 좌우가 뒤집혀 mm 좌표가 거울상이 된다
        reversed_order = [GOOD[0], GOOD[3], GOOD[2], GOOD[1]]
        self.assertEqual(check_corners(reversed_order), "bad_corners")

    def test_rotated_start_point_rejected(self):
        # 시계 방향이지만 시작점이 우상 (순서를 한 칸 돌림) -> mm 좌표가 90도 돌아간다
        rotated = GOOD[1:] + GOOD[:1]
        self.assertEqual(check_corners(rotated), "bad_corners")

    def test_every_rotation_of_the_start_point_except_the_right_one_is_rejected(self):
        for shift in (1, 2, 3):
            rotated = GOOD[shift:] + GOOD[:shift]
            self.assertEqual(check_corners(rotated), "bad_corners", f"시작점을 {shift}칸 돌림")

    def test_strongly_skewed_mat_is_accepted(self):
        # 윗변이 오른쪽 위로 크게 올라간 모양: x+y 합으로 순서를 추정하면 우상·우하가 뒤바뀌는 경우.
        # 순서 자체는 올바르므로 통과해야 한다.
        skewed = [(100, 300), (1250, 50), (900, 300), (50, 650)]
        self.assertGreater(skewed[1][0] + skewed[1][1], skewed[2][0] + skewed[2][1])
        self.assertIsNone(check_corners(skewed, FRAME))

    def test_bow_tie_rejected(self):
        bow_tie = [GOOD[0], GOOD[2], GOOD[1], GOOD[3]]  # 선이 꼬인 나비넥타이 모양
        self.assertEqual(check_corners(bow_tie), "bad_corners")

    def test_concave_shape_rejected(self):
        concave = [(100, 100), (1000, 100), (150, 300), (100, 600)]  # 한 꼭짓점이 안쪽으로 들어감
        self.assertEqual(check_corners(concave), "bad_corners")

    def test_collinear_points_rejected(self):
        line = [(100, 100), (200, 100), (300, 100), (400, 100)]
        self.assertEqual(check_corners(line), "bad_corners")

    def test_tiny_quad_rejected(self):
        tiny = [(100, 100), (105, 100), (105, 105), (100, 105)]  # 넓이 25px²
        self.assertEqual(check_corners(tiny), "bad_corners")

    def test_out_of_frame_only_checked_when_frame_known(self):
        outside = [(100, 150), (1500, 100), (1020, 620), (90, 600)]  # 우상이 화면(1280) 밖
        self.assertIsNone(check_corners(outside))  # 화면 크기를 모르면 검사하지 않는다
        self.assertEqual(check_corners(outside, FRAME), "corners_out_of_frame")
        negative = [(-5, 150), (1000, 100), (1020, 620), (-10, 600)]
        self.assertEqual(check_corners(negative, FRAME), "corners_out_of_frame")

    def test_points_on_frame_border_are_ok(self):
        border = [(0, 0), (1280, 0), (1280, 720), (0, 720)]
        self.assertIsNone(check_corners(border, FRAME))


if __name__ == "__main__":
    unittest.main()
