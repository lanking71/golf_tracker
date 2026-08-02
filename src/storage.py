"""
측정 결과 저장/조회 모듈 (SQLite).

'저장' 버튼을 누르면 이번 추적 결과(시작점/정지점 mm 좌표, 요약 통계
cm·cm/초, 전체 궤적, 사용한 검출 프로필 이름)를 SQLite DB에 기록한다.
캘리브레이션이 안 돼 있으면(실제 단위를 모르면) 저장하지 않는다 -
이 판단은 MainWindow가 저장을 호출하기 전에 한다.

DB 파일 경로는 config/settings.json의 storage.db_path에서 읽는다
(기본값 data/results.db, 프로젝트 루트 기준 상대 경로).
"""

import contextlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.config import load_settings

DEFAULT_DB_PATH = "data/results.db"

# 프로젝트 루트 (config/settings.json과 같은 기준)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class SavedResult:
    """DB에서 읽어온 측정 결과 한 건."""

    id: int
    recorded_at: str  # "YYYY-MM-DDTHH:MM:SS" 형식 문자열
    profile_name: str
    start_point_mm: tuple | None
    stop_point_mm: tuple | None
    total_distance_cm: float
    duration_s: float
    average_speed_cm: float
    max_speed_cm: float
    straightness: float
    mat_width_mm: float
    mat_height_mm: float
    trajectory_mm: list = field(default_factory=list)  # [(x_mm, y_mm), ...]


class Storage:
    """SQLite에 측정 결과를 저장·조회·삭제하는 클래스."""

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_path = load_settings().get("storage", {}).get("db_path", DEFAULT_DB_PATH)
        path = Path(db_path)
        self.db_path = path if path.is_absolute() else PROJECT_ROOT / path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextlib.contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at TEXT NOT NULL,
                    profile_name TEXT,
                    start_x_mm REAL,
                    start_y_mm REAL,
                    stop_x_mm REAL,
                    stop_y_mm REAL,
                    total_distance_cm REAL,
                    duration_s REAL,
                    average_speed_cm REAL,
                    max_speed_cm REAL,
                    straightness REAL,
                    mat_width_mm REAL,
                    mat_height_mm REAL,
                    trajectory_json TEXT NOT NULL
                )
                """
            )

    def save_result(
        self,
        profile_name: str,
        start_point_mm: tuple | None,
        stop_point_mm: tuple | None,
        total_distance_cm: float,
        duration_s: float,
        average_speed_cm: float,
        max_speed_cm: float,
        straightness: float,
        mat_width_mm: float,
        mat_height_mm: float,
        trajectory_mm: list,
    ) -> int:
        """결과 한 건을 저장하고, 새로 생긴 행의 id를 반환한다."""
        recorded_at = datetime.now().isoformat(timespec="seconds")
        with self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO results (
                    recorded_at, profile_name,
                    start_x_mm, start_y_mm, stop_x_mm, stop_y_mm,
                    total_distance_cm, duration_s, average_speed_cm, max_speed_cm,
                    straightness, mat_width_mm, mat_height_mm, trajectory_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recorded_at,
                    profile_name,
                    start_point_mm[0] if start_point_mm else None,
                    start_point_mm[1] if start_point_mm else None,
                    stop_point_mm[0] if stop_point_mm else None,
                    stop_point_mm[1] if stop_point_mm else None,
                    total_distance_cm,
                    duration_s,
                    average_speed_cm,
                    max_speed_cm,
                    straightness,
                    mat_width_mm,
                    mat_height_mm,
                    json.dumps(trajectory_mm),
                ),
            )
            return cursor.lastrowid

    def list_results(self) -> list:
        """저장된 모든 결과를 최신순(날짜 내림차순)으로 돌려준다."""
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM results ORDER BY recorded_at DESC").fetchall()
        return [self._row_to_result(row) for row in rows]

    def get_result(self, result_id: int) -> SavedResult | None:
        """id 하나로 결과를 조회한다. 없으면 None."""
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM results WHERE id = ?", (result_id,)).fetchone()
        return self._row_to_result(row) if row is not None else None

    def delete_result(self, result_id: int) -> None:
        """id로 결과 한 건을 삭제한다. 없는 id면 조용히 아무 일도 안 한다."""
        with self._connection() as conn:
            conn.execute("DELETE FROM results WHERE id = ?", (result_id,))

    @staticmethod
    def _row_to_result(row: sqlite3.Row) -> SavedResult:
        start = None
        if row["start_x_mm"] is not None and row["start_y_mm"] is not None:
            start = (row["start_x_mm"], row["start_y_mm"])
        stop = None
        if row["stop_x_mm"] is not None and row["stop_y_mm"] is not None:
            stop = (row["stop_x_mm"], row["stop_y_mm"])
        trajectory = [tuple(point) for point in json.loads(row["trajectory_json"])]
        return SavedResult(
            id=row["id"],
            recorded_at=row["recorded_at"],
            profile_name=row["profile_name"],
            start_point_mm=start,
            stop_point_mm=stop,
            total_distance_cm=row["total_distance_cm"],
            duration_s=row["duration_s"],
            average_speed_cm=row["average_speed_cm"],
            max_speed_cm=row["max_speed_cm"],
            straightness=row["straightness"],
            mat_width_mm=row["mat_width_mm"],
            mat_height_mm=row["mat_height_mm"],
            trajectory_mm=trajectory,
        )
