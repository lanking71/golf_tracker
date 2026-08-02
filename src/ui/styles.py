"""
골프 테마 QSS(스타일시트) 모음.

색상: 배경 #1B4332(짙은 그린), 패널 #2D6A4F, 포인트 #D4A017(골드)
"""

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

/* 상단 메뉴 바 ('설정 > 검출 설정...') */
QMenuBar {
    background-color: #1B4332;
    color: white;
}
QMenuBar::item {
    background-color: transparent;
    padding: 6px 12px;
}
QMenuBar::item:selected {
    background-color: #2D6A4F;
    border-radius: 4px;
}
QMenu {
    background-color: #2D6A4F;
    color: white;
    border: 1px solid #1B4332;
}
QMenu::item {
    padding: 6px 20px;
}
QMenu::item:selected {
    background-color: #D4A017;
    color: #1B4332;
}

/* HSV 튜닝 다이얼로그 */
QDialog {
    background-color: #1B4332;
}
QDialog QLabel {
    color: white;
}
QComboBox {
    background-color: #2D6A4F;
    color: white;
    border: none;
    border-radius: 6px;
    padding: 6px 10px;
}
QSlider::groove:horizontal {
    height: 6px;
    background: #2D6A4F;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #D4A017;
    width: 16px;
    margin: -6px 0;
    border-radius: 8px;
}
"""
