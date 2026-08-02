"""
카메라 입력을 담당하는 모듈.

OpenCV VideoCapture로 USB 카메라 영상을 받아온다.
카메라가 연결되지 않았거나 끊겼을 때 예외를 던지지 않고,
open()/read_frame()이 그냥 실패(False/None)를 반환하도록 만들어서
UI 쪽에서 안내 문구를 보여줄 수 있게 한다.

해상도·FPS·MJPG 사용 여부는 config/settings.json에서 읽어온다.
카메라 기종이 바뀌어 지원하는 해상도/FPS가 달라져도, 이 파일이 아니라
config/settings.json 값만 바꾸면 된다.
"""

import cv2
import numpy as np

from src.config import load_settings

# config/settings.json에 "camera" 항목이 없거나 일부 값이 빠져 있을 때 쓸 기본값
DEFAULT_CAMERA_SETTINGS = {
    "width": 1920,
    "height": 1080,
    "fps": 90,
    "use_mjpg": True,
}


class Camera:
    """USB 카메라를 열고 프레임을 읽어오는 클래스."""

    def __init__(self, camera_index: int = 0, settings: dict | None = None):
        self._camera_index = camera_index
        self._capture: cv2.VideoCapture | None = None

        # settings를 직접 넘기지 않으면 config/settings.json에서 읽는다.
        loaded = settings if settings is not None else load_settings().get("camera", {})
        self._settings = {**DEFAULT_CAMERA_SETTINGS, **loaded}

    def open(self) -> bool:
        """카메라를 연다. 성공하면 True, 실패하면 False를 반환한다."""
        # Windows에서는 CAP_DSHOW를 쓰면 카메라가 없을 때 더 빨리 실패한다.
        capture = cv2.VideoCapture(self._camera_index, cv2.CAP_DSHOW)
        if not capture.isOpened():
            capture.release()
            return False

        self._apply_settings(capture)
        self._capture = capture
        self._print_applied_settings(capture)
        return True

    def _apply_settings(self, capture: cv2.VideoCapture) -> None:
        """카메라에 해상도·FPS·압축 방식을 요청한다.

        순서가 중요하다: FOURCC(MJPG)를 가장 먼저 설정해야 한다.
        카메라가 무압축(YUY2) 상태로 열린 뒤에 높은 해상도/FPS를 요청하면
        USB 대역폭이 부족해 드라이버가 값을 조용히 낮춰버리는 경우가 많다.
        MJPG로 먼저 압축을 걸어야 1080p·90fps 같은 스펙이 실제로 적용된다.
        """
        if self._settings.get("use_mjpg", True):
            fourcc = cv2.VideoWriter_fourcc(*"MJPG")
            capture.set(cv2.CAP_PROP_FOURCC, fourcc)

        width = self._settings.get("width")
        height = self._settings.get("height")
        if width:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        if height:
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

        fps = self._settings.get("fps")
        if fps:
            capture.set(cv2.CAP_PROP_FPS, fps)

    def _print_applied_settings(self, capture: cv2.VideoCapture) -> None:
        """카메라가 실제로 받아들인 해상도·FOURCC·FPS를 터미널에 출력한다.

        요청한 값(config/settings.json)과 실제 적용된 값이 다를 수 있어서
        (카메라가 해당 스펙을 지원하지 않으면 드라이버가 다른 값으로 맞춘다)
        반드시 실제 값을 다시 읽어서 보여준다.
        """
        actual_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = capture.get(cv2.CAP_PROP_FPS)
        fourcc_code = int(capture.get(cv2.CAP_PROP_FOURCC))
        fourcc_text = "".join(chr((fourcc_code >> (8 * i)) & 0xFF) for i in range(4))
        print(
            f"[카메라] 적용된 설정 -> 해상도: {actual_width}x{actual_height}, "
            f"FOURCC: {fourcc_text}, FPS: {actual_fps:.1f}"
        )

    def is_opened(self) -> bool:
        return self._capture is not None and self._capture.isOpened()

    def read_frame(self) -> np.ndarray | None:
        """프레임 한 장을 읽는다. 카메라가 없거나 읽기에 실패하면 None."""
        if not self.is_opened():
            return None
        ok, frame = self._capture.read()
        if not ok:
            return None
        return frame

    def release(self) -> None:
        """카메라 장치를 반납한다."""
        if self._capture is not None:
            self._capture.release()
            self._capture = None
