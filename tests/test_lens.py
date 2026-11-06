"""
프레임 전처리(src/lens.py) 테스트: 회전 -> 축소 -> 왜곡 보정.

카메라 없이 합성 이미지로 확인한다.
"""

import unittest

import cv2
import numpy as np

from src.lens import DEFAULT_PROCESS_SCALE, FramePreprocessor, build_undistort_maps, undistorted_radius


def make_frame(width=1920, height=1080):
    """가로 줄무늬 + 세로 줄무늬가 있는 테스트 프레임 (내용이 바뀌었는지 비교하기 좋게)."""
    frame = np.zeros((height, width, 3), np.uint8)
    frame[::40, :, :] = 255
    frame[:, ::40, 1] = 200
    return frame


class PreprocessSizeTests(unittest.TestCase):
    def test_k1_zero_and_scale_one_returns_original(self):
        """k1=0, 축소 없음이면 원본과 똑같은 프레임이 나온다 (보정을 건너뜀)."""
        frame = make_frame()
        pre = FramePreprocessor({"process_scale": 1.0, "undistort_k1": 0.0})
        out = pre.process(frame)
        self.assertTrue(np.array_equal(out, frame))
        self.assertEqual(pre.map_build_count, 0)  # 왜곡 보정 지도를 만들지도 않는다

    def test_output_size_follows_process_scale(self):
        frame = make_frame(1920, 1080)
        for scale, expected in [(1.0, (1920, 1080)), (0.75, (1440, 810)), (0.5, (960, 540))]:
            with self.subTest(scale=scale):
                out = FramePreprocessor({"process_scale": scale}).process(frame)
                self.assertEqual((out.shape[1], out.shape[0]), expected)

    def test_scale_keeps_size_with_undistort(self):
        """왜곡 보정을 해도 프레임 크기는 축소된 크기 그대로다."""
        out = FramePreprocessor({"process_scale": 0.5, "undistort_k1": -0.2}).process(make_frame())
        self.assertEqual((out.shape[1], out.shape[0]), (960, 540))

    def test_rotate_before_scale(self):
        """회전이 먼저 적용되므로 90도 회전 + 0.5 축소는 540x960이 된다."""
        out = FramePreprocessor({"rotate": 90, "process_scale": 0.5}).process(make_frame())
        self.assertEqual((out.shape[1], out.shape[0]), (540, 960))

    def test_missing_or_bad_values_use_defaults(self):
        """예전 설정 파일(값 없음)이나 잘못된 값이어도 기본값으로 동작한다."""
        self.assertEqual(FramePreprocessor({}).process_scale, DEFAULT_PROCESS_SCALE)
        self.assertEqual(FramePreprocessor(None).undistort_k1, 0.0)
        bad = FramePreprocessor({"process_scale": "abc", "undistort_k1": None, "rotate": 45})
        self.assertEqual(bad.process_scale, DEFAULT_PROCESS_SCALE)
        self.assertEqual(bad.undistort_k1, 0.0)
        self.assertEqual(bad.rotate, 0)
        # 범위를 벗어난 값은 허용 범위로 맞춘다
        self.assertEqual(FramePreprocessor({"process_scale": 3}).process_scale, 1.0)
        self.assertEqual(FramePreprocessor({"process_scale": 0.01}).process_scale, 0.1)


class UndistortMapTests(unittest.TestCase):
    def test_nonzero_k1_changes_image(self):
        frame = make_frame(960, 540)
        out = FramePreprocessor({"process_scale": 1.0, "undistort_k1": -0.3}).process(frame)
        self.assertFalse(np.array_equal(out, frame))

    def test_fused_scale_and_undistort_matches_two_steps(self):
        """축소+왜곡 보정을 remap 한 번으로 합친 결과가 '축소한 뒤 보정'과 사실상 같다."""
        # 부드러운 그라데이션 영상 (축소 방식 차이로 생기는 계단 현상이 비교를 흐리지 않게)
        xs = np.linspace(0, 255, 1920, dtype=np.float32)
        ys = np.linspace(0, 255, 1080, dtype=np.float32)
        frame = np.dstack(
            [np.tile(xs, (1080, 1)), np.tile(ys[:, None], (1, 1920)), np.full((1080, 1920), 128, np.float32)]
        ).astype(np.uint8)
        fused = FramePreprocessor({"process_scale": 0.5, "undistort_k1": -0.3}).process(frame)

        small = cv2.resize(frame, (960, 540), interpolation=cv2.INTER_LINEAR)
        map1, map2 = build_undistort_maps(960, 540, -0.3)
        two_step = cv2.remap(small, map1, map2, interpolation=cv2.INTER_LINEAR)
        diff = np.abs(fused.astype(int) - two_step.astype(int))
        self.assertLess(diff.mean(), 1.5)

    def test_map_is_not_rebuilt_every_frame(self):
        """지도는 처음 한 번만 만들고, 이후 프레임은 remap만 한다."""
        pre = FramePreprocessor({"process_scale": 0.5, "undistort_k1": -0.2})
        frame = make_frame()
        for _ in range(30):
            pre.process(frame)
        self.assertEqual(pre.map_build_count, 1)

    def test_map_rebuilt_only_when_k1_or_size_changes(self):
        pre = FramePreprocessor({"process_scale": 1.0, "undistort_k1": -0.2})
        small, large = make_frame(640, 360), make_frame(1280, 720)
        pre.process(small)
        pre.process(small)
        self.assertEqual(pre.map_build_count, 1)

        pre.set_undistort_k1(-0.25)  # k1이 바뀜 -> 한 번 다시 만든다
        pre.process(small)
        pre.process(small)
        self.assertEqual(pre.map_build_count, 2)

        pre.process(large)  # 크기가 바뀜 -> 한 번 다시 만든다
        pre.process(large)
        self.assertEqual(pre.map_build_count, 3)

        pre.set_undistort_k1(0)  # 0이면 보정 자체를 건너뛴다
        self.assertTrue(np.array_equal(pre.process(large), large))
        self.assertEqual(pre.map_build_count, 3)

    def test_whole_frame_stays_visible(self):
        """술통형 보정(k1<0)을 해도 원본 네 모서리 근처 내용이 화면 밖으로 잘려 나가지 않는다.

        원본 가장자리에 1픽셀 테두리 대신 두꺼운 흰 띠를 두고, 보정 후에도
        네 변 쪽에 흰 픽셀이 남아 있는지 본다 (alpha=1.0이면 모두 화면 안에 남는다).
        """
        width, height = 960, 540
        frame = np.zeros((height, width, 3), np.uint8)
        band = 12
        frame[:band, :] = 255
        frame[-band:, :] = 255
        frame[:, :band] = 255
        frame[:, -band:] = 255
        out = FramePreprocessor({"process_scale": 1.0, "undistort_k1": -0.3}).process(frame)
        gray = out[:, :, 0]
        # 왼쪽·오른쪽 끝 가운데 높이의 흰 띠가 화면 안에 남아 있어야 한다
        middle_row = gray[height // 2]
        self.assertGreater(middle_row[: width // 4].max(), 200)
        self.assertGreater(middle_row[-width // 4 :].max(), 200)
        middle_col = gray[:, width // 2]
        self.assertGreater(middle_col[: height // 4].max(), 200)
        self.assertGreater(middle_col[-height // 4 :].max(), 200)


class UndistortedRadiusTests(unittest.TestCase):
    def test_inverse_of_distortion(self):
        for k1 in (-0.5, -0.2, 0.3):
            for r_u in (0.1, 0.3, 0.5):
                r_d = r_u * (1 + k1 * r_u * r_u)
                with self.subTest(k1=k1, r_u=r_u):
                    self.assertAlmostEqual(undistorted_radius(r_d, k1), r_u, places=6)

    def test_folded_region_is_clamped(self):
        """k1이 너무 작아 모델이 접히는 곳은 접히기 직전 반지름에서 멈춘다 (무한대로 가지 않음)."""
        k1 = -1.0
        peak = 1 / np.sqrt(3.0)
        self.assertAlmostEqual(undistorted_radius(0.9, k1), peak, places=6)


if __name__ == "__main__":
    unittest.main()
