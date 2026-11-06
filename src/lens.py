"""
프레임 전처리 모듈: 회전 -> 처리용 축소 -> 렌즈 왜곡 보정 (22단계).

왜 필요한가
- 카메라를 1280x720으로 열면 센서 가운데만 잘려서 매트 일부만 보인다.
  1920x1080으로 열면 매트 전체가 보이지만, 큰 영상 그대로 공을 검출하면
  프레임마다 처리 시간이 길어져(평균 약 25ms) FPS가 떨어진다.
  -> 카메라는 크게(넓은 화각) 받고, 처리할 때만 줄여서(process_scale) 쓴다.
- 광각 렌즈라 화면 가장자리의 직선(매트 테두리)이 활처럼 휘어 보인다.
  4점 원근 변환은 '직선은 직선으로' 옮기는 변환이라 휜 선을 바로잡지 못해서
  매트 가운데 좌표가 어긋난다. -> 렌즈 왜곡을 먼저 펴 준다(undistort_k1).

처리 순서 (이 순서가 중요하다)
    1) 회전(rotate): 카메라가 옆으로/거꾸로 달렸을 때 바로 세운다.
    2) 축소(process_scale): 예) 0.75면 1920x1080 -> 1440x810.
       축소를 왜곡 보정보다 먼저 해야 다음 단계(remap)가 처리할 픽셀 수도 줄어든다.
    3) 왜곡 보정(undistort_k1): 미리 만들어 둔 '좌표 지도(map)'로 cv2.remap만 한다.
    (실제 코드에서는 k1 ≠ 0일 때 2)와 3)을 remap 한 번으로 합쳐서 한다 - process() 설명 참고)

이 모듈이 돌려준 프레임이 엔진 전체의 기준이 된다. 공 검출 좌표, 시작점,
캘리브레이션 모서리, 화면 표시, 프로토콜 ALIGN corners가 모두
'전처리가 끝난 프레임' 기준의 픽셀 좌표다.

렌즈 왜곡 모델 (간단 버전)
- 카메라 행렬(렌즈의 초점거리·중심)은 정확히 재지 않고 간단히 가정한다:
  fx = fy = 프레임 폭, cx/cy = 프레임 가운데.
- 왜곡 계수는 k1 하나만 쓴다: [k1, 0, 0, 0, 0].
  k1 < 0: 가운데가 부풀어 보이는 '술통형' 왜곡(광각 렌즈에서 흔함)을 편다.
  k1 > 0: 반대로 가장자리가 오므라드는 '실패형' 왜곡을 편다.
  k1 = 0: 보정하지 않는다 (원본 그대로, 계산도 건너뜀).
  어느 쪽이 맞는지는 설정창의 슬라이더를 움직이며 매트 테두리가
  곧게 펴지는 값을 눈으로 찾는다.
"""

import cv2
import numpy as np

# config/settings.json의 camera 항목에 값이 없을 때 쓸 기본값 (예전 설정 파일과 호환)
DEFAULT_PROCESS_SCALE = 0.75
DEFAULT_UNDISTORT_K1 = 0.0
# process_scale로 허용하는 범위. 너무 작으면 공이 몇 픽셀밖에 안 돼 검출이 불가능하고,
# 1보다 크게 키우는 것은 의미가 없다(정보는 늘지 않고 처리만 느려진다).
MIN_PROCESS_SCALE = 0.1
MAX_PROCESS_SCALE = 1.0

# 왜곡을 편 뒤 화면 배율은 new_camera_matrix_for()가 정한다: 원본의 상하좌우 끝(매트 양 끝)이
# 화면 안에 남도록 전체를 줄인다. 그래서 가장자리에 검은 빈 곳이 생길 수 있는데, 검출에는
# 영향이 없다. (cv2.getOptimalNewCameraMatrix는 왜곡이 큰 이 렌즈에서 값이 튀어서 쓰지 않는다)

# rotate 설정값(도) -> cv2.rotate()에 넘길 코드. 0은 회전 없음.
ROTATE_CODES = {
    0: None,
    90: cv2.ROTATE_90_CLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
}


def _to_float(value, default: float) -> float:
    """설정값을 실수로 바꾼다. 숫자가 아니면(문자열, null 등) 기본값을 쓴다."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def clamp_process_scale(value) -> float:
    """process_scale 값을 허용 범위(0.1~1.0) 안으로 맞춘다. 잘못된 값이면 기본값."""
    scale = _to_float(value, DEFAULT_PROCESS_SCALE)
    if scale <= 0:
        return DEFAULT_PROCESS_SCALE
    return min(MAX_PROCESS_SCALE, max(MIN_PROCESS_SCALE, scale))


class FramePreprocessor:
    """카메라 원본 프레임을 엔진이 쓸 '처리 프레임'으로 바꾸는 클래스.

    사용법:
        pre = FramePreprocessor(camera_settings)   # config/settings.json의 camera 항목
        frame = pre.process(raw_frame)             # 매 프레임 호출

    왜곡 보정용 좌표 지도(map)는 계산이 무거워서 매 프레임 만들지 않는다.
    프레임 크기나 k1이 바뀔 때만 다시 만들고, 평소에는 만들어 둔 지도로
    cv2.remap만 한다. (몇 번 만들었는지 map_build_count로 확인할 수 있다 - 테스트용)
    """

    def __init__(self, settings: dict | None = None):
        settings = settings or {}
        rotate = settings.get("rotate", 0)
        # 0/90/180/270이 아닌 잘못된 값이면 회전하지 않는다.
        self.rotate = rotate if rotate in ROTATE_CODES else 0
        self.process_scale = clamp_process_scale(settings.get("process_scale", DEFAULT_PROCESS_SCALE))
        self.undistort_k1 = _to_float(settings.get("undistort_k1", DEFAULT_UNDISTORT_K1), DEFAULT_UNDISTORT_K1)

        # 왜곡 보정 지도 캐시. _map_key = (폭, 높이, k1): 이 값이 같으면 지도를 다시 쓴다.
        self._map_key: tuple | None = None
        self._map_x: np.ndarray | None = None
        self._map_y: np.ndarray | None = None
        self.map_build_count = 0

    # ------------------------------------------------------------------
    # 값 바꾸기 (설정창의 슬라이더가 실시간으로 부른다)
    # ------------------------------------------------------------------
    def set_undistort_k1(self, k1: float) -> None:
        """k1을 바꾼다. 다음 process() 호출 때 지도가 새 값으로 다시 만들어진다."""
        self.undistort_k1 = _to_float(k1, DEFAULT_UNDISTORT_K1)

    def set_process_scale(self, scale: float) -> None:
        """처리용 축소 비율을 바꾼다 (프레임 크기가 바뀌므로 지도도 다시 만들어진다)."""
        self.process_scale = clamp_process_scale(scale)

    # ------------------------------------------------------------------
    # 처리
    # ------------------------------------------------------------------
    def process(self, frame: np.ndarray) -> np.ndarray:
        """원본 프레임에 회전 -> 축소 -> 왜곡 보정을 차례로 적용해 돌려준다.

        왜곡 보정을 할 때(k1 ≠ 0)는 '축소'와 '왜곡 보정'을 remap 한 번으로 합쳐서 한다.
        remap은 '결과 화면의 픽셀 수'만큼 일하므로, 축소된 크기의 결과를 원본에서 바로
        뽑아내면 resize를 따로 할 필요가 없다 (현장 측정: 0.75배에서 6.6ms -> 3.4ms).
        결과는 '축소한 뒤 왜곡 보정'한 것과 같다 (지도를 축소된 크기 기준으로 만들기 때문).
        """
        frame = self._rotate(frame)
        if self.undistort_k1 == 0:
            return self._scale(frame)  # 보정하지 않음: 축소만 (k1=0, 축소 1.0이면 원본 그대로)
        return self._scale_and_undistort(frame)

    def _rotate(self, frame: np.ndarray) -> np.ndarray:
        code = ROTATE_CODES.get(self.rotate)
        if code is None:
            return frame
        return cv2.rotate(frame, code)

    def scaled_size(self, width: int, height: int) -> tuple[int, int]:
        """원본(회전 후) 크기 -> 처리 프레임 크기. 예) 1920x1080, 0.75 -> 1440x810"""
        if self.process_scale >= 1.0:
            return width, height
        return (
            max(1, round(width * self.process_scale)),
            max(1, round(height * self.process_scale)),
        )

    def _scale(self, frame: np.ndarray) -> np.ndarray:
        height, width = frame.shape[:2]
        new_size = self.scaled_size(width, height)
        if new_size == (width, height):
            return frame  # 1.0이면 줄이지 않는다 (복사도 하지 않음)
        # INTER_AREA: 영상을 '줄일 때' 가장 깔끔한 보간 방법 (작은 공의 색이 뭉개지지 않는다)
        return cv2.resize(frame, new_size, interpolation=cv2.INTER_AREA)

    def _scale_and_undistort(self, frame: np.ndarray) -> np.ndarray:
        height, width = frame.shape[:2]
        out_width, out_height = self.scaled_size(width, height)
        key = (width, height, out_width, out_height, self.undistort_k1)
        if key != self._map_key:
            # 크기나 k1이 바뀌었을 때만 지도를 새로 만든다 (무거운 계산은 여기서 한 번만)
            self._map_x, self._map_y = build_undistort_maps(
                out_width, out_height, self.undistort_k1, source_size=(width, height)
            )
            self._map_key = key
            self.map_build_count += 1
        # remap: 지도에 적힌 대로 원본의 픽셀을 옮겨 담는다 (가벼운 계산)
        return cv2.remap(frame, self._map_x, self._map_y, interpolation=cv2.INTER_LINEAR)


def camera_matrix_for(width: int, height: int) -> np.ndarray:
    """간단히 가정한 카메라 행렬: fx = fy = 프레임 폭, (cx, cy) = 프레임 가운데."""
    focal = float(width)
    return np.array(
        [[focal, 0.0, width / 2.0], [0.0, focal, height / 2.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )


def undistorted_radius(distorted: float, k1: float) -> float:
    """왜곡된 반지름 r_d에 해당하는 '펴진' 반지름 r_u를 구한다.

    k1 모델: r_d = r_u * (1 + k1 * r_u²)  (반지름은 초점거리로 나눈 값, 단위 없음)
    이 식을 r_u에 대해 직접 풀기 어려워서 이분법(범위를 반씩 줄여 가며 찾기)으로 푼다.

    k1 < 0(술통형)이면 r_u가 어느 값(r_peak = 1/√(-3·k1))을 넘으면 r_d가 오히려 줄어드는
    '접힘'이 생긴다. 그 너머는 모델이 성립하지 않으므로 r_peak에서 멈춘다
    (= 그보다 바깥의 원본 픽셀은 보정 후 화면에 나올 수 없다).
    """
    if distorted <= 0 or k1 == 0:
        return max(distorted, 0.0)
    if k1 < 0:
        high = 1.0 / np.sqrt(-3.0 * k1)  # 접히기 직전의 r_u
        if high * (1 + k1 * high * high) <= distorted:
            return float(high)
    else:
        high = distorted  # k1 > 0이면 r_u < r_d
    low = 0.0
    for _ in range(60):  # 60번이면 오차가 사실상 0이 된다
        mid = (low + high) / 2
        if mid * (1 + k1 * mid * mid) < distorted:
            low = mid
        else:
            high = mid
    return float((low + high) / 2)


def new_camera_matrix_for(width: int, height: int, k1: float) -> np.ndarray:
    """왜곡을 편 뒤 화면에 쓸 카메라 행렬: 원본의 상하좌우 끝이 화면 안에 들어오게 배율을 정한다.

    처음에는 cv2.getOptimalNewCameraMatrix(alpha)를 썼는데, 이 카메라처럼 왜곡이 큰
    광각 렌즈(k1이 약 -0.55보다 작음)에서는 결과가 k1에 따라 크게 튀고 가로·세로 배율이
    달라지기까지 해서(fx ≠ fy) 쓸 수 없었다. 그래서 직접 계산한다.

    방법: 원본 화면의 왼쪽·오른쪽 끝 가운데 점과 위·아래 끝 가운데 점이, 왜곡을 편 뒤
    어디로 가는지 계산하고, 그 점들이 모두 화면 안에 들어오도록 화면 전체를 같은 비율로
    줄인다(배율 scale). 매트 양 끝(화면 왼쪽·오른쪽 끝)이 잘려 나가지 않는 것이 목적이다.
    네 귀퉁이는 더 멀리 늘어나므로 일부 잘릴 수 있지만, 매트가 아닌 바닥 부분이다.
    """
    camera_matrix = camera_matrix_for(width, height)
    focal = camera_matrix[0, 0]
    half_extents = [width / 2.0, height / 2.0]  # 가운데에서 좌우 끝 / 위아래 끝까지의 거리(px)
    scale = min(
        (extent / focal) / undistorted_radius(extent / focal, k1) for extent in half_extents
    )
    new_matrix = camera_matrix.copy()
    new_matrix[0, 0] = focal * scale
    new_matrix[1, 1] = focal * scale
    return new_matrix


def build_undistort_maps(width: int, height: int, k1: float, source_size: tuple[int, int] | None = None):
    """k1 하나짜리 왜곡 모델로 cv2.remap에 쓸 좌표 지도(map_x, map_y)를 만든다.

    width, height: 처리 프레임(축소된 크기). 렌즈 모델(카메라 행렬)은 이 크기 기준이다.
    source_size: remap에 실제로 넣을 원본 크기. 처리 크기와 다르면(축소를 함께 할 때)
        지도 좌표를 원본 크기에 맞게 늘려서, 원본에서 바로 '축소 + 왜곡 보정' 결과를 뽑는다.

    new_camera_matrix_for()로 '보정 후 화면'의 배율을 정해서, 왜곡을 편 뒤에도
    원본 화면의 상하좌우 끝(매트 양 끝 포함)이 화면 안에 남게 한다.
    """
    camera_matrix = camera_matrix_for(width, height)
    dist_coeffs = np.array([k1, 0.0, 0.0, 0.0, 0.0], dtype=np.float64)
    new_matrix = new_camera_matrix_for(width, height, k1)
    # 먼저 실수(float) 지도로 만든다: 결과 화면의 각 픽셀이 '처리 크기 원본'의 어디에서 오는지
    map_x, map_y = cv2.initUndistortRectifyMap(
        camera_matrix, dist_coeffs, None, new_matrix, (width, height), cv2.CV_32FC1
    )
    if source_size is not None and source_size != (width, height):
        # 처리 크기 좌표 -> 실제 원본 크기 좌표 (예: 0.75배였다면 1/0.75배로 늘림)
        map_x *= source_size[0] / width
        map_y *= source_size[1] / height
    # CV_16SC2 형식(정수 지도)으로 바꾸면 remap이 더 빠르다.
    return cv2.convertMaps(map_x, map_y, cv2.CV_16SC2)
