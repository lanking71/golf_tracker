"""
볼 트래커 - 1단계: PySide6 UI 뼈대

이 파일은 아직 카메라나 추적 기능이 없는 '화면 껍데기'입니다.
- 왼쪽: 위(카메라 화면 자리) / 아래(궤적 결과 화면 자리)
- 오른쪽: 세로로 배치된 버튼 6개
- 위쪽: 현재 상태를 보여주는 배지 라벨

버튼을 누르면 아직 실제 동작은 하지 않고,
상단 상태 라벨의 문구만 바뀝니다. (실제 기능은 다음 단계에서 연결합니다)
"""

import sys

from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


# ------------------------------------------------------------------
# 골프 테마 QSS(스타일시트)
# 색상: 배경 #1B4332(짙은 그린), 패널 #2D6A4F, 포인트 #D4A017(골드)
# ------------------------------------------------------------------
QSS = """
/* 전체 창 배경 */
QMainWindow {
    background-color: #1B4332;
}

/* 카메라/궤적 결과를 보여줄 패널 라벨 */
QLabel#panel {
    background-color: #2D6A4F;
    color: white;
    border-radius: 8px;
    font-size: 16px;
    qproperty-alignment: AlignCenter;
}

/* 상단 상태 배지 라벨 */
QLabel#status {
    background-color: #D4A017;
    color: #1B4332;
    border-radius: 10px;
    padding: 8px 16px;
    font-size: 14px;
    font-weight: bold;
    qproperty-alignment: AlignCenter;
}

/* 기본 버튼 스타일 (초록 배경 + 흰 글씨) */
QPushButton {
    background-color: #2D6A4F;
    color: white;
    border: none;
    border-radius: 8px;
    padding: 14px 20px;
    font-size: 14px;
}
QPushButton:hover {
    background-color: #3B8564;
}
QPushButton:pressed {
    background-color: #24523D;
}

/* 캘리브레이션 버튼만 골드 강조 */
QPushButton#primary {
    background-color: #D4A017;
    color: #1B4332;
    font-weight: bold;
}
QPushButton#primary:hover {
    background-color: #E6B32A;
}
QPushButton#primary:pressed {
    background-color: #B8890F;
}
"""


class MainWindow(QMainWindow):
    """볼 트래커 메인 창 (1단계: 레이아웃 + 스타일만 구현)"""

    def __init__(self):
        super().__init__()

        self.setWindowTitle("볼 트래커")
        self.resize(1100, 650)

        # 중앙 위젯 + 전체를 세로로 나누는 레이아웃
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        root_layout = QVBoxLayout(central_widget)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        # 1) 상단 상태 배지 라벨
        self.status_label = QLabel("상태: 대기 중")
        self.status_label.setObjectName("status")
        root_layout.addWidget(self.status_label)

        # 2) 본문: 왼쪽(카메라/궤적) + 오른쪽(버튼들)을 가로로 배치
        body_layout = QHBoxLayout()
        body_layout.setSpacing(12)
        root_layout.addLayout(body_layout)

        body_layout.addLayout(self._build_left_panel(), 3)
        body_layout.addLayout(self._build_right_panel(), 1)

        # 전체 창에 QSS 스타일 적용
        self.setStyleSheet(QSS)

    def _build_left_panel(self) -> QVBoxLayout:
        """왼쪽 영역: 위(실시간 카메라) / 아래(궤적 결과) QLabel 두 개를 세로로 배치"""
        layout = QVBoxLayout()
        layout.setSpacing(12)

        self.camera_label = QLabel("실시간 카메라 화면\n(다음 단계에서 연결)")
        self.camera_label.setObjectName("panel")
        self.camera_label.setMinimumSize(400, 250)
        layout.addWidget(self.camera_label, 1)

        self.trajectory_label = QLabel("궤적 결과 화면\n(다음 단계에서 연결)")
        self.trajectory_label.setObjectName("panel")
        self.trajectory_label.setMinimumSize(400, 250)
        layout.addWidget(self.trajectory_label, 1)

        return layout

    def _build_right_panel(self) -> QVBoxLayout:
        """오른쪽 영역: 버튼 6개를 세로로 배치"""
        layout = QVBoxLayout()
        layout.setSpacing(10)

        # (버튼에 표시할 이름, 골드 강조 여부) 순서대로 정의
        button_specs = [
            ("캘리브레이션", True),
            ("시작 위치 등록", False),
            ("추적 준비", False),
            ("추적 중지", False),
            ("궤적 초기화", False),
            ("저장", False),
        ]

        for name, is_primary in button_specs:
            button = QPushButton(name)
            if is_primary:
                button.setObjectName("primary")
            # 클릭 시 상태 라벨 갱신 (실제 기능은 TODO: 다음 단계에서 구현)
            button.clicked.connect(
                lambda _checked=False, n=name: self._on_button_clicked(n)
            )
            layout.addWidget(button)

        # 버튼들을 위쪽으로 붙이고 아래는 빈 공간으로 남김
        layout.addStretch(1)
        return layout

    def _on_button_clicked(self, button_name: str) -> None:
        """버튼 클릭 시 호출되는 공통 슬롯.

        지금은 상태 라벨 문구만 갱신합니다.
        # TODO: 다음 단계에서 각 버튼의 실제 기능(카메라 열기, 추적 시작/중지 등)을 연결합니다.
        """
        self.status_label.setText(f"상태: {button_name} 클릭됨")


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
