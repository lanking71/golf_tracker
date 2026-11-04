"""
로그 설정(src/log_setup.py) 테스트: 로그 파일 생성, 중복 방지, 크기 제한(회전), 실패 시 계속 동작.
"""

import contextlib
import io
import logging
import shutil
import tempfile
import unittest
from pathlib import Path

from src.log_setup import remove_handlers, setup_logging
from tests.fake_camera import make_test_settings


class LogSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.root = logging.getLogger()
        self.original_level = self.root.level
        self.settings_patch = None

    def tearDown(self):
        remove_handlers()  # 파일을 풀어 줘야 임시 폴더를 지울 수 있다
        self.root.setLevel(self.original_level)
        if self.settings_patch is not None:
            self.settings_patch.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def use_settings(self, **overrides):
        """테스트 전용 설정(logging 항목 등)을 쓰게 한다 (사용자의 config/settings.json은 건드리지 않는다)."""
        self.settings_patch = make_test_settings(self.tmp_dir, **overrides)
        self.settings_patch.start()

    def log_path(self) -> Path:
        return Path(self.tmp_dir) / "logs" / "engine.log"

    def marked_handlers(self):
        return [h for h in self.root.handlers if getattr(h, "_ball_tracker_handler", False)]

    def test_creates_folder_and_writes_utf8_log(self):
        self.use_settings()
        used = setup_logging(self.log_path(), console=False)
        self.assertEqual(used, self.log_path())

        logging.getLogger("engine").info("한글 로그 시험")
        for handler in self.marked_handlers():
            handler.flush()
        text = self.log_path().read_text(encoding="utf-8")
        self.assertIn("한글 로그 시험", text)
        self.assertIn("INFO", text)
        self.assertIn("engine", text)

    def test_level_comes_from_settings(self):
        self.use_settings(logging={"level": "WARNING"})
        setup_logging(self.log_path(), console=False)
        logging.getLogger("engine").info("조용히")
        logging.getLogger("engine").warning("경고 메시지")
        for handler in self.marked_handlers():
            handler.flush()
        text = self.log_path().read_text(encoding="utf-8")
        self.assertNotIn("조용히", text)
        self.assertIn("경고 메시지", text)

    def test_calling_twice_does_not_duplicate_lines(self):
        self.use_settings()
        setup_logging(self.log_path(), console=False)
        setup_logging(self.log_path(), console=False)
        self.assertEqual(len(self.marked_handlers()), 1)

        logging.getLogger("engine").info("한 번만")
        for handler in self.marked_handlers():
            handler.flush()
        self.assertEqual(self.log_path().read_text(encoding="utf-8").count("한 번만"), 1)

    def test_old_logs_are_rotated_and_limited(self):
        self.use_settings(logging={"max_bytes": 300, "backup_count": 2})
        setup_logging(self.log_path(), console=False)
        for i in range(60):
            logging.getLogger("engine").info("회전 시험 줄 번호 %d 입니다", i)
        for handler in self.marked_handlers():
            handler.flush()
        folder = self.log_path().parent
        self.assertTrue((folder / "engine.log").exists())
        self.assertTrue((folder / "engine.log.1").exists())
        self.assertTrue((folder / "engine.log.2").exists())
        self.assertFalse((folder / "engine.log.3").exists())  # 보관 개수를 넘는 건 지워진다

    def test_unwritable_location_does_not_crash(self):
        self.use_settings()
        blocker = Path(self.tmp_dir) / "not_a_folder"
        blocker.write_text("파일", encoding="utf-8")  # 폴더 자리에 파일이 있어서 만들 수 없다
        warning = io.StringIO()
        with contextlib.redirect_stderr(warning):
            used = setup_logging(blocker / "engine.log", console=False)
        self.assertIsNone(used)  # 파일 로그는 포기하지만 예외는 던지지 않는다
        self.assertIn("로그 파일을 열 수 없습니다", warning.getvalue())  # 대신 경고를 알린다
        logging.getLogger("engine").info("계속 동작")  # 로그 호출도 문제없다

    def test_console_handler_is_optional(self):
        self.use_settings()
        setup_logging(self.log_path(), console=False)
        self.assertEqual(len(self.marked_handlers()), 1)  # 파일만
        setup_logging(self.log_path(), console=True)
        kinds = {type(h).__name__ for h in self.marked_handlers()}
        self.assertIn("RotatingFileHandler", kinds)


if __name__ == "__main__":
    unittest.main()
