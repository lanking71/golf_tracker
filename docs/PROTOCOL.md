# 메인 컨트롤 ↔ 비전 엔진 통신 규격 (v2.2)

**메인 컨트롤(Flutter 등) 개발자에게 전달하는 문서입니다. 이 문서만 보면 연동할 수 있습니다.**

비전 엔진은 PC에서 화면 없이 돌아가며, 메인 컨트롤이 보내는 JSON 명령에 따라 캘리브레이션을 하고
공의 좌표·거리·속도를 실시간으로 돌려줍니다.

### 한눈에 보기
1. 컨트롤이 엔진 PC의 **TCP 9000번 포트**에 접속한다. (엔진이 서버)
2. 엔진이 `HELLO`와 현재 `STATUS`를 보낸다.
3. 컨트롤이 `ALIGN` → `SET_ADDR` → `INIT` → `ADD` 순서로 명령을 보낸다.
4. 공이 움직이면 엔진이 `TRACK START` → `STATUS`(실시간 좌표) → `RESULT`(거리·속도·궤적)를 보낸다.

메시지는 전부 **한 줄짜리 JSON**입니다. 아래 "연동 예제(Dart)"를 그대로 시작점으로 쓰시면 됩니다.

## 연결
- TCP 소켓. 비전 엔진(PC)이 서버이고 컨트롤이 접속합니다. 기본 포트 **9000** (엔진 쪽 설정으로 바꿀 수 있음).
- 메시지는 **JSON 객체 1개 + 줄바꿈(`\n`)** 입니다. 한 줄에 하나씩 주고받습니다. 인코딩은 UTF-8입니다.
- 접속하면 엔진이 먼저 `HELLO`와 현재 `STATUS`를 보냅니다 (그 컨트롤에만).
- 여러 컨트롤이 동시에 접속할 수 있습니다.
  - **명령에 대한 응답은 그 명령을 보낸 컨트롤에게만** 갑니다.
  - **이벤트**(실시간 `STATUS`, `TRACK`, `ADDR`, `RESULT`)는 접속한 **모든** 컨트롤에게 갑니다.

### 단위와 좌표
| 항목 | 단위 |
|---|---|
| 좌표 `x`, `y` | **mm**. 원점은 매트 **좌상단**, x는 오른쪽(가로), y는 아래쪽(세로) |
| 거리 `dist`, `distance_mm` | mm |
| 속도 `speed`, `avg_speed`, `max_speed` | mm/초 |
| 시간 `t`, `duration` | 초 (추적 시작을 0으로 센 경과 시간) |
| 시각 `ts` | epoch **밀리초** (1970-01-01 기준, 정수) |

### 요청과 응답 짝짓기: `id`
어떤 명령이든 `"id"`(숫자 또는 문자열)를 붙이면 **응답에 그대로 되돌아옵니다**. 명령을 연달아 보내도 어느 응답이 어느 명령의 것인지 알 수 있습니다.
`id`가 없으면 응답에도 없습니다. 이벤트에는 `id`가 없습니다.
```json
→ {"cmd":"INIT","type":"BALL","id":"a1"}
← {"cmd":"INIT","result":"OK","x":1125.6,"y":1137.6,"id":"a1"}
```

### 응답과 이벤트 구분하기
- 메시지에 **`id`가 있으면 내가 보낸 명령의 응답**, **없으면 엔진이 먼저 보낸 이벤트**입니다.
- 이벤트: `HELLO`, `STATUS`(실시간), `TRACK`, `CAMERA`, `ADDR`(`event` 필드가 있는 것), `RESULT`(`result` 필드가 없는 것)
- 응답은 `result`가 `"OK"`/`"NG"`입니다 (`PING`→`PONG`, 오류→`ERROR` 제외).

## 연동 예제 (Dart)
> 참고용 예시입니다. 엔진 개발 환경에는 Dart가 없어서 **직접 실행해 보지는 못했습니다.**
> 프로토콜 자체는 엔진 쪽에서 실제 TCP 소켓으로 시험했습니다.

```dart
import 'dart:async';
import 'dart:convert';
import 'dart:io';

/// 비전 엔진과 통신하는 클라이언트.
/// - request(): 명령을 보내고, 같은 id의 응답이 올 때까지 기다린다.
/// - events: 엔진이 먼저 보내는 메시지(HELLO, STATUS, TRACK, RESULT ...)
class VisionClient {
  Socket? _socket;
  int _nextId = 1;
  final _pending = <int, Completer<Map<String, dynamic>>>{};
  final _events = StreamController<Map<String, dynamic>>.broadcast();

  Stream<Map<String, dynamic>> get events => _events.stream;

  Future<void> connect(String host, {int port = 9000}) async {
    final socket = await Socket.connect(host, port, timeout: const Duration(seconds: 5));
    socket.setOption(SocketOption.tcpNoDelay, true);
    _socket = socket;
    socket
        .cast<List<int>>()
        .transform(utf8.decoder)
        .transform(const LineSplitter()) // 줄 단위로 잘라 준다 (쪼개져 온 데이터도 처리)
        .listen(_onLine, onDone: _onClosed, onError: (_) => _onClosed());
  }

  void _onLine(String line) {
    final Map<String, dynamic> message;
    try {
      message = jsonDecode(line) as Map<String, dynamic>;
    } catch (_) {
      return; // 규격에 없는 줄은 무시
    }
    final id = message['id'];
    final waiting = id is int ? _pending.remove(id) : null;
    if (waiting != null) {
      waiting.complete(message); // 내가 보낸 명령의 응답
    } else {
      _events.add(message); // 엔진이 먼저 보낸 이벤트
    }
  }

  void _onClosed() {
    _socket = null;
    for (final c in _pending.values) {
      c.completeError(const SocketException('엔진과 연결이 끊어졌습니다'));
    }
    _pending.clear();
    _events.add({'cmd': 'DISCONNECTED'}); // 엔진 메시지가 아니라 이 클라이언트가 만든 알림
  }

  /// 명령을 보내고 응답을 기다린다. 5초 안에 응답이 없으면 TimeoutException.
  Future<Map<String, dynamic>> request(Map<String, dynamic> command,
      {Duration timeout = const Duration(seconds: 5)}) {
    final socket = _socket;
    if (socket == null) {
      return Future.error(const SocketException('엔진에 연결되어 있지 않습니다'));
    }
    final id = _nextId++;
    final completer = Completer<Map<String, dynamic>>();
    _pending[id] = completer;
    socket.write('${jsonEncode({...command, 'id': id})}\n');
    return completer.future.timeout(timeout, onTimeout: () {
      _pending.remove(id);
      throw TimeoutException('${command['cmd']} 응답 없음');
    });
  }

  Future<void> close() async => _socket?.destroy();
}

// ---- 사용 예 ----
Future<void> main() async {
  final client = VisionClient();
  await client.connect('192.168.0.10'); // 엔진이 돌아가는 PC의 주소

  client.events.listen((m) {
    switch (m['cmd']) {
      case 'STATUS': // 실시간 좌표 (TRACKING 중에는 초당 최대 30번)
        print('상태 ${m['state']}  x=${m['x']} y=${m['y']} t=${m['t']} 속도=${m['speed']}');
      case 'TRACK': // 공이 움직여서 추적이 시작됨
        print('추적 시작 ts=${m['ts']}');
      case 'RESULT': // 추적 끝: 거리·속도·궤적 점
        print('거리 ${m['distance_mm']}mm, 점 ${(m['points'] as List).length}개');
    }
  });

  final align = await client.request(
      {'cmd': 'ALIGN', 'type': 'REQ', 'width_mm': 4500, 'height_mm': 2000});
  if (align['result'] != 'OK') {
    print('매트 정렬 실패: ${align['reason']}');
    return;
  }
  await client.request({'cmd': 'SET_ADDR', 'x': 1000, 'y': 1000});
  final init = await client.request({'cmd': 'INIT', 'type': 'BALL'}); // 공이 보여야 OK
  if (init['result'] != 'OK') {
    print('공을 못 찾음: ${init['reason']}');
    return;
  }
  await client.request({'cmd': 'ADD', 'type': 'BALL'}); // 이제 공이 움직이길 기다림
}
```

### 연동할 때 알아둘 점
- **Flutter Web은 안 됩니다.** 웹 앱은 브라우저 제한 때문에 일반 TCP 소켓(`dart:io`의 `Socket`)을 쓸 수 없습니다. 모바일(Android/iOS)·Windows·macOS·Linux 앱이면 위 예제가 그대로 동작합니다. 웹이 필요하면 엔진에 WebSocket 방식을 추가해야 하니 알려 주세요.
- **같은 네트워크**여야 하고, 엔진 PC의 Windows 방화벽에서 **TCP 9000 허용**이 필요합니다.
- Android 에뮬레이터에서 같은 PC의 엔진에 접속하려면 주소를 `10.0.2.2`로 씁니다. 실제 기기는 엔진 PC의 IP를 씁니다.
- Android는 `AndroidManifest.xml`에 `INTERNET` 권한이, iOS는 로컬 네트워크 접근 허용(`NSLocalNetworkUsageDescription`)이 필요할 수 있습니다.
- **접속이 끊겼다가 다시 붙으면**: 접속 직후 `HELLO`와 `STATUS`가 다시 오니 현재 상태를 바로 알 수 있고, 놓친 결과는 `GET_RESULT`로 다시 받을 수 있습니다. 엔진은 끊김과 상관없이 계속 동작합니다.
- 시각(`ts`)은 **엔진 PC의 시계** 기준입니다. 컨트롤 PC와 시계가 다를 수 있으니, 시각을 비교하기보다 `t`(추적 시작 후 경과 시간)나 `PING`의 `PONG.ts`로 차이를 재서 쓰세요.
- 실시간 `STATUS`는 초당 최대 30개이니 화면을 매번 다시 그리지 말고 최신 값만 반영하세요. 공이 안 보이면 `x`, `y`가 `null`입니다 (0이 아님에 주의).
- 명령은 **응답을 기다린 뒤** 다음 명령을 보내는 것이 안전합니다 (예: `INIT`이 OK여야 `ADD`가 가능).

## 일반 흐름
```
컨트롤                          비전 엔진
 ── (프로세스 실행) ───────▶  설정·저장된 캘리브레이션을 불러오고 포트 대기
 ◀── HELLO, STATUS ─────────  접속 직후
 ── ALIGN ────────────────▶  매트 자동 검출 → ALIGN OK
 ── SET_ADDR ─────────────▶  ADDR OK        (공이 어드레스에 들어오면 ADDR IN 이벤트)
 ── INIT BALL ─────────────▶  INIT OK        state READY
 ── ADD BALL ──────────────▶  ADD OK         state ARMED (공이 움직이길 기다림)
 ◀── TRACK START ───────────  공이 움직인 순간
 ◀── STATUS (최대 30Hz) ────  x, y, t, speed, ts, frame
 ◀── STATUS FINISHED ───────  공이 멈춤/이탈
 ◀── RESULT ────────────────  거리·속도·직진성 + 궤적 점 전체
```
다음 퍼팅은 `INIT`부터 다시 하면 됩니다 (결과가 나온 `FINISHED` 상태에서도 가능).

### 상태(`state`)
```
IDLE ─ALIGN→ CALIBRATING ─INIT→ READY ─ADD→ ARMED ─공이 움직임→ TRACKING ─멈춤/이탈→ FINISHED
                                  ▲          │ ABORT             │ ABORT
                                  └──────────┴───────────────────┘
RESET: READY / ARMED / TRACKING / FINISHED → READY (시작점과 궤적을 지움)
```
| 상태 | 뜻 |
|---|---|
| `IDLE` | 대기. (엔진 시작 직후. 저장된 캘리브레이션이 있으면 `calibrated`가 `true`) |
| `CALIBRATING` | 캘리브레이션 직후 |
| `READY` | 시작점 등록됨 |
| `ARMED` | 추적 대기. 공이 시작점에서 움직이길 기다림 (이 동안은 정지·이탈 판정을 하지 않음) |
| `TRACKING` | 공이 움직이는 중. 좌표를 기록하고 실시간으로 보냄 |
| `FINISHED` | 공이 멈췄거나 화면을 벗어나 추적이 끝남 |

## 컨트롤 → 엔진 명령

응답은 `result`가 `"OK"` 또는 `"NG"`이고, NG에는 `reason`이 붙습니다.

### ALIGN: 매트 정렬(캘리브레이션)
```json
{"cmd":"ALIGN","type":"REQ","width_mm":4500,"height_mm":2000}
```
- `type`은 `"REQ"` (생략 가능), `width_mm`/`height_mm`은 선택입니다 (없으면 현재 설정값 유지).
- 매트 모서리를 얻는 방법이 **세 가지**입니다.

| 방법 | 보내는 값 | 언제 쓰나 |
|---|---|---|
| 자동 검출 (기본) | 아무것도 안 붙임 | 카메라가 매트를 잘 보고 있을 때. 녹색 매트를 영상에서 찾는다 |
| 모서리 직접 전달 | `"corners":[[x,y],[x,y],[x,y],[x,y]]` | 자동 검출이 안 될 때(조명, 일부 가림), 또는 컨트롤이 모서리를 알고 있을 때 |
| 저장값 재사용 | `"use_saved":true` | 카메라·매트를 옮기지 않았을 때. 영상 없이도 되고 가장 빠르다 |

  ```json
  {"cmd":"ALIGN","corners":[[100,150],[1000,100],[1020,620],[90,600]],"width_mm":4500,"height_mm":2000}
  {"cmd":"ALIGN","use_saved":true}
  ```
- **`corners`**: **카메라 화면의 픽셀 좌표** 4점이며, 순서는 **좌상 → 우상 → 우하 → 좌하**(화면에서 시계 방향, 첫 점이 왼쪽 위)입니다. 엔진은 점이 4개인지, 사각형이 맞는지(볼록하고 꼬이지 않음), 순서가 맞는지, 화면 안에 있는지 확인하고 이상하면 NG로 거절합니다. (순서가 틀린 채로 적용되면 좌표가 뒤집혀 나오기 때문입니다.)
- **`use_saved`**: 엔진에 이미 저장돼 있는 캘리브레이션(이전에 성공한 `ALIGN`의 결과)을 그대로 씁니다. `width_mm`/`height_mm`을 함께 보내면 저장된 모서리로 새 크기 기준 좌표를 다시 계산합니다. `corners`와 `use_saved`를 함께 보낼 수는 없습니다.
- 응답: 성공하면 `CALIBRATING` 상태가 됩니다.
  ```json
  {"cmd":"ALIGN","result":"OK","method":"auto","width_mm":4500,"height_mm":2000,
   "corners":[[101,151],[999,101],[1019,619],[91,599]]}
  ```
  - `method`: 실제로 쓰인 방법 (`"auto"` / `"corners"` / `"saved"`)
  - `corners`: 실제로 적용된 모서리(픽셀). 자동 검출이 성공했을 때 이 값을 컨트롤이 보관해 두면, 다음에 자동 검출이 안 될 때 `corners`로 다시 보낼 수 있습니다.
- NG 이유: `bad_state`(추적 대기·추적 중), `bad_size`, `bad_type`, `bad_option`(`use_saved`가 true/false가 아님, 또는 `corners`와 `use_saved`를 함께 보냄)
  - 자동 검출: `no_frame`(카메라 영상 없음), `mat_not_found`(매트를 못 찾음)
  - `corners`: `bad_corners`(점이 4개가 아님/숫자가 아님/사각형이 아님/순서가 틀림), `corners_out_of_frame`(점이 화면 밖)
  - `use_saved`: `not_calibrated`(저장된 캘리브레이션이 없음)
- NG이면 기존 캘리브레이션과 크기는 **그대로** 유지됩니다.

### SET_ADDR: 어드레스 위치 지정
```json
{"cmd":"SET_ADDR","x":1000,"y":1000,"radius_mm":100}
```
- 공을 놓을 위치(mm)입니다. `radius_mm`은 선택이고 기본 100입니다.
- 응답: `{"cmd":"ADDR","result":"OK"}`
- NG 이유: `bad_value`, `out_of_range`(매트 크기 밖)

### INIT: 공 시작점 등록
```json
{"cmd":"INIT","type":"BALL"}
```
- 지금 보이는 공을 시작점으로 등록하고, 이전 궤적은 지웁니다.
- 응답: `{"cmd":"INIT","result":"OK","x":1125.6,"y":1137.6}`
- NG 이유: `not_calibrated`, `no_ball`(공이 안 보이거나 매트 밖), `bad_state`(추적 대기·추적 중), `bad_type`

### ADD: 추적 대기(무장)
```json
{"cmd":"ADD","type":"BALL"}
```
- 시작점이 등록된 상태(`READY`)에서 **추적 대기(`ARMED`)** 로 들어갑니다. 바로 추적이 시작되지 않습니다.
- `ARMED`에서는 공이 오래 가만히 있어도 "정지"로 판정하지 않고, 공이 안 보여도 "이탈"로 판정하지 않습니다 (퍼팅 전에 기다리는 동안 결과가 나와버리지 않도록).
- 공이 시작점에서 `tracking.arm_move_distance`(기본 8픽셀) 이상 움직이는 순간 자동으로 `TRACKING`이 되고 `TRACK START` 이벤트가 옵니다.
- 응답: `{"cmd":"ADD","result":"OK"}` (이때 상태는 `ARMED`)
- NG 이유: `bad_state`(INIT을 안 했거나 이미 대기/추적 중), `bad_type`

### ABORT: 대기·추적 중지
```json
{"cmd":"ABORT"}
```
- `ARMED`나 `TRACKING`을 멈추고 `READY`로 돌아갑니다. **시작점은 남아 있어서** 바로 `ADD`로 다시 대기할 수 있습니다.
- 응답: `{"cmd":"ABORT","result":"OK"}`. NG 이유: `bad_state`(`ARMED`/`TRACKING`이 아닐 때)

### RESET: 초기화
```json
{"cmd":"RESET"}
```
- 시작점·궤적·마지막 결과(`GET_RESULT`)를 지우고 `READY`로 돌아갑니다. 캘리브레이션은 유지됩니다.
- 응답: `{"cmd":"RESET","result":"OK"}`. NG 이유: `bad_state`(`IDLE`/`CALIBRATING`일 때)

### SAVE: 결과를 엔진 DB에 저장
```json
{"cmd":"SAVE"}
```
- 추적이 끝난(`FINISHED`) 결과를 엔진 PC의 DB(SQLite)에 저장합니다. 컨트롤이 결과를 직접 저장한다면 쓰지 않아도 됩니다.
- 응답: `{"cmd":"SAVE","result":"OK","record_id":12}` (`record_id`는 DB에 저장된 번호. 요청 `id`를 되돌려 주는 `id` 필드와는 별개입니다.)
- NG 이유: `bad_state`(`FINISHED`가 아님), `not_calibrated`, `no_trajectory`(궤적 점이 2개 미만)

### PROFILE: 공 검출 프로필(공 색) 조회·변경
```json
{"cmd":"PROFILE"}
{"cmd":"PROFILE","name":"real"}
```
- 엔진에는 공 색 설정(프로필)이 여러 개 있습니다 (예: 연습용 흰 공/연두 공, 실전용 분홍 공). `name`이 없으면 목록을 조회하고, `name`(프로필 `key`)을 주면 그 프로필로 바꿉니다. 바꾼 설정은 엔진에 저장돼 다음 실행에도 유지됩니다.
- 응답: 바꾼 뒤(또는 조회 시) 현재 상태를 돌려줍니다.
  ```json
  {"cmd":"PROFILE","result":"OK","active":"real",
   "profiles":[{"key":"practice","name":"연습용 (흰 공)"},
               {"key":"real","name":"실전용 (분홍 공)"},
               {"key":"practice_lime","name":"연습용 (연두 공)"}]}
  ```
- NG 이유: `unknown_profile`(그런 `key`가 없음), `bad_value`(`name`이 문자열이 아님), `bad_state`(`ARMED`/`TRACKING` 중에는 바꿀 수 없음. 목록 조회는 언제든 가능)

### STATUS: 상태 조회
```json
{"cmd":"STATUS"}
```
응답은 아래 `STATUS` 메시지와 같은 형식입니다 (요청한 컨트롤에게만).

### GET_RESULT: 마지막 결과 다시 받기
```json
{"cmd":"GET_RESULT"}
```
- 마지막 추적의 `RESULT`를 다시 보냅니다. 결과 메시지를 놓쳤거나, 접속이 끊겼다가 다시 접속했을 때 씁니다.
- 응답: `RESULT` 메시지에 `"result":"OK"`가 더해진 형태. 결과가 없으면 `{"cmd":"RESULT","result":"NG","reason":"no_result"}`

### PING: 연결 확인
```json
{"cmd":"PING"}
```
- 응답: `{"cmd":"PONG","ts":1730728512345}` (`ts`는 엔진의 현재 시각)

## 엔진 → 컨트롤 (엔진이 먼저 보내는 메시지)

### HELLO: 접속 인사
```json
{"cmd":"HELLO","version":"2.2","state":"IDLE","calibrated":true,"camera":"OK","profile":"practice_lime"}
```
- 접속 직후 그 컨트롤에게 1번 보내고, 바로 뒤에 현재 `STATUS`도 보냅니다.
- `version`: 프로토콜 버전. 새 명령·필드가 호환되게 추가되면 끝자리(2.2 → 2.3), 호환되지 않게 바뀌면 앞자리가 올라갑니다.
- `calibrated`: 엔진에 저장된 캘리브레이션이 있는지. `true`이면 `ALIGN`을 `use_saved`로 보내 바로 쓸 수 있습니다.
- `profile`: 지금 활성화된 공 검출 프로필의 `key` (`PROFILE` 명령 참고).

### STATUS: 상태·좌표
```json
{"cmd":"STATUS","state":"TRACKING","calibrated":true,"camera":"OK","x":1532.4,"y":980.1,
 "t":1.24,"speed":850.3,"ts":1730728512345,"frame":4821}
```
- `state`: 위의 상태 표 참고
- `camera`: `"OK"`(카메라 영상을 읽는 중) / `"LOST"`(끊김). 바뀌는 순간에는 `CAMERA` 이벤트도 옵니다.
- `x`, `y`: 공의 현재 위치(mm). 공이 안 보이거나 매트 밖이면 `null`
- `t`: 추적 시작 후 경과 시간(초). 0은 `TRACK START`의 시각(공이 마지막으로 가만히 있던 순간)입니다. `TRACKING`일 때만 값이 있고 아니면 `null`
- `speed`: 속도(mm/초). `TRACKING`일 때만 포함됩니다.
- `ts`: 이 메시지를 만든 시각(epoch 밀리초). `frame`: 엔진이 지금까지 처리한 카메라 프레임 수. 컨트롤이 지연·누락을 알아챌 때 씁니다.
- 전송 시점: 상태가 바뀔 때, 그리고 `TRACKING` 중에는 초당 최대 30회 (`protocol.status_hz`).

### TRACK: 추적 시작
```json
{"cmd":"TRACK","event":"START","ts":1730728512345}
```
- `ARMED`에서 공이 움직여 `TRACKING`이 된 순간 1번 옵니다. `STATUS(TRACKING)`보다 먼저 옵니다.
- `ts`: 추적 시작 시각 (epoch 밀리초). 공이 **마지막으로 가만히 있던 프레임**의 시각이라, 스윙 직후의 출발 시각을 정확히 알 수 있습니다.

### CAMERA: 카메라 영상 수신 상태 변화
```json
{"cmd":"CAMERA","event":"LOST"}
{"cmd":"CAMERA","event":"OK"}
```
- 카메라 영상 수신 상태가 **바뀐 순간에만** 1번씩 옵니다 (끊기면 `LOST`, 영상을 받기 시작하거나 복구되면 `OK`). 끊겨 있는 동안 `STATUS`의 `camera`도 `"LOST"`입니다.
- 엔진은 카메라가 끊기면 **1초마다 자동으로 다시 연결을 시도**합니다. 컨트롤이 할 일은 없습니다. 복구되면 `OK`가 옵니다.
- **추적 중에 카메라가 끊긴 경우**: 공이 안 보이는 것으로 보고 시간을 세다가, 2초(`tracking.lost_duration`)가 지나면 추적을 `LOST`로 끝내고 `STATUS(FINISHED)` → `RESULT`(`reason":"LOST"`)를 보냅니다. 그 전에 복구되면 추적이 이어집니다. (`ARMED` 중에 끊기면 상태는 그대로 유지됩니다.)
- 카메라가 끊긴 동안 `ALIGN`(자동 검출)과 `INIT`은 실패합니다 (`no_frame`, `no_ball`).

### ADDR: 어드레스 위치 진입/이탈
```json
{"cmd":"ADDR","event":"IN"}
{"cmd":"ADDR","event":"OUT"}
```
- `SET_ADDR`로 지정한 위치의 반경 안으로 공이 들어오면 `IN`, 반경의 1.2배 밖으로 나가면 `OUT`을 1번씩 보냅니다.

### RESULT: 추적 결과
```json
{"cmd":"RESULT","reason":"STOP","ts":1730728514001,
 "start":[1125.6,1137.6],"stop":[1852.5,1144.8],
 "distance_mm":727.0,"duration":0.7,"avg_speed":1038.5,"max_speed":1061.1,"straightness":1.0,
 "fields":["x","y","t","dist","speed"],
 "points":[[1125.6,1137.6,0.0,0.0,0.0],
           [1170.2,1139.0,0.05,44.6,892.1],
           [1240.8,1141.3,0.10,115.3,1414.0]]}
```
- `reason`: `STOP`(공이 멈춤) / `LOST`(공이 화면을 벗어남). 추적이 끝나면 `STATUS(FINISHED)`가 먼저 오고 그 다음에 `RESULT`가 옵니다.
- 요약값: `distance_mm`(총 이동 거리), `duration`(초), `avg_speed`·`max_speed`(mm/초), `straightness`(0~1, 1에 가까울수록 직선). 점이 2개 미만이라 통계를 못 구하면 `null`입니다.
- `points`: 궤적 점 목록입니다. 점 하나는 `fields` 순서대로 **`[x, y, t, dist, speed]`** 입니다.
  - `x`, `y`: 위치(mm)
  - `t`: 첫 점을 0으로 센 경과 시간(초)
  - `dist`: 출발 위치부터 그 점까지 **누적 이동 거리**(mm). 마지막 점의 `dist`가 `distance_mm`과 같습니다.
  - `speed`: 직전 점에서 그 점까지의 **순간 속도**(mm/초). 첫 점은 0입니다.
  - 첫 점은 출발 위치(`start`), 이후 점은 일정 거리 이상 움직일 때마다 기록되므로 간격이 일정하지 않습니다.
- `protocol.result_points`를 `false`로 하면 `fields`/`points` 없이 요약값만 보냅니다.

## 오류 응답
- JSON이 아닌 줄: `{"cmd":"ERROR","reason":"bad_json"}` (보낸 컨트롤에게만)
- 모르는 명령: `{"cmd":"ERROR","reason":"unknown_cmd","received":"FOO"}`
- 엔진 내부 오류: `{"cmd":"ERROR","reason":"internal_error","received":"ALIGN"}` — 명령을 처리하다 예상 못 한 오류가 났다는 뜻입니다. 엔진은 계속 동작하며 오류 내용은 엔진의 로그 파일(`logs/engine.log`)에 남습니다. 같은 명령에서 반복되면 엔진 담당자에게 로그를 전달해 주세요.

## 초기 손그림 규격과의 대응표
처음에 정리된 손그림 규격(APP ↔ Ball)을 JSON으로 옮긴 것입니다. 기존에 알고 계신 이름과 맞춰 보세요.

| 손그림 (앱 → 엔진) | 이 문서의 JSON | 손그림 (엔진 → 앱) | 이 문서의 JSON |
|---|---|---|---|
| `ALIGN REQ` | `{"cmd":"ALIGN","type":"REQ"}` | `ALIGN OK` | `{"cmd":"ALIGN","result":"OK"}` |
| `SET ADDR 1000,2000` | `{"cmd":"SET_ADDR","x":1000,"y":2000}` | `ADDR OK` | `{"cmd":"ADDR","result":"OK"}` |
| (엔진이 먼저) | | `ADDR IN` / `ADDR OUT` | `{"cmd":"ADDR","event":"IN"}` / `"OUT"` |
| `INIT BALL` | `{"cmd":"INIT","type":"BALL"}` | `INIT OK` | `{"cmd":"INIT","result":"OK","x":..,"y":..}` |
| `ADD BALL` | `{"cmd":"ADD","type":"BALL"}` | `ADD OK` | `{"cmd":"ADD","result":"OK"}` |
| `STATUS` | `{"cmd":"STATUS"}` | `STATUS` + 좌표 `(x, y)` | `{"cmd":"STATUS","state":..,"x":..,"y":..}` |

손그림에 없던 것(이 문서에서 추가): `ABORT`, `RESET`, `GET_RESULT`, `PING`, `HELLO`, `TRACK`, `RESULT`(거리·속도·궤적), 요청 `id`.

## 확인이 필요한 가정
손그림만으로는 의미가 분명하지 않아 **엔진 쪽에서 아래처럼 해석해서 구현**했습니다. 컨트롤 쪽 생각과 다르면 알려 주세요.

| 명령 | 엔진의 해석 |
|---|---|
| `SET_ADDR` | 공을 놓을 **어드레스 위치**(매트 mm 좌표) 지정. 공이 그 반경(기본 100mm) 안에 들어오면 `ADDR IN`, 나가면 `ADDR OUT` 이벤트 |
| `INIT` | **지금 보이는 공을 시작점으로 등록** (공이 안 보이면 NG `no_ball`) |
| `ADD` | **추적 준비.** 공이 움직이기 시작하면 자동으로 추적 시작 (`ARMED` 상태로 대기) |
| 좌표계 | **원점은 매트 좌상단**, x는 오른쪽, y는 아래쪽, 단위 mm. 컨트롤(게임)이 다른 좌표계(예: 홀컵 기준)를 쓰면 컨트롤에서 변환 필요 |
| 매트 크기 | `ALIGN`의 `width_mm`/`height_mm`(실전 4500×2000)을 컨트롤이 보내는 것으로 가정. 안 보내면 엔진에 저장된 값 사용 |

## 엔진 쪽 설정 (참고: 엔진 담당자가 조정)
컨트롤 개발자가 건드릴 필요는 없지만, 동작을 이해하는 데 참고하세요. 엔진의 `config/settings.json`의 `protocol` 항목입니다.

| 키 | 기본값 | 뜻 |
|---|---|---|
| `port` | `9000` | 접속 포트 |
| `status_hz` | `30` | 추적 중 `STATUS` 최대 전송 횟수(초당) |
| `addr_radius_mm` | `100` | 어드레스 영역 반경 (`SET_ADDR`의 `radius_mm`으로 덮어쓸 수 있음) |
| `result_points` | `true` | `RESULT`에 궤적 점 전체를 포함할지 |
