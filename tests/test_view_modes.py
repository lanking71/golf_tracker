"""
27단계: 카메라 창에 궤적 직접 그리기 + "전체 보기" 테스트 (화면 없는 offscreen 방식).

- 카메라 보기에 궤적 선이 그려짐 / 체크박스를 끄면 안 그려짐
- 전체 보기 크기가 매트 비율과 같음, 매트 네 모서리가 전체 보기의 네 모서리로 감
- 알려진 mm 위치의 공이 전체 보기의 같은 위치에 그려짐
- 캘리브레이션이 없거나 무효면 카메라 보기, 캘리브레이션·색 잡기 중에는 카메라 보기로 바뀌었다가 돌아옴
- 보기 모드 저장·복원
"""

import json
import unittest

import cv2
import numpy as np

import src.config
from src.tracker import TrackerState
from src.ui.main_window import (
    BALL_CENTER_COLOR_BGR,
    TRAJECTORY_PATH_COLOR_BGR,
    VIEW_CAMERA,
    VIEW_MAT,
    MainWindow,
)
from tests.base import EngineTestCase, qt_app
from tests.fake_camera import MAT_COLOR_BGR, MAT_CORNERS


class ViewTestCase(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.window = MainWindow(self.engine)
        self.window.resize(1300, 850)
        self.window.show()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        qt_app.processEvents()
        super().tearDown()

    def step(self, ball=None, dt=0.05):
        super().step(ball, dt)
        qt_app.processEvents()

    def calibrate(self):
        self.step()
        self.assertEqual(self.engine.align_corners([list(c) for c in MAT_CORNERS]), "OK")
        self.engine.tracker.state = TrackerState.READY  # 캘리브레이션 모드를 끝낸 것으로 (모서리 찍기 화면이 아님)
        self.step()

    def putt(self):
        """시작점 등록 → 추적 대기 → 공을 (300,400)에서 (540,400)까지 굴림."""
        self.step(ball=(300, 400))
        self.assertEqual(self.engine.register_start(), "OK")
        self.assertTrue(self.engine.start_tracking())
        for x in range(320, 541, 20):
            self.step(ball=(x, 400))

    @staticmethod
    def near(pixel, color, tolerance=40):
        return all(abs(int(a) - int(b)) <= tolerance for a, b in zip(pixel, color))


class CameraViewTests(ViewTestCase):
    def test_trajectory_drawn_on_camera_view(self):
        self.calibrate()
        self.putt()
        frame = self.window._last_view_frame
        self.assertEqual(self.window._last_view, VIEW_CAMERA)
        self.assertEqual(frame.shape[:2], (720, 1280))  # 카메라 프레임 그대로
        # 공이 지나간 자리(지금 공이 있는 곳이 아닌)에 라임색 궤적 선이 있다
        self.assertTrue(self.near(frame[400, 430], TRAJECTORY_PATH_COLOR_BGR))
        # 궤적이 없는 매트 자리는 매트 색 그대로
        self.assertTrue(self.near(frame[300, 430], MAT_COLOR_BGR))

    def test_trajectory_hidden_when_unchecked(self):
        self.calibrate()
        self.window._on_trajectory_toggled(False)
        self.putt()
        frame = self.window._last_view_frame
        self.assertTrue(self.near(frame[400, 430], MAT_COLOR_BGR))
        saved = json.loads(src.config.SETTINGS_PATH.read_text(encoding="utf-8"))
        self.assertFalse(saved["ui"]["show_trajectory"])

    def test_no_bottom_trajectory_panel(self):
        self.assertFalse(hasattr(self.window, "trajectory_label"))  # 아래 궤적 창은 없어졌다
        self.assertTrue(self.window.stats_label.isVisible())  # 통계 패널은 남아 있다


class MatViewTests(ViewTestCase):
    def test_mat_view_size_and_corners(self):
        self.calibrate()
        self.window._set_view_mode(VIEW_MAT)
        self.step()
        self.assertEqual(self.window._last_view, VIEW_MAT)
        frame = self.window._last_view_frame
        # 4500x2000mm 매트 -> 가로 1440, 세로 1440 x 2000/4500 = 640
        self.assertEqual(frame.shape[:2], (640, 1440))
        transform = self.window._mat_view.transform(self.engine.calibration)
        corners = cv2.perspectiveTransform(np.array([MAT_CORNERS], dtype=np.float64), transform)[0]
        expected = [(0, 0), (1440, 0), (1440, 640), (0, 640)]
        for (x, y), (ex, ey) in zip(corners, expected):
            self.assertAlmostEqual(x, ex, delta=1)
            self.assertAlmostEqual(y, ey, delta=1)
        # 매트 안은 매트 색으로 꽉 찬다 (펼친 화면 가운데)
        self.assertTrue(self.near(frame[320, 720], MAT_COLOR_BGR))

    def test_ball_drawn_at_its_mm_position(self):
        self.calibrate()
        self.window._set_view_mode(VIEW_MAT)
        self.step(ball=(600, 300))
        ball_mm = self.engine.calibration.pixel_to_real(600, 300)
        x = round(ball_mm[0] / 4500 * 1440)
        y = round(ball_mm[1] / 2000 * 640)
        frame = self.window._last_view_frame
        self.assertTrue(self.near(frame[y, x], BALL_CENTER_COLOR_BGR, 10))  # 공 중심(빨간 점)

    def test_trajectory_drawn_on_mat_view(self):
        self.calibrate()
        self.window._set_view_mode(VIEW_MAT)
        self.putt()
        mm = self.engine.calibration.pixel_to_real(430, 400)
        x, y = round(mm[0] / 4500 * 1440), round(mm[1] / 2000 * 640)
        self.assertTrue(self.near(self.window._last_view_frame[y, x], TRAJECTORY_PATH_COLOR_BGR))


class ViewSwitchTests(ViewTestCase):
    def test_mat_view_needs_calibration(self):
        self.step()
        self.assertFalse(self.window.view_buttons[VIEW_MAT].isEnabled())
        self.window._set_view_mode(VIEW_MAT)
        self.step()
        self.assertEqual(self.window._last_view, VIEW_CAMERA)
        self.calibrate()
        self.assertTrue(self.window.view_buttons[VIEW_MAT].isEnabled())

    def test_invalid_calibration_returns_to_camera(self):
        self.calibrate()
        self.window._set_view_mode(VIEW_MAT)
        self.step()
        self.assertEqual(self.window._last_view, VIEW_MAT)
        self.engine.calibration.invalidate()  # 처리 조건이 바뀌어 무효가 된 경우와 같음
        self.step()
        self.assertEqual(self.window._last_view, VIEW_CAMERA)
        self.assertTrue(self.window.view_buttons[VIEW_CAMERA].isChecked())

    def test_calibrating_and_color_pick_force_camera_then_return(self):
        self.calibrate()
        self.window._set_view_mode(VIEW_MAT)
        self.step()
        self.engine.start_calibration()  # 모서리 찍기 모드
        self.step()
        self.assertEqual(self.window._last_view, VIEW_CAMERA)
        for corner in MAT_CORNERS:
            self.engine.add_calibration_corner(*corner)
        self.engine.tracker.state = TrackerState.READY  # 캘리브레이션을 마침
        self.step()
        self.assertEqual(self.window._last_view, VIEW_MAT)  # 원래 보기로 돌아옴

        self.window._pick_mode = True  # 색 잡기 모드
        self.assertEqual(self.window._effective_view(), VIEW_CAMERA)
        self.window._pick_mode = False
        self.assertEqual(self.window._effective_view(), VIEW_MAT)

    def test_view_mode_saved_and_restored(self):
        self.calibrate()
        self.window._set_view_mode(VIEW_MAT)
        saved = json.loads(src.config.SETTINGS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(saved["ui"]["view_mode"], VIEW_MAT)
        other = MainWindow(self.engine)
        self.assertEqual(other._view_mode, VIEW_MAT)
        other.close()
        other.deleteLater()


if __name__ == "__main__":
    unittest.main()
