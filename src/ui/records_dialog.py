"""
저장된 측정 기록 조회 다이얼로그.

'기록 > 기록 보기...' 메뉴에서 연다. src.storage.Storage에 저장된
결과를 날짜 내림차순으로 목록에 보여주고, 하나를 클릭하면 그때의
궤적(매트 실제 비율로 그림)과 요약 통계를 다시 보여준다. 선택한
기록은 삭제할 수도 있다.
"""

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from src.storage import SavedResult, Storage

RECORD_KEY_ROLE = Qt.UserRole

# 궤적 미리보기 캔버스 렌더링 기준 폭 (라벨 크기에 맞춰 다시 스케일되므로
# 절대값은 중요하지 않고 매트 비율만 정확하면 된다)
CANVAS_WIDTH = 700

BG_COLOR_BGR = (79, 106, 45)  # 패널 배경 (#2D6A4F)
START_COLOR_BGR = (23, 160, 212)  # 골드 (#D4A017)
PATH_COLOR_BGR = (178, 213, 149)  # 라임 (#95D5B2)
CURRENT_COLOR_BGR = (255, 255, 255)
STOP_RADIUS = 14
STOP_THICKNESS = 3
START_RADIUS = 10
CURRENT_RADIUS = 6

PLACEHOLDER_TEXT = "기록을 선택하면\n궤적이 표시됩니다"


class RecordsDialog(QDialog):
    """저장된 측정 기록 목록 + 선택한 기록의 궤적/통계 미리보기."""

    def __init__(self, storage: Storage, parent=None):
        super().__init__(parent)
        self.setWindowTitle("측정 기록")
        self.resize(780, 520)
        self.storage = storage

        layout = QHBoxLayout(self)
        layout.addLayout(self._build_list_section(), 1)
        layout.addLayout(self._build_preview_section(), 2)

        self.refresh()

    def _build_list_section(self) -> QVBoxLayout:
        layout = QVBoxLayout()

        title = QLabel("<b>저장된 기록</b>")
        layout.addWidget(title)

        self.record_list = QListWidget()
        self.record_list.itemClicked.connect(self._on_record_clicked)
        layout.addWidget(self.record_list)

        delete_button = QPushButton("선택 기록 삭제")
        delete_button.clicked.connect(self._on_delete_clicked)
        layout.addWidget(delete_button)

        close_button = QPushButton("닫기")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)

        return layout

    def _build_preview_section(self) -> QVBoxLayout:
        layout = QVBoxLayout()

        self.trajectory_label = QLabel(PLACEHOLDER_TEXT)
        self.trajectory_label.setObjectName("panel")
        self.trajectory_label.setMinimumSize(360, 220)
        self.trajectory_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.trajectory_label, 1)

        self.stats_label = QLabel("")
        self.stats_label.setObjectName("stats_card")
        self.stats_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.stats_label.setWordWrap(True)
        layout.addWidget(self.stats_label)

        return layout

    def refresh(self) -> None:
        """저장소에서 목록을 다시 불러온다. 메인 창이 다이얼로그를 다시 열 때 호출한다."""
        self.record_list.clear()
        self.trajectory_label.setText(PLACEHOLDER_TEXT)
        self.trajectory_label.setPixmap(QPixmap())
        self.stats_label.setText("")

        for result in self.storage.list_results():
            label = (
                f"{result.recorded_at}\n"
                f"{result.profile_name}  ·  {result.total_distance_cm:.1f}cm"
            )
            item = QListWidgetItem(label)
            item.setData(RECORD_KEY_ROLE, result.id)
            self.record_list.addItem(item)

    def _on_record_clicked(self, item: QListWidgetItem) -> None:
        result_id = item.data(RECORD_KEY_ROLE)
        result = self.storage.get_result(result_id)
        if result is not None:
            self._render_result(result)

    def _on_delete_clicked(self) -> None:
        item = self.record_list.currentItem()
        if item is None:
            QMessageBox.information(self, "삭제", "삭제할 기록을 목록에서 선택해주세요.")
            return
        reply = QMessageBox.question(self, "삭제", "이 기록을 삭제할까요?")
        if reply != QMessageBox.Yes:
            return
        self.storage.delete_result(item.data(RECORD_KEY_ROLE))
        self.refresh()

    def _render_result(self, result: SavedResult) -> None:
        """선택한 기록의 궤적을 매트 실제 비율로 그리고, 통계를 함께 보여준다."""
        width = CANVAS_WIDTH
        height = max(1, round(width * result.mat_height_mm / result.mat_width_mm))
        canvas = np.full((height, width, 3), BG_COLOR_BGR, dtype=np.uint8)

        def to_canvas(point: tuple) -> tuple:
            x, y = point
            canvas_x = max(0, min(width - 1, int(x / result.mat_width_mm * width)))
            canvas_y = max(0, min(height - 1, int(y / result.mat_height_mm * height)))
            return (canvas_x, canvas_y)

        if result.start_point_mm is not None:
            cv2.circle(canvas, to_canvas(result.start_point_mm), START_RADIUS, START_COLOR_BGR, -1)

        points = [to_canvas(p) for p in result.trajectory_mm]
        if len(points) >= 2:
            cv2.polylines(
                canvas, [np.array(points, dtype=np.int32)], isClosed=False,
                color=PATH_COLOR_BGR, thickness=2,
            )
        if points:
            cv2.circle(canvas, points[-1], CURRENT_RADIUS, CURRENT_COLOR_BGR, -1)

        if result.stop_point_mm is not None:
            cv2.circle(canvas, to_canvas(result.stop_point_mm), STOP_RADIUS, START_COLOR_BGR, STOP_THICKNESS)

        rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
        h, w, channels = rgb.shape
        image = QImage(rgb.data, w, h, channels * w, QImage.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(image).scaled(
            self.trajectory_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.trajectory_label.setPixmap(pixmap)

        self.stats_label.setText(
            f"<b>{result.recorded_at}</b><br>"
            f"프로필: {result.profile_name}<br>"
            f"총 거리: {result.total_distance_cm:.1f}cm<br>"
            f"소요 시간: {result.duration_s:.2f}초<br>"
            f"평균 속도: {result.average_speed_cm:.1f}cm/초<br>"
            f"최고 속도: {result.max_speed_cm:.1f}cm/초<br>"
            f"직진성: {result.straightness:.2f}"
        )
