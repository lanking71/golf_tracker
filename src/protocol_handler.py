"""
메인 컨트롤 프로그램에서 온 JSON 명령을 처리하고, 엔진 상태를 보내는 모듈 (프로토콜 v2).

protocol_server.py가 '통신'(JSON 주고받기)을 맡고, 이 모듈은 그 위에서
'무슨 뜻인지'(명령 처리)를 맡는다. Qt에 의존하지 않는 순수 파이썬이라
UI 없이도 테스트할 수 있다. 상세 규격은 docs/PROTOCOL.md 참고.

컨트롤 -> 엔진 명령 (cmd 필드). 모든 명령에 선택 필드 "id"를 붙이면
응답에 그대로 되돌려 준다 (요청과 응답을 짝지을 때 쓴다):
    ALIGN       매트 정렬(캘리브레이션). 기본은 자동 검출. 선택: width_mm, height_mm,
                corners(모서리 4점을 직접 전달), use_saved(저장된 캘리브레이션 재사용)
    SET_ADDR    어드레스(공을 놓을) 위치를 매트 mm 좌표로 지정. 선택: radius_mm
    INIT        type=BALL: 지금 보이는 공을 시작점으로 등록
    ADD         type=BALL: 추적 대기(READY -> ARMED). 공이 움직이면 자동으로 TRACKING
    ABORT       추적 대기/추적을 멈추고 READY로 되돌아감
    RESET       시작점과 궤적을 지우고 READY로 되돌아감
    STATUS      현재 상태를 1회 조회
    GET_RESULT  마지막 추적 결과를 다시 받음
    SAVE        이번 결과를 엔진의 DB(SQLite)에 저장 (FINISHED 상태에서만)
    PROFILE     공 검출 프로필(공 색 설정) 조회·변경. 선택: name
    PING       연결 확인 (PONG으로 응답)

엔진 -> 컨트롤 (먼저 보내는 것, 접속한 모든 앱에 전송):
    HELLO       접속 직후 1회 (그 앱에만): 프로토콜 버전과 현재 상태
    STATUS      상태 변화 시 + 추적 중에는 초당 최대 status_hz번(기본 30)
    TRACK       event=START: 공이 움직여 추적이 시작된 순간 1회
    ADDR        event=IN/OUT: 공이 어드레스 위치 안으로 들어오거나 나갈 때 1회
    RESULT      추적이 끝났을 때(정지/이탈) 결과 1회

명령에 대한 응답은 그 명령을 보낸 앱에게만 간다. 좌표는 모두 매트 mm 좌표이고
원점은 좌상단이다.
"""

import math
import time

from src.stats import compute_point_series, compute_trajectory_stats
from src.tracker import LOST_REASON, STOP_REASON, TrackerState

# 프로토콜 버전 (HELLO로 컨트롤에 알려 준다). 호환되는 추가(새 명령·필드)는 끝자리를,
# 호환되지 않는 변경은 앞자리를 올린다. 2.1: ALIGN corners/use_saved, SAVE, PROFILE 추가.
PROTOCOL_VERSION = "2.1"

# config/settings.json의 "protocol" 항목에 없을 때 쓸 기본값
DEFAULT_ADDR_RADIUS_MM = 100
DEFAULT_STATUS_HZ = 30
DEFAULT_RESULT_POINTS = True
# 어드레스 영역을 벗어났다고 볼 때의 여유 배수 (경계에서 IN/OUT이 깜빡이는 것을 막는다)
ADDR_OUT_MARGIN = 1.2

# 종료 사유를 앱에 보낼 때 쓰는 영문 코드
FINISH_REASON_CODES = {STOP_REASON: "STOP", LOST_REASON: "LOST"}

# RESULT.points 한 점의 각 값이 무엇인지 (컨트롤이 읽기 쉽도록 RESULT에 함께 실어 보낸다)
POINT_FIELDS = ["x", "y", "t", "dist", "speed"]

# 요청에 "id"가 없었다는 표시 (id가 null/0/빈 문자열이어도 그대로 되돌려 주려고 None과 구분한다)
_NO_ID = object()


def _is_number(value) -> bool:
    """JSON 숫자인지 (True/False는 파이썬에서 int지만 숫자로 보지 않는다)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class ProtocolHandler:
    """컨트롤 명령 처리 + 엔진 상태 실시간 전송."""

    def __init__(self, engine, send, settings: dict | None = None, send_to=None):
        """
        engine: VisionEngine. 화면 버튼과 앱 명령이 같은 엔진·Tracker 메서드를 쓰므로
                상태 규칙이 항상 같다. 상태를 바꾸는 명령이 끝나면 engine.changed 시그널로
                화면(있다면)이 다시 그리도록 알린다.
        send(dict): 접속한 모든 앱에 보내는 함수 (ProtocolServer.send)
        send_to(client, dict): 특정 앱 한 대에게만 보내는 함수 (ProtocolServer.send_to).
                명령에 대한 응답에 쓴다. 없으면 응답도 모두에게 보낸다.
        """
        self._engine = engine
        self._send = send
        self._send_to = send_to

        loaded = settings or {}
        self.addr_radius_mm: float = loaded.get("addr_radius_mm", DEFAULT_ADDR_RADIUS_MM)
        status_hz = loaded.get("status_hz", DEFAULT_STATUS_HZ)
        self._push_interval = 1.0 / status_hz if status_hz > 0 else 0.0
        # RESULT에 궤적 점 전체를 실을지 (False면 요약값만 보낸다)
        self.result_points: bool = loaded.get("result_points", DEFAULT_RESULT_POINTS)

        # 어드레스 위치(mm)와 공이 지금 그 안에 있는지
        self._addr_mm: tuple[float, float] | None = None
        self._addr_inside = False
        # 실시간 STATUS 전송용
        self._last_state: TrackerState | None = None
        self._last_push_time = 0.0
        self._track_t0: float | None = None  # 추적 시작 시각
        self._prev_sample: tuple[float, float, float] | None = None  # (x, y, 시각)
        # 마지막 추적 결과 (GET_RESULT로 다시 받을 수 있다)
        self._last_result: dict | None = None

        # 지금 처리 중인 명령의 응답 방법 (handle()이 명령마다 정하고 끝나면 되돌린다)
        self._reply = self._send
        self._request_id = _NO_ID

    @property
    def tracker(self):
        return self._engine.tracker

    @property
    def calibration(self):
        # 설정을 다시 불러오면 엔진의 calibration 객체가 새로 바뀌므로,
        # 객체를 저장해 두지 않고 매번 엔진에서 꺼내 쓴다.
        return self._engine.calibration

    # ------------------------------------------------------------------
    # 컨트롤 -> 엔진 명령 처리
    # ------------------------------------------------------------------
    def handle(self, message: dict, client=None) -> None:
        """컨트롤에서 온 명령 하나를 처리하고 응답을 보낸다.

        client는 그 명령을 보낸 앱(ProtocolServer가 넘겨 준 소켓)이다. 주어지면
        응답을 그 앱에만 보내고, 없으면 모두에게 보낸다. 명령에 "id"가 있으면
        응답에 그대로 붙여서 돌려준다.
        """
        self._reply = self._reply_function(client)
        self._request_id = message.get("id", _NO_ID)
        try:
            cmd = message.get("cmd")
            handlers = {
                "ALIGN": self._cmd_align,
                "SET_ADDR": self._cmd_set_addr,
                "INIT": self._cmd_init,
                "ADD": self._cmd_add,
                "ABORT": self._cmd_abort,
                "RESET": self._cmd_reset,
                "STATUS": self._cmd_status,
                "SAVE": self._cmd_save,
                "PROFILE": self._cmd_profile,
                "GET_RESULT": self._cmd_get_result,
                "PING": self._cmd_ping,
            }
            handler = handlers.get(cmd) if isinstance(cmd, str) else None
            if handler is None:
                self._respond({"cmd": "ERROR", "reason": "unknown_cmd", "received": cmd})
                return
            handler(message)
        finally:
            self._reply = self._send
            self._request_id = _NO_ID

    def _reply_function(self, client):
        """응답을 보낼 함수: 보낸 앱을 알면 그 앱에만, 모르면 모두에게."""
        if client is not None and self._send_to is not None:
            return lambda msg: self._send_to(client, msg)
        return self._send

    def _respond(self, message: dict) -> None:
        """명령에 대한 응답을 보낸다 (요청의 id가 있으면 붙이고, 요청한 앱에게만)."""
        if self._request_id is not _NO_ID:
            message = {**message, "id": self._request_id}
        self._reply(message)

    def _ng(self, cmd: str, reason: str) -> None:
        self._respond({"cmd": cmd, "result": "NG", "reason": reason})

    def on_client_connected(self, client) -> None:
        """새 앱이 접속했을 때: 인사(HELLO)와 현재 상태(STATUS)를 그 앱에만 보낸다.

        앱은 접속 직후 상태를 따로 물어보지 않아도 엔진이 준비됐는지,
        캘리브레이션이 돼 있는지 바로 알 수 있다.
        """
        reply = self._reply_function(client)
        reply({
            "cmd": "HELLO",
            "version": PROTOCOL_VERSION,
            "state": self.tracker.state.name,
            "calibrated": self.calibration.is_calibrated(),
            "camera": self._camera_text(),
            "profile": self._engine.profiles()[0],
        })
        reply(self._build_status(time.time()))

    def _cmd_align(self, message: dict) -> None:
        """매트를 자동 검출해 캘리브레이션한다. 크기(width_mm/height_mm)가 오면 함께 적용한다."""
        if message.get("type", "REQ") != "REQ":
            return self._ng("ALIGN", "bad_type")

        # 모서리를 얻는 방법: corners(직접 전달) > use_saved(저장값) > 자동 검출(기본).
        # corners와 use_saved를 함께 보내면 어느 쪽인지 알 수 없어서 거절한다.
        corners = message.get("corners")
        use_saved = message.get("use_saved", False)
        if not isinstance(use_saved, bool):
            return self._ng("ALIGN", "bad_option")
        if corners is not None and use_saved:
            return self._ng("ALIGN", "bad_option")

        # 실제 검출·검증·저장은 엔진이 한다 (화면의 '캘리브레이션' 버튼과 같은 부품을 쓴다).
        width, height = message.get("width_mm"), message.get("height_mm")
        if corners is not None:
            method, result = "corners", self._engine.align_corners(corners, width, height)
        elif use_saved:
            method, result = "saved", self._engine.align_saved(width, height)
        else:
            method, result = "auto", self._engine.align_auto(width, height)
        if result != "OK":
            return self._ng("ALIGN", result)

        self._addr_inside = False
        self._respond({
            "cmd": "ALIGN",
            "result": "OK",
            "method": method,
            "width_mm": self.calibration.mat_width_mm,
            "height_mm": self.calibration.mat_height_mm,
            # 실제로 쓰인 모서리(픽셀). 자동 검출 결과를 컨트롤이 보관했다가
            # 나중에 corners로 다시 보낼 수 있다.
            "corners": [list(corner) for corner in self.calibration.corners],
        })
        self._engine.changed.emit()

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
        self._respond({"cmd": "ADDR", "result": "OK"})

    def _cmd_init(self, message: dict) -> None:
        """지금 보이는 공을 시작점으로 등록한다."""
        if message.get("type", "BALL") != "BALL":
            return self._ng("INIT", "bad_type")
        if not self.calibration.is_calibrated():
            return self._ng("INIT", "not_calibrated")
        # 결과 코드는 engine.START_* 와 같은 문자열이다 (engine이 이 파일을 import하므로
        # 여기서 engine 상수를 가져오면 순환 import가 되어 문자열을 직접 비교한다).
        result = self._engine.register_start()
        if result == "bad_state":
            return self._ng("INIT", "bad_state")
        if result != "OK":
            # 공이 안 보이거나 매트 밖이라 무시된 경우 (앱에는 둘 다 '공 없음'으로 알린다)
            return self._ng("INIT", "no_ball")

        x, y = self._to_mm(self.tracker.start_position)
        self._respond({"cmd": "INIT", "result": "OK", "x": x, "y": y})
        self._engine.changed.emit()

    def _cmd_add(self, message: dict) -> None:
        """추적 대기(ARMED)로 들어간다 (시작 위치가 등록된 READY 상태에서만).

        실제 추적은 공이 움직이는 순간 자동으로 시작되고, 그때 TRACK START
        이벤트가 간다. 그 전에는 정지 판정을 하지 않는다.
        """
        if message.get("type", "BALL") != "BALL":
            return self._ng("ADD", "bad_type")
        if not self._engine.start_tracking():
            return self._ng("ADD", "bad_state")
        self._respond({"cmd": "ADD", "result": "OK"})
        self._engine.changed.emit()

    def _cmd_abort(self, message: dict) -> None:
        """추적 대기(ARMED)나 추적(TRACKING)을 멈추고 READY로 돌아간다 (시작점은 남는다)."""
        if not self._engine.abort():
            return self._ng("ABORT", "bad_state")
        self._respond({"cmd": "ABORT", "result": "OK"})
        self._engine.changed.emit()

    def _cmd_reset(self, message: dict) -> None:
        """시작점·궤적·마지막 결과를 지우고 READY로 돌아간다 (캘리브레이션은 남는다)."""
        if not self._engine.reset():
            return self._ng("RESET", "bad_state")
        self._last_result = None
        self._respond({"cmd": "RESET", "result": "OK"})
        self._engine.changed.emit()

    def _cmd_save(self, message: dict) -> None:
        """이번 추적 결과를 엔진의 SQLite DB에 저장한다 (추적이 끝난 FINISHED 상태에서만).

        응답의 record_id는 DB에 저장된 행 번호이다. (요청의 id를 되돌려 주는
        "id" 필드와 이름이 겹치지 않게 record_id라고 부른다.)
        """
        if not self.tracker.can("save"):
            return self._ng("SAVE", "bad_state")
        status, record_id = self._engine.save_result()
        if status != "OK":
            return self._ng("SAVE", status)
        self._respond({"cmd": "SAVE", "result": "OK", "record_id": record_id})

    def _cmd_profile(self, message: dict) -> None:
        """공 검출 프로필(공 색 설정)을 조회하거나 바꾼다.

        name이 없으면 목록을 돌려주고, name(프로필 키)이 있으면 그 프로필로 바꾼다.
        """
        if "name" in message and message["name"] is not None:
            name = message["name"]
            if not isinstance(name, str):
                return self._ng("PROFILE", "bad_value")
            result = self._engine.select_profile(name)
            if result != "OK":
                return self._ng("PROFILE", result)
            self._engine.changed.emit()

        active, profiles = self._engine.profiles()
        self._respond({
            "cmd": "PROFILE",
            "result": "OK",
            "active": active,
            "profiles": [
                {"key": key, "name": profile.get("name", key)} for key, profile in profiles.items()
            ],
        })

    def _cmd_status(self, message: dict) -> None:
        self._respond(self._build_status(time.time()))

    def _cmd_get_result(self, message: dict) -> None:
        """마지막 추적 결과를 다시 보낸다 (결과 메시지를 놓쳤을 때 쓴다)."""
        if self._last_result is None:
            return self._ng("RESULT", "no_result")
        self._respond({**self._last_result, "result": "OK"})

    def _cmd_ping(self, message: dict) -> None:
        """연결이 살아 있는지 확인한다. ts는 엔진의 현재 시각(epoch 밀리초)이다."""
        self._respond({"cmd": "PONG", "ts": int(time.time() * 1000)})

    # ------------------------------------------------------------------
    # 엔진 -> 컨트롤 실시간 전송 (접속한 모든 앱에게)
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

    def on_tracking_started(self, started_at: float | None) -> None:
        """공이 움직여 추적이 시작됐을 때 TRACK START 이벤트를 1회 보낸다.

        started_at은 공이 마지막으로 가만히 있던 시각(time.time() 값)으로,
        스윙 직후의 정확한 출발 시각이다. 이후 STATUS의 t(경과 시간)도 이 시각을
        0으로 해서 센다.
        """
        if started_at is None:
            started_at = time.time()
        self._track_t0 = started_at
        self._prev_sample = None
        self._send({"cmd": "TRACK", "event": "START", "ts": int(started_at * 1000)})

    def on_finished(self, reason: str) -> None:
        """추적이 정지/이탈로 끝났을 때 호출. 결과(RESULT)를 만들어 1회 보내고 보관해 둔다."""
        trajectory = self._engine.trajectory_to_real() if self.calibration.is_calibrated() else []

        start = self._to_mm(self.tracker.start_position)
        stop_pixel = self.tracker.stop_position
        if stop_pixel is None and self.tracker.trajectory:
            last = self.tracker.trajectory[-1]
            stop_pixel = (last.x, last.y)
        stop = self._to_mm(stop_pixel)

        stats = compute_trajectory_stats(trajectory, start, stop)
        result = {
            "cmd": "RESULT",
            "reason": FINISH_REASON_CODES.get(reason, "STOP"),
            "ts": int(time.time() * 1000),
            "start": start,
            "stop": stop,
            "distance_mm": round(stats.total_distance, 1) if stats else None,
            "duration": round(stats.duration, 3) if stats else None,
            "avg_speed": round(stats.average_speed, 1) if stats else None,
            "max_speed": round(stats.max_speed, 1) if stats else None,
            "straightness": round(stats.straightness, 3) if stats else None,
        }
        if self.result_points:
            # 점마다 [x_mm, y_mm, 경과시간_초, 누적거리_mm, 속도_mm/초]
            result["fields"] = POINT_FIELDS
            result["points"] = [
                [round(x, 1), round(y, 1), round(t, 3), round(dist, 1), round(speed, 1)]
                for x, y, t, dist, speed in compute_point_series(trajectory)
            ]
        self._last_result = result
        self._send(result)

    def _to_mm(self, pixel) -> list[float] | None:
        """픽셀 좌표를 [x_mm, y_mm]로 바꾼다 (좌표가 없거나 캘리브레이션 전이면 None)."""
        if pixel is None:
            return None
        real = self.calibration.pixel_to_real(*pixel)
        if real is None:
            return None
        return [round(real[0], 1), round(real[1], 1)]

    def _camera_text(self) -> str:
        """카메라 상태 문구: 프레임을 정상적으로 읽고 있으면 OK, 아니면 LOST."""
        return "OK" if self._engine.camera_ok else "LOST"

    def _build_status(self, now: float, detection=None) -> dict:
        """STATUS 메시지를 만든다. detection이 없으면 가장 최근 검출 결과를 쓴다.

        ts는 이 메시지를 만든 시각(epoch 밀리초), frame은 엔진이 지금까지 처리한
        프레임 수라서, 컨트롤이 지연이나 누락을 알아챌 수 있다.
        """
        if detection is None:
            detection = self._engine.last_detection
        position = self._to_mm((detection.x, detection.y)) if detection is not None else None

        status = {
            "cmd": "STATUS",
            "state": self.tracker.state.name,
            "calibrated": self.calibration.is_calibrated(),
            "camera": self._camera_text(),
            "x": position[0] if position else None,
            "y": position[1] if position else None,
            "t": None,
        }
        if self.tracker.state == TrackerState.TRACKING:
            t0 = self._track_t0 if self._track_t0 is not None else now
            status["t"] = round(now - t0, 3)
            status["speed"] = self._speed_mm_per_s(position, now)
        status["ts"] = int(now * 1000)
        status["frame"] = self._engine.frame_count
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
