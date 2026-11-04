# 메인 컨트롤 ↔ 비전 엔진 통신 규격 (v2.0)

비전 엔진은 화면 없이 돌아가며, 메인 컨트롤 프로그램이 보내는 JSON 명령에 따라 캘리브레이션을 하고
공의 좌표·거리·속도를 실시간으로 돌려줍니다.

## 연결
- TCP 소켓. 비전 엔진(PC)이 서버이고 컨트롤이 접속합니다. 기본 포트 **9000** (`config/settings.json`의 `protocol.port`).
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

### Flutter(Dart) 예시
```dart
final socket = await Socket.connect(pcIp, 9000);
socket.cast<List<int>>().transform(utf8.decoder).transform(const LineSplitter())
    .listen((line) => handle(jsonDecode(line)));
socket.write(jsonEncode({'cmd': 'STATUS', 'id': 1}) + '\n');
```

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
- 녹색 매트 모서리를 자동 검출합니다. 응답: `{"cmd":"ALIGN","result":"OK","width_mm":4500,"height_mm":2000}`
- NG 이유: `bad_state`(추적 대기·추적 중), `bad_size`, `no_frame`(카메라 영상 없음), `mat_not_found`(매트를 못 찾음), `bad_type`
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
{"cmd":"HELLO","version":"2.0","state":"IDLE","calibrated":true,"camera":"OK"}
```
- 접속 직후 그 컨트롤에게 1번 보내고, 바로 뒤에 현재 `STATUS`도 보냅니다.
- `version`: 프로토콜 버전. 호환되지 않게 바뀌면 올라갑니다.

### STATUS: 상태·좌표
```json
{"cmd":"STATUS","state":"TRACKING","calibrated":true,"camera":"OK","x":1532.4,"y":980.1,
 "t":1.24,"speed":850.3,"ts":1730728512345,"frame":4821}
```
- `state`: 위의 상태 표 참고
- `camera`: `"OK"`(카메라 영상을 읽는 중) / `"LOST"`(끊김)
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

## 설정 (`config/settings.json`의 `protocol`)
`enabled`와 `port`는 프로그램의 설정 창(`python main.py --ui` → 설정 > 프로필/매트 설정... > 앱 통신 설정)에서도 바꿀 수 있습니다.
나머지는 파일을 고친 뒤 프로그램을 다시 시작해야 적용됩니다.

| 키 | 기본값 | 뜻 |
|---|---|---|
| `enabled` | `true` | 서버 사용 여부 |
| `port` | `9000` | 접속 포트 |
| `addr_radius_mm` | `100` | 어드레스 영역 반경 (`SET_ADDR`의 `radius_mm`으로 덮어쓸 수 있음) |
| `status_hz` | `30` | 추적 중 `STATUS` 최대 전송 횟수(초당) |
| `result_points` | `true` | `RESULT`에 궤적 점 전체를 포함할지 |

## v1 → v2 변경점
v1은 컨트롤 연동 전의 시안이라 호환성은 신경 쓰지 않았습니다.
- `ADD`가 바로 추적하지 않고 `ARMED`(추적 대기)로 들어갑니다. `TRACK START` 이벤트가 추가됐습니다.
- 응답이 **요청한 컨트롤에게만** 가고, 요청의 `id`가 응답에 되돌아옵니다.
- 새 명령: `ABORT`, `RESET`, `GET_RESULT`, `PING`. 새 메시지: `HELLO`, `PONG`, `TRACK`.
- `STATUS`에 `camera`, `ts`, `frame` 필드가 추가됐습니다.
- `RESULT`에 `ts`, `fields`, `points`(좌표·시간·누적 거리·속도)가 추가됐습니다.
- 작은 메시지를 지연 없이 보내도록 TCP_NODELAY를 켰습니다.
