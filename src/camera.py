"""
카메라 입력을 담당하는 모듈.

OpenCV VideoCapture로 USB 카메라 영상을 받아온다.
카메라가 연결되지 않았거나 끊겼을 때 예외를 던지지 않고,
open()/read_frame()이 그냥 실패(False/None)를 반환하도록 만들어서
UI 쪽에서 안내 문구를 보여줄 수 있게 한다.
"""

import cv2
import numpy as np


class Camera:
    """USB 카메라를 열고 프레임을 읽어오는 클래스."""

    def __init__(self, camera_index: int = 0):
        self._camera_index = camera_index
        self._capture: cv2.VideoCapture | None = None

    def open(self) -> bool:
        """카메라를 연다. 성공하면 True, 실패하면 False를 반환한다."""
        # Windows에서는 CAP_DSHOW를 쓰면 카메라가 없을 때 더 빨리 실패한다.
        capture = cv2.VideoCapture(self._camera_index, cv2.CAP_DSHOW)
        if not capture.isOpened():
            capture.release()
            return False
        self._capture = capture
        return True

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
