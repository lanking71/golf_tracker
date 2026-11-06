"""
23단계: 수동 4점 캘리브레이션 + 매트 크기 확인 테스트.

- 기본(auto_detect 꺼짐): 캘리브레이션 시작 후 클릭이 4개까지 받아짐
- auto_detect 켜짐: 자동 검출 결과는 '제안'일 뿐, 확정 전에는 저장되지 않음
- 오른쪽 클릭(마지막 점 취소), 4점 후 점 이동 시 행렬 재계산
- 4점이 올바르지 않으면 저장하지 않음
- 비율 경고 (매트 크기 오타)
- 화면(라벨) 좌표 -> 처리 프레임 좌표 변환 (여백·축소 포함)
- 엔진 시작 시 화면에 알림(포트 문구 갱신), k1 슬라이더 로그 정리
"""

import json
import logging
import unittest

import numpy as np

import src.config
from src.calibration import Calibration, aspect_ratio_warning, corners_aspect_ratio
from src.engine import CALIBRATION_AUTO_FAILED, CALIBRATION_MANUAL, CALIBRATION_PROPOSED
from src.tracker import TrackerState
from src.ui.camera_label import frame_to_label, label_to_frame
from tests.base import EngineTestCase
from tests.fake_camera import MAT_CORNERS


def saved_calibration() -> dict:
    """테스트용 설정 파일에 지금 저장돼 있는 calibration 항목."""
    return json.loads(src.config.SETTINGS_PATH.read_text(encoding="utf-8"))["calibration"]


class ManualCalibrationTests(EngineTestCase):
    """기본 설정(auto_detect 없음 = 꺼짐)."""

    def test_default_is_manual_and_accepts_four_clicks(self):
        self.step()
        self.assertEqual(self.engine.start_calibration(), CALIBRATION_MANUAL)
        self.assertIsNone(self.engine.calibration_proposal)
        for i, (x, y) in enumerate(MAT_CORNERS, start=1):
            self.assertTrue(self.engine.add_calibration_corner(x, y))
            self.assertEqual(len(self.engine.calibration.corners), i)
        self.assertTrue(self.engine.calibration.is_calibrated())
        saved = saved_calibration()
        self.assertEqual(saved["corners"], [list(c) for c in MAT_CORNERS])
        self.assertEqual(saved["frame_size"], [1280, 720])
        # 5번째 클릭은 받지 않는다 (옮기려면 드래그)
        self.assertFalse(self.engine.add_calibration_corner(10, 10))

    def test_restart_clears_wrong_points(self):
        """'다시 지정' = 캘리브레이션 버튼 재클릭: 찍은 점을 모두 지운다 (자동 검출로 다시 채우지 않음)."""
        self.step()
        self.engine.start_calibration()
        self.engine.add_calibration_corner(*MAT_CORNERS[0])
        self.engine.add_calibration_corner(*MAT_CORNERS[1])
        self.assertEqual(self.engine.start_calibration(), CALIBRATION_MANUAL)
        self.assertEqual(self.engine.calibration.corners, [])

    def test_bad_fourth_point_is_not_saved(self):
        """순서가 틀린 4점(좌상, 우하, 우상, 좌하)은 저장하지 않고 다시 찍게 한다."""
        self.step()
        self.engine.start_calibration()
        wrong = [MAT_CORNERS[0], MAT_CORNERS[2], MAT_CORNERS[1], MAT_CORNERS[3]]
        for x, y in wrong[:3]:
            self.assertTrue(self.engine.add_calibration_corner(x, y))
        self.assertFalse(self.engine.add_calibration_corner(*wrong[3]))
        self.assertEqual(self.engine.corner_problem, "bad_corners")
        self.assertEqual(self.engine.calibration.corners, [])
        self.assertFalse(self.engine.calibration.is_calibrated())
        self.assertIsNone(saved_calibration().get("perspective_matrix"))
        # 다시 올바르게 찍으면 이유가 지워지고 저장된다
        for x, y in MAT_CORNERS:
            self.engine.add_calibration_corner(x, y)
        self.assertIsNone(self.engine.corner_problem)
        self.assertTrue(self.engine.calibration.is_calibrated())

    def test_point_outside_frame_is_rejected(self):
        self.step()
        self.engine.start_calibration()
        corners = list(MAT_CORNERS)
        corners[2] = (1300, 620)  # 화면(1280x720) 밖
        for x, y in corners:
            self.engine.add_calibration_corner(x, y)
        self.assertEqual(self.engine.corner_problem, "corners_out_of_frame")
        self.assertFalse(self.engine.calibration.is_calibrated())

    def test_right_click_undoes_last_point(self):
        self.step()
        self.engine.start_calibration()
        for x, y in MAT_CORNERS[:3]:
            self.engine.add_calibration_corner(x, y)
        self.assertTrue(self.engine.undo_last_corner())
        self.assertEqual(self.engine.calibration.corners, list(MAT_CORNERS[:2]))
        # 4점을 다 찍은 뒤 취소하면 행렬도 지워진다
        for x, y in MAT_CORNERS[2:]:
            self.engine.add_calibration_corner(x, y)
        self.assertTrue(self.engine.calibration.is_calibrated())
        self.assertTrue(self.engine.undo_last_corner())
        self.assertFalse(self.engine.calibration.is_calibrated())
        self.assertEqual(len(self.engine.calibration.corners), 3)
        # 다시 4번째 점을 찍을 수 있다
        self.assertTrue(self.engine.add_calibration_corner(*MAT_CORNERS[3]))
        self.assertTrue(self.engine.calibration.is_calibrated())

    def test_undo_only_while_calibrating(self):
        self.assertFalse(self.engine.undo_last_corner())

    def test_move_corner_recomputes_matrix_and_saves(self):
        self.step()
        self.engine.start_calibration()
        for x, y in MAT_CORNERS:
            self.engine.add_calibration_corner(x, y)
        before = self.engine.calibration.matrix.copy()

        self.assertIsNone(self.engine.move_calibration_corner(2, 1010, 630))
        self.assertEqual(self.engine.calibration.corners[2], (1010, 630))
        self.assertFalse(np.allclose(before, self.engine.calibration.matrix))
        # 옮긴 점이 정확히 매트 우하단(가로, 세로 mm)으로 변환된다
        x_mm, y_mm = self.engine.calibration.pixel_to_real(1010, 630)
        self.assertAlmostEqual(x_mm, self.engine.calibration.mat_width_mm, delta=0.5)
        self.assertAlmostEqual(y_mm, self.engine.calibration.mat_height_mm, delta=0.5)
        self.assertEqual(saved_calibration()["corners"][2], [1010, 630])

    def test_bad_move_is_rejected(self):
        self.step()
        self.engine.start_calibration()
        for x, y in MAT_CORNERS:
            self.engine.add_calibration_corner(x, y)
        before = list(self.engine.calibration.corners)
        # 우하단 점을 좌상단 너머로 끌고 가면 사각형이 꼬인다
        self.assertEqual(self.engine.move_calibration_corner(2, 50, 50), "bad_corners")
        self.assertEqual(self.engine.calibration.corners, before)
        self.assertEqual(self.engine.move_calibration_corner(2, 2000, 620), "corners_out_of_frame")

    def test_move_needs_four_points(self):
        self.step()
        self.engine.start_calibration()
        self.engine.add_calibration_corner(*MAT_CORNERS[0])
        self.assertEqual(self.engine.move_calibration_corner(0, 10, 10), "bad_state")

    def test_nearest_corner(self):
        calibration = Calibration({"corners": [list(c) for c in MAT_CORNERS]})
        self.assertEqual(calibration.nearest_corner(1005, 105, 15), 1)
        self.assertIsNone(calibration.nearest_corner(500, 400, 15))


class AutoDetectProposalTests(EngineTestCase):
    """calibration.auto_detect = true: 자동 검출 결과는 '제안'일 뿐 바로 저장되지 않는다."""

    settings_overrides = {
        "calibration": {"mat_width_mm": 4500, "mat_height_mm": 2000, "filter_outside_mat": True, "auto_detect": True}
    }

    def test_proposal_is_not_saved_until_accepted(self):
        self.step()
        self.assertEqual(self.engine.start_calibration(), CALIBRATION_PROPOSED)
        self.assertIsNotNone(self.engine.calibration_proposal)
        self.assertEqual(self.engine.calibration.corners, [])  # 클릭을 받을 수 있는 상태
        self.assertFalse(self.engine.calibration.is_calibrated())
        self.assertIsNone(saved_calibration().get("perspective_matrix"))  # 아직 저장 안 됨

        self.assertTrue(self.engine.accept_calibration_proposal())
        self.assertIsNone(self.engine.calibration_proposal)
        self.assertTrue(self.engine.calibration.is_calibrated())
        self.assertIsNotNone(saved_calibration()["perspective_matrix"])

    def test_reject_proposal_goes_manual(self):
        self.step()
        self.engine.start_calibration()
        self.engine.reject_calibration_proposal()
        self.assertIsNone(self.engine.calibration_proposal)
        self.assertFalse(self.engine.accept_calibration_proposal())  # 확정할 제안이 없다
        for x, y in MAT_CORNERS:
            self.assertTrue(self.engine.add_calibration_corner(x, y))
        self.assertTrue(self.engine.calibration.is_calibrated())

    def test_clicking_while_proposal_means_manual(self):
        """제안이 떠 있어도 바로 클릭하면 '직접 지정'으로 보고 제안을 버린다."""
        self.step()
        self.engine.start_calibration()
        self.assertTrue(self.engine.add_calibration_corner(*MAT_CORNERS[0]))
        self.assertIsNone(self.engine.calibration_proposal)
        self.assertEqual(self.engine.calibration.corners, [MAT_CORNERS[0]])

    def test_auto_failed_goes_manual(self):
        self.camera.show_mat = False
        self.step()
        self.assertEqual(self.engine.start_calibration(), CALIBRATION_AUTO_FAILED)
        self.assertEqual(self.engine.tracker.state, TrackerState.CALIBRATING)
        self.assertIsNone(self.engine.calibration_proposal)


class AspectRatioWarningTests(unittest.TestCase):
    # 화면에서 가로 900px, 세로 300px인 3:1 사각형
    THREE_TO_ONE = [(100, 100), (1000, 100), (1000, 400), (100, 400)]

    def test_ratio_of_corners(self):
        self.assertAlmostEqual(corners_aspect_ratio(self.THREE_TO_ONE), 3.0)
        self.assertIsNone(corners_aspect_ratio(self.THREE_TO_ONE[:3]))

    def test_matching_size_has_no_warning(self):
        self.assertIsNone(aspect_ratio_warning(self.THREE_TO_ONE, 3000, 1000))
        # 30% 안쪽 차이(카메라가 조금 비스듬함)는 경고하지 않는다
        self.assertIsNone(aspect_ratio_warning(self.THREE_TO_ONE, 3000, 1200))

    def test_typo_size_warns(self):
        warning = aspect_ratio_warning(self.THREE_TO_ONE, 3000, 10000)
        self.assertIsNotNone(warning)
        self.assertIn("3000x10000", warning)
        self.assertIn("크기를 확인하세요", warning)
        self.assertIsNotNone(aspect_ratio_warning(self.THREE_TO_ONE, 4500, 3000))


class AspectNoticeEngineTests(EngineTestCase):
    """세로 10000mm 오타로 캘리브레이션하면 엔진이 경고(calibration_notice)를 남긴다 (저장은 됨)."""

    settings_overrides = {
        "calibration": {"mat_width_mm": 3000, "mat_height_mm": 10000, "filter_outside_mat": True}
    }

    def test_notice_after_calibration(self):
        self.step()
        self.engine.start_calibration()
        with self.assertLogs("engine", level="WARNING") as logs:
            for x, y in MAT_CORNERS:
                self.engine.add_calibration_corner(x, y)
        self.assertTrue(self.engine.calibration.is_calibrated())  # 저장은 막지 않는다
        self.assertIn("3000x10000", self.engine.calibration_notice)
        self.assertTrue(any("크기를 확인하세요" in line for line in logs.output))


class CoordinateMappingTests(unittest.TestCase):
    """화면(라벨) 좌표 -> 처리 프레임(1440x810) 좌표. 비율을 유지해 축소하고 위아래에 여백이 있다."""

    LABEL = (800, 600)  # 라벨 크기
    PIXMAP = (800, 450)  # 1440x810을 800 폭에 맞춰 줄인 영상 크기 (위아래 여백 75px씩)
    FRAME = (1440, 810)

    def test_center_and_corners(self):
        self.assertEqual(label_to_frame((400, 300), self.LABEL, self.PIXMAP, self.FRAME), (720, 405))
        self.assertEqual(label_to_frame((0, 75), self.LABEL, self.PIXMAP, self.FRAME), (0, 0))
        self.assertEqual(label_to_frame((799, 524), self.LABEL, self.PIXMAP, self.FRAME), (1438, 808))

    def test_letterbox_margin_is_ignored(self):
        self.assertIsNone(label_to_frame((400, 74), self.LABEL, self.PIXMAP, self.FRAME))  # 위 여백
        self.assertIsNone(label_to_frame((400, 525), self.LABEL, self.PIXMAP, self.FRAME))  # 아래 여백

    def test_side_margin(self):
        """창이 세로로 길지 않고 가로로 넓을 때는 좌우에 여백이 생긴다."""
        label, pixmap = (1000, 450), (800, 450)
        self.assertIsNone(label_to_frame((50, 200), label, pixmap, self.FRAME))
        self.assertEqual(label_to_frame((100, 0), label, pixmap, self.FRAME), (0, 0))
        self.assertEqual(label_to_frame((500, 225), label, pixmap, self.FRAME), (720, 405))

    def test_round_trip(self):
        for point in [(0, 0), (720, 405), (1300, 700), (37, 801)]:
            label_point = frame_to_label(point, self.LABEL, self.PIXMAP, self.FRAME)
            back = label_to_frame(label_point, self.LABEL, self.PIXMAP, self.FRAME)
            self.assertLessEqual(abs(back[0] - point[0]), 2)
            self.assertLessEqual(abs(back[1] - point[1]), 2)


class EngineStartAndPreviewTests(EngineTestCase):
    def test_start_notifies_screen(self):
        """엔진이 시작되면 changed를 보낸다 -> 화면이 통신 상태 문구를 다시 그린다 (포트 문구 버그 수정)."""
        changed = []
        self.engine.changed.connect(lambda: changed.append(1))
        self.engine.start()
        self.assertTrue(changed)

    def test_k1_slider_logs_once(self):
        """슬라이더를 움직이는 동안에는 '처리 조건 바뀜' 로그가 값마다 쌓이지 않는다."""
        self.step()
        engine_logger = logging.getLogger("engine")
        with self.assertLogs(engine_logger, level="INFO") as logs:
            for k1 in (-0.1, -0.2, -0.3):
                self.engine.set_undistort_k1(k1, preview=True)
                self.step()
            engine_logger.info("구분선")
        self.assertFalse(any("처리 조건 바뀜" in line for line in logs.output))

        with self.assertLogs(engine_logger, level="INFO") as logs:
            self.engine.end_k1_preview()
            self.engine.end_k1_preview()  # 두 번 불러도 한 번만 남는다
        changed_lines = [line for line in logs.output if "처리 조건 바뀜" in line]
        self.assertEqual(len(changed_lines), 1)
        self.assertIn("k1=-0.30", changed_lines[0])


if __name__ == "__main__":
    unittest.main()
