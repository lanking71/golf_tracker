"""
앱(Flutter)에서 온 JSON 명령을 처리하고, 엔진 상태를 앱에 보내는 모듈.

protocol_server.py가 '통신'(JSON 주고받기)을 맡고, 이 모듈은 그 위에서
'무슨 뜻인지'(명령 처리)를 맡는다. Qt에 의존하지 않는 순수 파이썬이라
UI 없이도 테스트할 수 있다. 상세 규격은 docs/PROTOCOL.md 참고.

앱 -> 엔진 명령 (cmd 필드):
    ALIGN     매트 자동 정렬(캘리브레이션). 선택: width_mm, height_mm
    SET_ADDR  어드레스(공을 놓을) 위치를 매트 mm 좌표로 지정. 선택: radius_mm
    INIT      type=BALL: 지금 보이는 공을 시작점으로 등록
    ADD       type=BALL: 추적 시작(READY -> TRACKING)
    STATUS    현재 상태를 1회 조회

엔진 -> 앱 (먼저 보내는 것):
    STATUS    상태 변화 시 + 추적 중에는 초당 최대 status_hz번(기본 30)
    ADDR      event=IN/OUT: 공이 어드레스 위치 안으로 들어오거나 나갈 때 1회
    RESULT    추적이 끝났을 때(정지/이탈) 결과 1회

좌표는 모두 매트 mm 좌표이고 원점은 좌상단이다.
"""

import math
import time

from src.calibration import detect_mat_corners
from src.stats import compute_trajectory_stats
from src.tracker import LOST_REASON, STOP_REASON, TrackerState, TrajectoryPoint

# config/settings.json의 "protocol" 항목에 없을 때 쓸 기본값
DEFAULT_ADDR_RADIUS_MM = 100
DEFAULT_STATUS_HZ = 30
# 어드레스 영역을 벗어났다고 볼 때의 여유 배수 (경계에서 IN/OUT이 깜빡이는 것을 막는다)
ADDR_OUT_MARGIN = 1.2

# 종료 사유를 앱에 보낼 때 쓰는 영문 코드
FINISH_REASON_CODES = {STOP_REASON: "STOP", LOST_REASON: "LOST"}


def _is_number(value) -> bool:
    """JSON 숫자인지 (True/False는 파이썬에서 int지만 숫자로 보지 않는다)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class ProtocolHandler:
    """앱 명령 처리 + 엔진 상태 실시간 전송."""

    def __init__(
        self,
        tracker,
        calibration,
        send,
        get_detection,
        get_raw_frame,
        on_change=None,
        settings: dict | None = None,
    ):
        """
        tracker, calibration: 메인 창과 같은 객체 (버튼과 앱 명령이 같은 상태 규칙을 쓴다)
        send(dict): 앱으로 메시지를 보내는 함수 (ProtocolServer.send)
        get_detection(): 가장 최근 프레임의 (매트 안) 공 검출 결과, 없으면 None
        get_raw_frame(): 그리기 전의 가장 최근 카메라 프레임, 없으면 None
        on_change(): 앱 명령으로 상태가 바뀌었을 때 UI를 갱신하라고 알리는 함수
        """
        self.tracker = tracker
        self.calibration = calibration
        self._send = send
        self._get_detection = get_detection
        self._get_raw_frame = get_raw_frame
        self._on_change = on_change or (lambda: None)

        loaded = settings or {}
        self.addr_radius_mm: float = loaded.get("addr_radius_mm", DEFAULT_ADDR_RADIUS_MM)
        status_hz = loaded.get("status_hz", DEFAULT_STATUS_HZ)
        self._push_interval = 1.0 / status_hz if status_hz > 0 else 0.0

        # 어드레스 위치(mm)와 공이 지금 그 안에 있는지
        self._addr_mm: tuple[float, float] | None = None
        self._addr_inside = False
        # 실시간 STATUS 전송용
        self._last_state: TrackerState | None = None
        self._last_push_time = 0.0
        self._track_t0: float | None = None  # 추적 시작 시각
        self._prev_sample: tuple[float, float, float] | None = None  # (x, y, 시각)

    # ------------------------------------------------------------------
    # 앱 -> 엔진 명령 처리
    # ------------------------------------------------------------------
    def handle(self, message: dict) -> None:
        """앱에서 온 명령 하나를 처리하고 응답을 보낸다."""
        cmd = message.get("cmd")
        handlers = {
            "ALIGN": self._cmd_align,
            "SET_ADDR": self._cmd_set_addr,
            "INIT": self._cmd_init,
            "ADD": self._cmd_add,
            "STATUS": self._cmd_status,
        }
        handler = handlers.get(cmd) if isinstance(cmd, str) else None
        if handler is None:
            self._send({"cmd": "ERROR", "reason": "unknown_cmd", "received": cmd})
            return
        handler(message)

    def _ng(self, cmd: str, reason: str) -> None:
        self._send({"cmd": cmd, "result": "NG", "reason": reason})

    def _cmd_align(self, message: dict) -> None:
        """매트를 자동 검출해 캘리브레이션한다. 크기(width_mm/height_mm)가 오면 함께 적용한다."""
        if message.get("type", "REQ") != "REQ":
            return self._ng("ALIGN", "bad_type")
        if not self.tracker.can("calibrate"):
            return self._ng("ALIGN", "bad_state")

        width = message.get("width_mm", self.calibration.mat_width_mm)
        height = message.get("height_mm", self.calibration.mat_height_mm)
        if not (_is_number(width) and _is_number(height) and width > 0 and height > 0):
            return self._ng("ALIGN", "bad_size")

        frame = self._get_raw_frame()
        if frame is None:
            return self._ng("ALIGN", "no_frame")
        corners = detect_mat_corners(
            frame, self.calibration.mat_hsv_lower, self.calibration.mat_hsv_upper
        )
        if corners is None:
            return self._ng("ALIGN", "mat_not_found")

        # 모두 성공한 뒤에만 상태를 바꾼다 (실패했는데 크기만 바뀌는 일이 없도록).
        self.calibration.set_mat_size(width, height)
        self.calibration.set_corners(corners)
        self.calibration.save()
        self.tracker.calibrate()
        self._addr_inside = False
        self._send({
            "cmd": "ALIGN",
            "result": "OK",
            "width_mm": self.calibration.mat_width_mm,
            "height_mm": self.calibration.mat_height_mm,
        })
        self._on_change()

    def _cmd_set_addr(self, message: dict) -> None:
        """어드레스(공을 놓을) 위치를 mm 좌표로 저장한다."""
        x, y = message.get("x"), message.get("y")
        if not (_is_number(x) and _is_number(y)):
            return self._ng("ADDR", "bad_value")
        if not (0 <= x <= self.calibration.mat_width_mm and 0 <= y <= self.calibration.mat_height_mm):
            return self._ng("ADDR", "out_of_range")
        radius = message.get("radius_mm", self.addr_radius_mm)
        if not (_is_number(radius) and radius > 0):
            return self._ng("ADDR", "bad_value")

        self._addr_mm = (float(x), float(y))
        self.addr_radius_mm = radius
        self._addr_inside = False  # 새 위치 기준으로 IN/OUT을 다시 판정한다
        self._send({"cmd": "ADDR", "result": "OK"})

    def _cmd_init(self, message: dict) -> None:
        """지금 보이는 공을 시작점으로 등록한다."""
        if message.get("type", "BALL") != "BALL":
            return self._ng("INIT", "bad_type")
        if not self.calibration.is_calibrated():
            return self._ng("INIT", "not_calibrated")
        detection = self._get_detection()
        if detection is None:
            return self._ng("INIT", "no_ball")
        if not self.tracker.register_start_position(detection.x, detection.y):
            return self._ng("INIT", "bad_state")

        real = self.calibration.pixel_to_real(detection.x, detection.y)
        self._send({"cmd": "INIT", "result": "OK", "x": round(real[0], 1), "y": round(real[1], 1)})
        self._on_change()

    def _cmd_add(self, message: dict) -> None:
        """추적을 시작한다 (시작 위치가 등록된 READY 상태에서만)."""
        if message.get("type", "BALL") != "BALL":
            return self._ng("ADD", "bad_type")
        if not self.tracker.start_tracking():
            return self._ng("ADD", "bad_state")
        self._send({"cmd": "ADD", "result": "OK"})
        self._on_change()

    def _cmd_status(self, message: dict) -> None:
        self._send(self._build_status(time.time()))

    # ------------------------------------------------------------------
    # 엔진 -> 앱 실시간 전송
    # ------------------------------------------------------------------
    def on_frame(self, detection, now: float) -> None:
        """매 프레임 호출. 상태 변화/추적 중 좌표/어드레스 IN·OUT을 앱에 보낸다.

        detection은 매트 안에서 검출된 공(없으면 None)이다.
        """
        state = self.tracker.state
        state_changed = state != self._last_state
        self._last_state = state

        if state == TrackerState.TRACKING:
            if self._track_t0 is None:
                self._track_t0 = now
        else:
            self._track_t0 = None
            self._prev_sample = None

        self._update_addr_event(detection)

        # 상태가 바뀌었을 때는 바로, 추적 중에는 정해 둔 주기로 보낸다.
        due = state == TrackerState.TRACKING and now - self._last_push_time >= self._push_interval
        if state_changed or due:
            self._last_push_time = now
            self._send(self._build_status(now, detection))

    def on_finished(self, reason: str) -> None:
        """추적이 정지/이탈로 끝났을 때 호출. 결과(RESULT)를 1회 보낸다."""
        trajectory = []
        for p in self.tracker.trajectory:
            real = self.calibration.pixel_to_real(p.x, p.y)
            if real is not None:
                trajectory.append(TrajectoryPoint(x=real[0], y=real[1], timestamp=p.timestamp))

        start = self._to_mm(self.tracker.start_position)
        stop_pixel = self.tracker.stop_position
        if stop_pixel is None and self.tracker.trajectory:
            last = self.tracker.trajectory[-1]
            stop_pixel = (last.x, last.y)
        stop = self._to_mm(stop_pixel)

        stats = compute_trajectory_stats(trajectory, start, stop)
        self._send({
            "cmd": "RESULT",
            "reason": FINISH_REASON_CODES.get(reason, "STOP"),
            "start": start,
            "stop": stop,
            "distance_mm": round(stats.total_distance, 1) if stats else None,
            "duration": round(stats.duration, 3) if stats else None,
            "avg_speed": round(stats.average_speed, 1) if stats else None,
            "max_speed": round(stats.max_speed, 1) if stats else None,
            "straightness": round(stats.straightness, 3) if stats else None,
        })

    def _to_mm(self, pixel) -> list[float] | None:
        """픽셀 좌표를 [x_mm, y_mm]로 바꾼다 (좌표가 없거나 캘리브레이션 전이면 None)."""
        if pixel is None:
            return None
        real = self.calibration.pixel_to_real(*pixel)
        if real is None:
            return None
        return [round(real[0], 1), round(real[1], 1)]

    def _build_status(self, now: float, detection=None) -> dict:
        """STATUS 메시지를 만든다. detection이 없으면 가장 최근 검출 결과를 쓴다."""
        if detection is None:
            detection = self._get_detection()
        position = self._to_mm((detection.x, detection.y)) if detection is not None else None

        status = {
            "cmd": "STATUS",
            "state": self.tracker.state.name,
            "calibrated": self.calibration.is_calibrated(),
            "x": position[0] if position else None,
            "y": position[1] if position else None,
            "t": None,
        }
        if self.tracker.state == TrackerState.TRACKING:
            t0 = self._track_t0 if self._track_t0 is not None else now
            status["t"] = round(now - t0, 3)
            status["speed"] = self._speed_mm_per_s(position, now)
        return status

    def _speed_mm_per_s(self, position, now: float) -> float | None:
        """직전에 보낸 좌표와의 거리로 속도(mm/초)를 계산한다."""
        if position is None:
            return None
        speed = None
        if self._prev_sample is not None:
            px, py, pt = self._prev_sample
            if now > pt:
                speed = round(math.hypot(position[0] - px, position[1] - py) / (now - pt), 1)
        self._prev_sample = (position[0], position[1], now)
        return speed

    def _update_addr_event(self, detection) -> None:
        """공이 어드레스 위치 안으로 들어오면 IN, 벗어나면 OUT을 1회씩 보낸다."""
        if self._addr_mm is None or detection is None:
            return
        real = self.calibration.pixel_to_real(detection.x, detection.y)
        if real is None:
            return
        distance = math.hypot(real[0] - self._addr_mm[0], real[1] - self._addr_mm[1])
        if not self._addr_inside and distance <= self.addr_radius_mm:
            self._addr_inside = True
            self._send({"cmd": "ADDR", "event": "IN"})
        elif self._addr_inside and distance > self.addr_radius_mm * ADDR_OUT_MARGIN:
            self._addr_inside = False
            self._send({"cmd": "ADDR", "event": "OUT"})
