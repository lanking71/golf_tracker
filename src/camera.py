"""
카메라 입력을 담당하는 모듈.

OpenCV VideoCapture로 USB 카메라 영상을 받아온다.
카메라가 연결되지 않았거나 끊겼을 때 예외를 던지지 않고,
open()/read_frame()이 그냥 실패(False/None)를 반환하도록 만들어서
UI 쪽에서 안내 문구를 보여줄 수 있게 한다.

해상도·FPS·MJPG 사용 여부·회전 각도는 config/settings.json에서
읽어온다. 카메라 기종이 바뀌거나 카메라가 옆으로/거꾸로 설치돼도,
이 파일이 아니라 config/settings.json 값만 바꾸면 된다.

카메라가 90/180/270도 돌아간 채로 설치된 경우를 위해 "rotate" 값을
지원한다. read_frame()이 돌려주는 프레임이 이미 회전이 적용된
상태라서, 검출·궤적·시작점 등 이후 모든 코드는 이 회전된 프레임
기준의 좌표를 그대로 쓰면 된다.
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
    "rotate": 0,
}

# rotate 설정값(도) -> cv2.rotate()에 넘길 코드. 0은 회전 없음(회전 코드 없음).
ROTATE_CODES = {
    0: None,
    90: cv2.ROTATE_90_CLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
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
        params = self._build_open_params()
        # Windows에서는 CAP_DSHOW를 쓰면 카메라가 없을 때 더 빨리 실패한다.
        capture = cv2.VideoCapture(self._camera_index, cv2.CAP_DSHOW, params)
        if not capture.isOpened():
            capture.release()
            return False

        self._capture = capture
        self._print_applied_settings(capture)
        return True

    def _build_open_params(self) -> list:
        """VideoCapture.open()에 한 번에 넘길 (속성, 값) 쌍 목록을 만든다.

        카메라를 먼저 기본(YUY2) 모드로 연 뒤 set()으로 MJPG를 요청하면,
        DirectShow가 이미 잡은 영상 모드를 다시 협상하지 않아서 계속
        YUY2로 남고 대역폭 부족으로 FPS가 크게 떨어지는 문제가 있었다.
        그래서 open() 호출 시점에 FOURCC(MJPG)·해상도·FPS를 함께
        전달해야 처음부터 고속 MJPEG 모드로 연결된다.
        """
        params: list = []

        if self._settings.get("use_mjpg", True):
            fourcc = cv2.VideoWriter_fourcc(*"MJPG")
            params += [cv2.CAP_PROP_FOURCC, fourcc]

        width = self._settings.get("width")
        height = self._settings.get("height")
        if width:
            params += [cv2.CAP_PROP_FRAME_WIDTH, width]
        if height:
            params += [cv2.CAP_PROP_FRAME_HEIGHT, height]

        fps = self._settings.get("fps")
        if fps:
            params += [cv2.CAP_PROP_FPS, fps]

        return params

    def _print_applied_settings(self, capture: cv2.VideoCapture) -> None:
        """카메라가 실제로 받아들인 해상도·FOURCC·FPS를 터미널에 출력한다.

        요청한 값(config/settings.json)과 실제 적용된 값이 다를 수 있어서
        (카메라가 해당 스펙을 지원하지 않으면 드라이버가 다른 값으로 맞춘다)
        반드시 실제 값을 다시 읽어서 보여준다.
        """
        actual_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = capture.get(cv2.CAP_PROP_FPS)
        # 일부 카메라/드라이버는 이 속성을 지원하지 않아 -1을 돌려준다.
        # 그럴 때는 화면 상단 FPS 배지(실측값)를 참고하라고 안내한다.
        fps_text = f"{actual_fps:.1f}" if actual_fps > 0 else "드라이버 미보고 (화면 FPS 배지 참고)"
        fourcc_code = int(capture.get(cv2.CAP_PROP_FOURCC))
        fourcc_text = "".join(chr((fourcc_code >> (8 * i)) & 0xFF) for i in range(4))
        rotate_degrees = self._settings.get("rotate", 0)
        print(
            f"[카메라] 적용된 설정 -> 해상도: {actual_width}x{actual_height}, "
            f"FOURCC: {fourcc_text}, FPS: {fps_text}, 회전: {rotate_degrees}도"
        )

    def is_opened(self) -> bool:
        return self._capture is not None and self._capture.isOpened()

    def read_frame(self) -> np.ndarray | None:
        """프레임 한 장을 읽는다. 카메라가 없거나 읽기에 실패하면 None.

        config/settings.json의 rotate 값(0/90/180/270)이 설정돼 있으면
        회전을 적용한 뒤 돌려준다. 이후 코드는 이미 회전된 프레임만 보게 된다.
        """
        if not self.is_opened():
            return None
        ok, frame = self._capture.read()
        if not ok:
            return None
        return self._apply_rotation(frame)

    def _apply_rotation(self, frame: np.ndarray) -> np.ndarray:
        rotate_degrees = self._settings.get("rotate", 0)
        rotate_code = ROTATE_CODES.get(rotate_degrees)
        if rotate_code is None:
            # rotate: 0이거나, 0/90/180/270이 아닌 잘못된 값이면 회전하지 않는다.
            return frame
        return cv2.rotate(frame, rotate_code)

    def release(self) -> None:
        """카메라 장치를 반납한다."""
        if self._capture is not None:
            self._capture.release()
            self._capture = None
