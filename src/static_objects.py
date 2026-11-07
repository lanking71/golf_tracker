"""
고정 물체(홀컵·티 등) 기억 모듈 (25단계).

문제: 연습용 흰 공과 흰 홀컵은 색이 같아서, 퍼팅한 공이 컵 근처까지 가면 컵을 공으로
잡는 일이 있었다. 크기(mm 기준 공 크기, src/detector.py)로 대부분 걸러지지만, 컵 테두리 일부만
보이거나 매트 위에 공 크기만 한 흰 물체(티 등)가 있으면 크기만으로는 구별할 수 없다.

해결: 공 색 범위에 걸리는 물체 중 '오랫동안 같은 자리에 가만히 있는 것'을 고정 물체로 기억해
두고, 그 자리에서 나온 후보는 공으로 보지 않는다. 컵 위치를 알고 있으니 공이 컵 근처에서
사라지면 '홀인'으로 판정할 수도 있다 (src/tracker.py).

수집 (StaticObjectCollector):
    scan_duration초(기본 3초) 동안 화면 전체의 공 색 덩어리를 매 프레임 모은다.
    같은 자리(첫 위치에서 ±still_tolerance_px, 기본 3px)에 수집 프레임의 80% 이상 계속 보인
    덩어리만 고정 물체가 된다 (움직이는 공·사람 손·잠깐 생긴 반사는 빠진다).
    빼는 것:
      - 시작점에 놓인 공 (exclude_points 근처)
      - 수집 종류가 '자동'이고 시작점을 모를 때: 공 크기 판정을 통과하는 덩어리
        (캘리브레이션 직후·엔진 시작 직후에는 공이 매트 어딘가에 놓여 있을 수 있는데, 그 공을
        고정 물체로 기억하면 그 자리에서 공을 영영 못 찾기 때문이다)
      - 너무 작은 잡티 (예상 공 면적의 30% 미만, 캘리브레이션이 없으면 20px² 미만)
    '고정 물체 다시 찾기' 버튼(수동 수집)도 시작점이 없으면 공 크기 덩어리는 기억하지 않는다
    (공을 매트에 둔 채 눌러도 공을 고정 물체로 기억하지 않게). 공 크기만 한 티·표시는 시작점을
    등록한 직후의 자동 수집(시작점의 공만 빼고 모두 기억)에서 기억된다.

번호(홀 번호) 규칙:
    고정 물체 중 '홀컵'으로 보는 것은 면적이 공 예상 면적의 hole.min_area_ratio배(기본 1.3배)
    이상인 것이다 (컵은 공보다 크게 보이고, 티는 공보다 작게 보인다). 캘리브레이션이 없으면
    공 크기를 모르므로 홀컵을 정하지 않는다.
    홀 번호는 매트 좌표로 위(y 작음) -> 아래, 같은 높이(50mm 이내)면 왼쪽 -> 오른쪽 순서로
    1번부터 붙인다. 수집할 때 한 번 정해서 저장하므로, 같은 수집 결과에서는 번호가 바뀌지 않는다.

저장: 현장 파일 settings.json의 "static_objects" 항목. 처리 조건(처리 프레임 크기·k1)을 함께
기록하고, 조건이 바뀌면(해상도·왜곡 보정 변경) 화면 좌표가 맞지 않으므로 무효로 본다.
"""

import math
from dataclasses import dataclass, field

# config/settings.json의 "static" 항목에 값이 없을 때 쓰는 기본값
DEFAULT_STATIC_SETTINGS = {
    "enabled": True,
    "scan_duration": 3.0,  # 수집 시간(초)
    "still_tolerance_px": 3,  # 이 거리(px) 안에 계속 있어야 '같은 자리'
    # 컵 테두리에 붙은 공을 인정하는 기준 (아래 StaticObjectMap.is_static_candidate 설명 참고)
    "rim_accept_ratio": 0.8,
    # 공 예상 면적의 이 배수보다 큰 덩어리는 고정 물체로 기억하지 않는다 (26단계 현장 수정).
    # 이렇게 큰 것은 크기 판정에서 이미 공이 아니라고 걸러지므로 기억할 필요가 없고, 기억하면 오히려 해가 된다:
    # 현장에서 수집할 때 매트 위에 서 있던 사람의 신발(x9.4~9.9)이 '홀컵'으로 기억돼 홀 번호가 밀렸다.
    # 컵은 x1.4~2.6으로 보인다.
    "max_area_ratio": 4.0,
}
# 수집 프레임 중 이 비율 이상 보여야 고정 물체로 본다
MIN_PRESENT_RATIO = 0.8
# 수집 중 프레임이 이 개수보다 적으면(카메라가 너무 느림) 결과를 믿을 수 없어 저장하지 않는다
MIN_SCAN_FRAMES = 5
# 이보다 작은 덩어리는 잡티로 보고 수집하지 않는다: 예상 공 면적 x 이 비율 (캘리브레이션 없으면 px²)
MIN_OBJECT_AREA_RATIO = 0.3
MIN_OBJECT_AREA_PX = 20
# 저장할 때 반경에 더하는 여유(px): 덩어리 테두리가 프레임마다 1~2px 흔들려도 확실히 덮도록
RADIUS_MARGIN_PX = 2
# 시작점 공을 뺄 때의 거리: (덩어리 반경 + 공 반경) x 이 값 안이면 시작점 공으로 본다
EXCLUDE_POINT_RATIO = 1.5
# 홀 번호를 붙일 때 '같은 높이'로 보는 y 차이(mm)
HOLE_ROW_TOLERANCE_MM = 50

# 수집 종류
SCAN_MANUAL = "manual"  # '고정 물체 다시 찾기' 버튼 (공을 치운 상태)
SCAN_AUTO = "auto"  # 캘리브레이션 직후·엔진 시작·시작점 등록 직후 자동 수집


def static_settings(loaded: dict | None) -> dict:
    """설정 파일의 static 항목과 기본값을 합친다."""
    return {**DEFAULT_STATIC_SETTINGS, **(loaded or {})}


@dataclass
class StaticObject:
    """기억한 고정 물체 하나 (처리 프레임 픽셀 좌표 기준)."""

    x: float
    y: float
    radius: float  # 덩어리 최소 외접원 반경 + 여유 (px)
    area: float  # 덩어리 면적 (px²)
    hole: int | None = None  # 홀 번호 (홀컵이 아니면 None)
    x_mm: float | None = None  # 매트 좌표 (캘리브레이션이 있을 때)
    y_mm: float | None = None

    def to_dict(self) -> dict:
        return {
            "x": round(self.x, 1),
            "y": round(self.y, 1),
            "radius": round(self.radius, 1),
            "area": round(self.area, 1),
            "hole": self.hole,
            "x_mm": None if self.x_mm is None else round(self.x_mm, 1),
            "y_mm": None if self.y_mm is None else round(self.y_mm, 1),
        }

    @staticmethod
    def from_dict(data: dict) -> "StaticObject":
        return StaticObject(
            x=float(data["x"]),
            y=float(data["y"]),
            radius=float(data["radius"]),
            area=float(data.get("area", 0.0)),
            hole=int(data["hole"]) if data.get("hole") is not None else None,
            x_mm=float(data["x_mm"]) if data.get("x_mm") is not None else None,
            y_mm=float(data["y_mm"]) if data.get("y_mm") is not None else None,
        )


@dataclass
class StaticObjectMap:
    """기억한 고정 물체 목록 + 그 목록을 만든 처리 조건."""

    objects: list[StaticObject] = field(default_factory=list)
    frame_size: tuple[int, int] | None = None
    undistort_k1: float | None = None
    # 테두리에 붙은 공 인정 기준 (DEFAULT_STATIC_SETTINGS["rim_accept_ratio"])
    rim_accept_ratio: float = DEFAULT_STATIC_SETTINGS["rim_accept_ratio"]

    # ------------------------------------------------------------------
    # 저장 / 불러오기
    # ------------------------------------------------------------------
    @staticmethod
    def from_settings(data: dict | None, rim_accept_ratio: float | None = None) -> "StaticObjectMap":
        """settings.json의 static_objects 항목을 읽는다. 없거나 깨져 있으면 빈 목록."""
        result = StaticObjectMap()
        if rim_accept_ratio is not None:
            result.rim_accept_ratio = float(rim_accept_ratio)
        if not isinstance(data, dict):
            return result
        try:
            result.objects = [StaticObject.from_dict(item) for item in data.get("objects", [])]
            size = data.get("frame_size")
            result.frame_size = (int(size[0]), int(size[1])) if size else None
            k1 = data.get("undistort_k1")
            result.undistort_k1 = float(k1) if isinstance(k1, (int, float)) and not isinstance(k1, bool) else None
        except (TypeError, ValueError, KeyError, IndexError):
            return StaticObjectMap(rim_accept_ratio=result.rim_accept_ratio)
        return result

    def to_settings(self) -> dict:
        return {
            "objects": [obj.to_dict() for obj in self.objects],
            "frame_size": list(self.frame_size) if self.frame_size else None,
            "undistort_k1": self.undistort_k1,
        }

    def matches_processing(self, frame_size, undistort_k1: float) -> bool:
        """이 목록을 만든 처리 조건(프레임 크기·k1)이 지금과 같은지. 기록이 없으면 False."""
        if self.frame_size is None or self.undistort_k1 is None:
            return False
        if abs(self.undistort_k1 - float(undistort_k1)) > 1e-9:
            return False
        if frame_size is not None and tuple(frame_size) != self.frame_size:
            return False
        return True

    # ------------------------------------------------------------------
    # 검출할 때: 고정 물체 자리의 후보 빼기
    # ------------------------------------------------------------------
    def is_static_candidate(self, x: float, y: float, ball_ok: bool, ball_radius_px: float | None) -> bool:
        """후보 (x, y)가 고정 물체 자리라서 공으로 보면 안 되는지.

        규칙 (d = 후보 중심과 고정 물체 중심의 거리, R = 고정 물체 반경, r = 공 반경):
          - d < R: 고정 물체 안쪽 -> 뺀다 (컵 자체, 또는 컵과 겹쳐 하나로 붙은 덩어리).
          - R <= d < R + r: 공이 고정 물체에 닿을 만큼 가까운 자리. 여기서는
              공 조건(크기·모양)을 통과했고 d >= (R + r) x rim_accept_ratio(기본 0.8)이면 공으로 인정,
              아니면 뺀다.
            왜 이렇게 하나: 공이 컵 테두리에 붙어 멈추면 공 중심은 컵 중심에서 약 (R + r) 떨어져 있다.
            마스크를 만들 때 모서리를 다듬는 과정(블러·모폴로지) 때문에 공 덩어리가 컵 쪽으로
            1~2px 깎이거나 늘어나서 중심이 조금 안쪽으로 보일 수 있으니 0.8배까지 봐준다.
            반대로 컵 테두리의 일부만 보이는 조각은 크기가 공과 다르거나 컵 중심에 더 가까워서 빠진다.
          - d >= R + r: 고정 물체와 상관없는 자리 -> 빼지 않는다.
        ball_radius_px: 그 위치의 공 반경(px). 모르면(캘리브레이션 없음) 고정 물체 반경의 절반으로 본다.
        """
        for obj in self.objects:
            distance = math.hypot(x - obj.x, y - obj.y)
            if distance < obj.radius:
                return True
            r = ball_radius_px if ball_radius_px else obj.radius / 2
            touch = obj.radius + r
            if distance < touch:
                if ball_ok and distance >= touch * self.rim_accept_ratio:
                    continue  # 컵 테두리에 붙어 있는 공으로 인정
                return True
        return False

    def holes(self) -> list[StaticObject]:
        """홀컵으로 본 고정 물체들 (홀 번호 순서)."""
        return sorted((obj for obj in self.objects if obj.hole is not None), key=lambda obj: obj.hole)


@dataclass
class _Track:
    """수집 중인 덩어리 하나 (같은 자리에 계속 있는지 지켜보는 중)."""

    x: float  # 처음 본 위치 (같은 자리 판단 기준)
    y: float
    sum_x: float = 0.0
    sum_y: float = 0.0
    sum_radius: float = 0.0
    sum_area: float = 0.0
    seen: int = 0
    ball_ok_count: int = 0  # 공 조건(크기·모양)을 통과한 프레임 수

    def add(self, x: float, y: float, radius: float, area: float, ball_ok: bool) -> None:
        self.sum_x += x
        self.sum_y += y
        self.sum_radius += radius
        self.sum_area += area
        self.seen += 1
        if ball_ok:
            self.ball_ok_count += 1

    @property
    def ball_like(self) -> bool:
        """보인 프레임의 절반 이상에서 공 조건을 통과했으면 '공일 수도 있는 덩어리'로 본다.

        (한 번만 통과해도 공으로 보면, 어둡게 보여 크기가 경계에 걸친 컵이 가끔 공 크기로 잡히는
        것 때문에 컵을 기억하지 못한다 - 현장에서 실제로 위·아래 컵이 그랬다)
        """
        return self.ball_ok_count * 2 >= self.seen


class StaticObjectCollector:
    """scan_duration초 동안 덩어리를 지켜보고, 계속 같은 자리에 있던 것을 고정 물체로 만든다."""

    def __init__(self, duration: float, tolerance_px: float):
        self.duration = float(duration)
        self.tolerance = float(tolerance_px)
        self.active = False
        self.kind = SCAN_AUTO
        self.started_at = 0.0
        self.frames = 0
        self._tracks: list[_Track] = []
        self._exclude_points: list[tuple[float, float]] = []

    def start(self, now: float, kind: str, exclude_points=None) -> None:
        """수집을 시작한다. exclude_points: 빼야 할 공 위치들 (시작점 등)."""
        self.active = True
        self.kind = kind
        self.started_at = now
        self.frames = 0
        self._tracks = []
        self._exclude_points = [tuple(p) for p in (exclude_points or []) if p is not None]

    def cancel(self) -> None:
        """수집을 그만둔다 (공이 움직이기 시작하는 등). 이전에 기억한 목록은 그대로 둔다."""
        self.active = False
        self._tracks = []

    def feed(self, blobs, now: float, min_area: float, inside=None) -> None:
        """이번 프레임의 덩어리 목록(DetectionCandidate)을 넣는다.

        min_area: 이보다 작은 덩어리는 잡티로 보고 무시한다.
        inside: (x, y) -> 매트 안인지. 매트 밖 물체는 공 검출에서 어차피 무시되므로 수집하지 않는다.
        """
        if not self.active:
            return
        self.frames += 1
        for blob in blobs:
            if blob.area < min_area:
                continue
            if inside is not None and not inside(blob.x, blob.y):
                continue
            track = self._find_track(blob.x, blob.y)
            if track is None:
                track = _Track(float(blob.x), float(blob.y))
                self._tracks.append(track)
            track.add(float(blob.x), float(blob.y), blob.diameter / 2, blob.area, not blob.reject)

    def _find_track(self, x: float, y: float) -> _Track | None:
        for track in self._tracks:
            if math.hypot(x - track.x, y - track.y) <= self.tolerance:
                return track
        return None

    def done(self, now: float) -> bool:
        """수집 시간이 다 됐는지."""
        return self.active and now - self.started_at >= self.duration

    def finish(self, ball_radius_at=None) -> list[StaticObject] | None:
        """수집을 끝내고 고정 물체 목록을 돌려준다. 프레임이 너무 적으면 None (믿을 수 없음).

        ball_radius_at: (x, y) -> 그 위치의 공 반경(px) 또는 None. 시작점 공을 뺄 때 쓴다.
        """
        self.active = False
        if self.frames < MIN_SCAN_FRAMES:
            return None
        objects = []
        for track in self._tracks:
            if track.seen < self.frames * MIN_PRESENT_RATIO:
                continue  # 수집 중 계속 보이지 않음 (움직였거나 잠깐 생긴 것)
            x = track.sum_x / track.seen
            y = track.sum_y / track.seen
            radius = track.sum_radius / track.seen
            area = track.sum_area / track.seen
            ball_r = (ball_radius_at(x, y) if ball_radius_at is not None else None) or radius
            if any(
                math.hypot(x - px, y - py) <= (radius + ball_r) * EXCLUDE_POINT_RATIO
                for px, py in self._exclude_points
            ):
                continue  # 시작점에 놓인 공
            if not self._exclude_points and track.ball_like:
                # 공이 어디 있는지 모름(시작점 없음): 공 크기 덩어리는 공일 수 있어서 기억하지 않는다.
                # 수동 수집('고정 물체 다시 찾기')도 마찬가지다. 처음에는 "사용자가 공을 치웠다"고 보고
                # 공 크기 덩어리도 기억했는데, 현장에서 공을 매트에 둔 채 눌러서 공 자리가 고정 물체가
                # 되고, 그 뒤로 공을 영영 못 찾는 문제가 있었다.
                continue
            objects.append(StaticObject(x, y, radius + RADIUS_MARGIN_PX, area))
        self._tracks = []
        return objects


def number_holes(objects: list[StaticObject], expected_area_at, to_mm, min_area_ratio: float) -> None:
    """고정 물체 중 홀컵을 정하고 번호를 붙인다 (objects를 직접 고친다).

    expected_area_at(x, y): 그 위치의 공 예상 면적(px²) 또는 None (캘리브레이션 없음)
    to_mm(x, y): 픽셀 -> 매트 mm 좌표 또는 None
    홀컵 = 면적이 공 예상 면적의 min_area_ratio배 이상인 것. 번호는 매트 위 -> 아래,
    같은 높이(HOLE_ROW_TOLERANCE_MM 이내)면 왼쪽 -> 오른쪽 순서.
    """
    holes = []
    for obj in objects:
        obj.hole = None
        mm = to_mm(obj.x, obj.y)
        obj.x_mm, obj.y_mm = (mm if mm is not None else (None, None))
        expected = expected_area_at(obj.x, obj.y)
        if mm is None or not expected:
            continue
        if obj.area >= expected * min_area_ratio:
            holes.append(obj)
    # 위 -> 아래로 먼저 정렬한 뒤, y 차이가 작은 것끼리는 같은 줄로 보고 왼쪽 -> 오른쪽
    holes.sort(key=lambda obj: obj.y_mm)
    rows: list[list[StaticObject]] = []
    for obj in holes:
        if rows and abs(obj.y_mm - rows[-1][0].y_mm) <= HOLE_ROW_TOLERANCE_MM:
            rows[-1].append(obj)
        else:
            rows.append([obj])
    number = 1
    for row in rows:
        for obj in sorted(row, key=lambda o: o.x_mm):
            obj.hole = number
            number += 1
