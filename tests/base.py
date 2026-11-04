"""
엔진 테스트의 공통 준비 코드 (여러 테스트 파일이 함께 쓴다).

EngineTestCase는 테스트마다
- 임시 폴더에 테스트 전용 설정을 만들고 (사용자의 config/settings.json은 건드리지 않는다)
- 가짜 카메라로 VisionEngine을 만들고
- 엔진이 보내는 시그널을 모아 둔다.
"""

import shutil
import tempfile
import unittest

from PySide6.QtCore import QCoreApplication

from src.engine import VisionEngine
from tests.fake_camera import FakeCamera, make_test_settings

# Qt 객체(시그널·타이머·TCP 서버)를 만들려면 Qt 애플리케이션이 하나 있어야 한다.
qt_app = QCoreApplication.instance() or QCoreApplication([])


class EngineTestCase(unittest.TestCase):
    """엔진 테스트의 공통 준비: 임시 설정 + 가짜 카메라 + 시그널 기록."""

    # 하위 클래스가 설정의 일부를 바꾸고 싶을 때 채운다 (예: {"protocol": {...}})
    settings_overrides: dict = {}

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.settings_patch = make_test_settings(self.tmp_dir, **self.settings_overrides)
        self.settings_patch.start()

        self.camera = FakeCamera()
        self.engine = VisionEngine(camera=self.camera)

        # 엔진이 보내는 시그널을 모아 둔다
        self.frames = []
        self.finished = []
        self.trajectory_updates = []
        self.camera_lost = []
        self.engine.frame_processed.connect(self.frames.append)
        self.engine.tracking_finished.connect(self.finished.append)
        self.engine.trajectory_updated.connect(lambda: self.trajectory_updates.append(1))
        self.engine.camera_unavailable.connect(lambda: self.camera_lost.append(1))

        self.clock = 100.0  # 테스트용 가짜 시계 (초)

    def tearDown(self):
        self.engine.shutdown()
        self.settings_patch.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def step(self, ball=None, dt=0.05):
        """공 위치를 정하고(ball 생략 시 그대로) 시계를 dt초 흘린 뒤 프레임 1장을 처리한다.

        공을 화면에서 없애려면 step() 전에 self.camera.ball = None 으로 직접 지정한다.
        """
        if ball is not None:
            self.camera.ball = ball
        self.clock += dt
        self.engine.process_frame(now=self.clock)
