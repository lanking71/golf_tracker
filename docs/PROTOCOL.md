# 앱 ↔ 비전엔진 통신 규격

## 연결
- TCP 소켓. 비전엔진(PC)이 서버이고 앱이 접속합니다. 기본 포트 **9000** (`config/settings.json`의 `protocol.port`).
- 메시지는 **JSON 객체 1개 + 줄바꿈(`\n`)** 입니다. 한 줄에 하나씩 주고받습니다.
- 인코딩은 UTF-8입니다.
- 좌표는 모두 **매트 mm 좌표**이고 원점은 매트 **좌상단**, x는 오른쪽(가로), y는 아래쪽(세로)입니다.
- 접속한 모든 앱에 같은 메시지가 전송됩니다.

Flutter 예시:
```dart
final socket = await Socket.connect(pcIp, 9000);
socket.cast<List<int>>().transform(utf8.decoder).transform(const LineSplitter())
    .listen((line) => handle(jsonDecode(line)));
socket.write(jsonEncode({'cmd': 'STATUS'}) + '\n');
```

## 일반 흐름
```
ALIGN → SET_ADDR → (공 올려둠) → INIT → ADD → [STATUS 실시간] → RESULT
```
다음 퍼팅은 `INIT`부터 다시 하면 됩니다 (결과가 나온 FINISHED 상태에서도 가능).

## 앱 → 엔진 명령

응답은 `result`가 `"OK"` 또는 `"NG"`이고, NG에는 `reason`이 붙습니다.

### ALIGN: 매트 정렬(캘리브레이션)
```json
{"cmd":"ALIGN","type":"REQ","width_mm":4500,"height_mm":2000}
```
- `type`은 `"REQ"` (생략 가능), `width_mm`/`height_mm`은 선택입니다 (없으면 현재 설정값 유지).
- 녹색 매트 모서리를 자동 검출합니다. 응답: `{"cmd":"ALIGN","result":"OK","width_mm":4500,"height_mm":2000}`
- NG 이유: `bad_state`(추적 중), `bad_size`, `no_frame`(카메라 영상 없음), `mat_not_found`(매트를 못 찾음), `bad_type`

### SET_ADDR: 어드레스 위치 지정
```json
{"cmd":"SET_ADDR","x":1000,"y":2000,"radius_mm":100}
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
- NG 이유: `not_calibrated`, `no_ball`(공이 안 보이거나 매트 밖), `bad_state`(추적 중), `bad_type`

### ADD: 추적 시작
```json
{"cmd":"ADD","type":"BALL"}
```
- 시작점이 등록된 상태(READY)에서 추적을 시작합니다.
- 응답: `{"cmd":"ADD","result":"OK"}`
- NG 이유: `bad_state`(INIT을 안 했거나 이미 추적 중), `bad_type`

### STATUS: 상태 조회
```json
{"cmd":"STATUS"}
```
응답은 아래 STATUS 메시지와 같은 형식입니다.

## 엔진 → 앱 (엔진이 먼저 보내는 메시지)

### STATUS: 상태·좌표
```json
{"cmd":"STATUS","state":"TRACKING","calibrated":true,"x":1532.4,"y":980.1,"t":1.24,"speed":850.3}
```
- `state`: `IDLE`(대기) / `CALIBRATING` / `READY`(시작점 등록됨) / `TRACKING`(추적 중) / `FINISHED`(추적 완료)
- `x`, `y`: 공의 현재 위치(mm). 공이 안 보이거나 매트 밖이면 `null`
- `t`: 추적 시작 후 경과 시간(초). TRACKING일 때만 값이 있고 아니면 `null`
- `speed`: 속도(mm/초). TRACKING일 때만 포함됩니다.
- 전송 시점: 상태가 바뀔 때, 그리고 TRACKING 중에는 초당 최대 30회 (`protocol.status_hz`).

### ADDR: 어드레스 위치 진입/이탈
```json
{"cmd":"ADDR","event":"IN"}
{"cmd":"ADDR","event":"OUT"}
```
- `SET_ADDR`로 지정한 위치의 반경 안으로 공이 들어오면 `IN`, 반경의 1.2배 밖으로 나가면 `OUT`을 1번씩 보냅니다.

### RESULT: 추적 결과
```json
{"cmd":"RESULT","reason":"STOP","start":[1125.6,1137.6],"stop":[1852.5,1144.8],
 "distance_mm":727.0,"duration":0.7,"avg_speed":1038.5,"max_speed":1061.1,"straightness":1.0}
```
- `reason`: `STOP`(공이 멈춤) / `LOST`(공이 화면을 벗어남)
- 점이 2개 미만이라 통계를 못 구하면 `distance_mm` 등은 `null`입니다.
- `straightness`: 0~1, 1에 가까울수록 직선으로 굴러간 것입니다.

## 오류 응답
- JSON이 아닌 줄: `{"cmd":"ERROR","reason":"bad_json"}`
- 모르는 명령: `{"cmd":"ERROR","reason":"unknown_cmd","received":"FOO"}`

## 설정 (`config/settings.json`의 `protocol`)
| 키 | 기본값 | 뜻 |
|---|---|---|
| `enabled` | `true` | 서버 사용 여부 |
| `port` | `9000` | 접속 포트 |
| `addr_radius_mm` | `100` | 어드레스 영역 반경 (SET_ADDR에서 radius_mm으로 덮어쓸 수 있음) |
| `status_hz` | `30` | 추적 중 STATUS 최대 전송 횟수(초당) |
