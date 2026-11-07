"""
24단계: 화면에서 공을 클릭해 색(HSV 범위)을 자동으로 잡는 기능 테스트.

합성 이미지: 녹색 잡음 배경(인조잔디 역할) 위에 흰 공 / 연두 공 / 분홍 공(Hue가 빨강 경계 0/179를
넘나듦)을 그린다. 공을 클릭(샘플)하면 그 공만 잡히고 배경 잡음은 걸리지 않아야 한다.
"""

import unittest

import cv2
import numpy as np

from src.detector import (
    BallDetector,
    auto_color_range,
    build_mask,
    color_range_from_pixels,
    hsv_in_range,
    sample_ball_pixels,
)

RNG = np.random.default_rng(1234)
WHITE = (150, 150)
LIME = (400, 150)
PINK = (650, 150)
RADIUS = 14


def hsv_patch(shape, h_values, s_range, v_range):
    """주어진 범위에서 무작위 H/S/V 값을 갖는 HSV 영상."""
    h = RNG.choice(h_values, size=shape)
    s = RNG.integers(s_range[0], s_range[1] + 1, size=shape)
    v = RNG.integers(v_range[0], v_range[1] + 1, size=shape)
    return np.dstack([h, s, v]).astype(np.uint8)


def draw_ball(frame, center, h_values, s_range, v_range):
    """center에 원 모양으로 HSV 잡음이 있는 공을 그린다."""
    patch = cv2.cvtColor(hsv_patch(frame.shape[:2], h_values, s_range, v_range), cv2.COLOR_HSV2BGR)
    mask = np.zeros(frame.shape[:2], np.uint8)
    cv2.circle(mask, center, RADIUS, 255, -1)
    frame[mask > 0] = patch[mask > 0]


def make_scene(extra_white=None):
    # 녹색 매트: Hue 48~72, 채도·밝기에 잡음
    mat = hsv_patch((300, 800), np.arange(48, 73), (90, 200), (50, 150))
    frame = cv2.cvtColor(mat, cv2.COLOR_HSV2BGR)
    draw_ball(frame, WHITE, np.arange(0, 180), (0, 25), (215, 250))  # 흰 공: 채도 낮음
    draw_ball(frame, LIME, np.arange(32, 41), (170, 230), (190, 240))  # 연두 공
    # 분홍(빨강 계열) 공: Hue가 176~179와 0~3에 걸침
    draw_ball(frame, PINK, np.array([176, 177, 178, 179, 0, 1, 2, 3]), (170, 230), (190, 240))
    if extra_white is not None:
        center, v_range = extra_white
        draw_ball(frame, center, np.arange(0, 180), (0, 25), v_range)
    return frame


def detector_for(result) -> BallDetector:
    return BallDetector(
        {
            "active_profile": "picked",
            "profiles": {
                "picked": {
                    "name": "클릭으로 잡은 색",
                    "hsv_lower": result.hsv_lower,
                    "hsv_upper": result.hsv_upper,
                    "min_area": result.suggested_min_area,
                    "max_area": result.suggested_max_area,
                    "min_circularity": 0.7,
                }
            },
        }
    )


class ColorPickTests(unittest.TestCase):
    def setUp(self):
        self.frame = make_scene()

    def pick(self, point):
        sample = sample_ball_pixels(self.frame, point)
        self.assertIsNotNone(sample)
        result = auto_color_range(self.frame, [sample])
        self.assertTrue(result.ball_found)
        return result

    def assert_only_ball(self, result, point):
        """추출한 범위로 그 공만 잡히는지: 공 위치는 마스크 안, 다른 공·매트는 밖."""
        mask = build_mask(self.frame, result.hsv_lower, result.hsv_upper)
        self.assertEqual(result.other_blobs, 0)
        self.assertGreater(mask[point[1], point[0]], 0)
        for other in (WHITE, LIME, PINK):
            if other != point:
                self.assertEqual(mask[other[1], other[0]], 0, f"{other}의 공까지 잡힘")
        # 매트 잡음: 공 3개 말고는 거의 걸리지 않아야 한다 (공 면적의 30% 미만)
        mask_without_ball = mask.copy()
        cv2.circle(mask_without_ball, point, RADIUS + 6, 0, -1)
        self.assertLess(int((mask_without_ball > 0).sum()), result.ball_area * 0.3)
        found = detector_for(result).detect(self.frame)
        self.assertIsNotNone(found)
        self.assertAlmostEqual(found.x, point[0], delta=3)
        self.assertAlmostEqual(found.y, point[1], delta=3)

    def test_white_ball(self):
        result = self.pick(WHITE)
        # 흰 공은 색(Hue)이 의미 없으므로 0~179 전체 허용
        self.assertEqual((result.hsv_lower[0], result.hsv_upper[0]), (0, 179))
        self.assertLess(result.hsv_upper[1], 100)  # 채도 상한은 낮게
        self.assertGreater(result.hsv_lower[2], 120)  # 밝기 하한은 높게
        self.assert_only_ball(result, WHITE)

    def test_lime_ball_separates_from_green_mat(self):
        result = self.pick(LIME)
        self.assertLessEqual(result.hsv_lower[0], result.hsv_upper[0])
        # 공 가장자리는 블러로 매트 색과 섞여 Hue 범위가 매트(48~)와 조금 겹칠 수 있다.
        # 그래도 채도·밝기 범위가 달라 매트는 걸리지 않아야 한다 (아래 assert_only_ball이 확인).
        self.assertLess(result.hsv_upper[0], 60)
        self.assert_only_ball(result, LIME)

    def test_pink_ball_wraps_hue(self):
        result = self.pick(PINK)
        # 빨강 경계를 넘는 범위: H 최소 > H 최대 (예: 170 ~ 10)
        self.assertGreater(result.hsv_lower[0], result.hsv_upper[0])
        self.assert_only_ball(result, PINK)

    def test_suggested_area_limits(self):
        result = self.pick(WHITE)
        # 25단계: 0.4~2.5배 -> 0.6~1.3배 (홀컵 약 1.4~2.1배는 통과시키지 않는다)
        self.assertAlmostEqual(result.suggested_min_area, result.ball_area * 0.6, delta=1)
        self.assertAlmostEqual(result.suggested_max_area, result.ball_area * 1.3, delta=1)
        self.assertIsNone(result.area_warning)  # 캘리브레이션 정보(예상 면적)를 안 주면 경고도 없음
        self.assertGreater(result.ball_circularity, 0.7)
        self.assertAlmostEqual(result.ball_diameter, 2 * RADIUS, delta=5)

    def test_click_outside_frame(self):
        self.assertIsNone(sample_ball_pixels(self.frame, (5000, 10)))


class MergeSamplesTests(unittest.TestCase):
    def test_two_samples_cover_both_lighting(self):
        """같은 흰 공이 밝은 곳·어두운 곳에 있을 때 두 샘플을 합치면 둘 다 잡힌다."""
        dark = (150, 250)
        frame = make_scene(extra_white=(dark, (125, 150)))
        bright_sample = sample_ball_pixels(frame, WHITE)
        dark_sample = sample_ball_pixels(frame, dark)
        merged_pixels = np.concatenate([bright_sample.pixels, dark_sample.pixels])

        bright_only = color_range_from_pixels(bright_sample.pixels, 0.0)
        merged = color_range_from_pixels(merged_pixels, 0.0)
        self.assertGreater(bright_only[0][2], 160)  # 밝은 샘플만 쓰면 어두운 공(V 125~150)은 범위 밖
        self.assertLessEqual(merged[0][2], 130)  # 합치면 밝기 하한이 내려간다

        result = auto_color_range(frame, [bright_sample, dark_sample])
        mask = build_mask(frame, result.hsv_lower, result.hsv_upper)
        self.assertGreater(mask[WHITE[1], WHITE[0]], 0)
        self.assertGreater(mask[dark[1], dark[0]], 0)


class HueWrapMaskTests(unittest.TestCase):
    def test_two_hue_intervals(self):
        hsv = np.array([[[178, 200, 200], [2, 200, 200], [90, 200, 200], [12, 200, 200]]], np.uint8)
        mask = hsv_in_range(hsv, [170, 100, 100], [8, 255, 255])
        self.assertEqual(list(mask[0] > 0), [True, True, False, False])

    def test_normal_interval_unchanged(self):
        hsv = np.array([[[50, 200, 200], [120, 200, 200]]], np.uint8)
        mask = hsv_in_range(hsv, [40, 100, 100], [60, 255, 255])
        self.assertEqual(list(mask[0] > 0), [True, False])


if __name__ == "__main__":
    unittest.main()
