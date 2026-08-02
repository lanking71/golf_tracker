"""
HSV 검출 설정(튜닝) 다이얼로그.

H/S/V 각각 최소·최대 슬라이더 6개로 검출 범위를 실시간 조정한다.
슬라이더를 움직이는 동안 values_changed 시그널을 보내서,
메인 창이 카메라 패널에 마스크 미리보기(검출 영역이 흰색으로 보이는
화면)를 그릴 수 있게 한다.

'저장'을 누르면 saved 시그널로 (프로필 키, 프로필 dict)를 보낸다.
실제로 config/settings.json에 쓰는 것은 메인 창이 담당한다.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

from src.detector import (
    DEFAULT_MAX_AREA,
    DEFAULT_MIN_AREA,
    DEFAULT_MIN_CIRCULARITY,
)

# (내부 키, 라벨, 최솟값, 최댓값) - Hue는 0~179, Saturation/Value는 0~255 (OpenCV 기준)
SLIDER_SPECS = [
    ("h_min", "Hue 최소", 0, 179),
    ("h_max", "Hue 최대", 0, 179),
    ("s_min", "Saturation 최소", 0, 255),
    ("s_max", "Saturation 최대", 0, 255),
    ("v_min", "Value 최소", 0, 255),
    ("v_max", "Value 최대", 0, 255),
]


class TuningDialog(QDialog):
    """HSV 슬라이더 6개 + 프로필 저장 기능을 가진 창."""

    # (lower=[h,s,v], upper=[h,s,v]) - 슬라이더가 움직일 때마다 보낸다
    values_changed = Signal(list, list)
    # (profile_key, profile_dict) - '저장' 버튼을 누르면 보낸다
    saved = Signal(str, dict)

    def __init__(self, profiles: dict, active_profile: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("검출 설정 (HSV 튜닝)")
        self.resize(420, 380)
        # 창을 닫아도(X 버튼) 인스턴스가 사라지지 않고 숨겨지기만 하도록
        # 기본 동작을 쓴다 (메인 창이 이 인스턴스를 재사용한다).

        self._profiles = dict(profiles)

        layout = QVBoxLayout(self)
        layout.addLayout(self._build_profile_row())

        self._sliders: dict[str, QSlider] = {}
        for key, label_text, minimum, maximum in SLIDER_SPECS:
            layout.addLayout(self._build_slider_row(key, label_text, minimum, maximum))

        layout.addStretch(1)
        layout.addLayout(self._build_button_row())

        self._load_profile_into_sliders(active_profile)
        self._select_profile_in_combo(active_profile)

    def _build_profile_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel("프로필:"))

        self.profile_combo = QComboBox()
        self.profile_combo.setEditable(True)
        # 자동으로 새 항목을 만들지 않게 해서, '저장' 시점에만 새 프로필 여부를 판단한다.
        self.profile_combo.setInsertPolicy(QComboBox.NoInsert)
        self._refresh_profile_combo()
        self.profile_combo.currentIndexChanged.connect(self._on_profile_selected)
        row.addWidget(self.profile_combo, 1)
        return row

    def _build_slider_row(self, key: str, label_text: str, minimum: int, maximum: int) -> QHBoxLayout:
        row = QHBoxLayout()

        label = QLabel(label_text)
        label.setFixedWidth(120)
        row.addWidget(label)

        slider = QSlider(Qt.Horizontal)
        slider.setRange(minimum, maximum)
        row.addWidget(slider, 1)

        value_label = QLabel("0")
        value_label.setFixedWidth(32)
        value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(value_label)

        slider.valueChanged.connect(lambda v, vl=value_label: vl.setText(str(v)))
        slider.valueChanged.connect(self._on_slider_changed)
        self._sliders[key] = slider
        return row

    def _build_button_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch(1)

        save_button = QPushButton("저장")
        save_button.setObjectName("primary")
        save_button.clicked.connect(self._on_save_clicked)
        row.addWidget(save_button)

        close_button = QPushButton("닫기")
        close_button.clicked.connect(self.close)
        row.addWidget(close_button)
        return row

    def refresh_profiles(self, profiles: dict, active_profile: str) -> None:
        """메인 창에서 다이얼로그를 다시 열 때, 최신 프로필 목록으로 갱신한다."""
        self._profiles = dict(profiles)
        self._refresh_profile_combo()
        self._load_profile_into_sliders(active_profile)
        self._select_profile_in_combo(active_profile)

    def _refresh_profile_combo(self) -> None:
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for key, profile in self._profiles.items():
            self.profile_combo.addItem(profile.get("name", key), userData=key)
        self.profile_combo.blockSignals(False)

    def _select_profile_in_combo(self, key: str) -> None:
        index = self.profile_combo.findData(key)
        if index >= 0:
            self.profile_combo.blockSignals(True)
            self.profile_combo.setCurrentIndex(index)
            self.profile_combo.blockSignals(False)

    def _on_profile_selected(self, index: int) -> None:
        key = self.profile_combo.itemData(index)
        if key is not None:
            self._load_profile_into_sliders(key)

    def _load_profile_into_sliders(self, key: str) -> None:
        profile = self._profiles.get(key)
        if profile is None:
            return
        lower = profile.get("hsv_lower", [0, 0, 0])
        upper = profile.get("hsv_upper", [179, 255, 255])
        self._sliders["h_min"].setValue(lower[0])
        self._sliders["s_min"].setValue(lower[1])
        self._sliders["v_min"].setValue(lower[2])
        self._sliders["h_max"].setValue(upper[0])
        self._sliders["s_max"].setValue(upper[1])
        self._sliders["v_max"].setValue(upper[2])

    def _current_lower_upper(self) -> tuple[list, list]:
        lower = [
            self._sliders["h_min"].value(),
            self._sliders["s_min"].value(),
            self._sliders["v_min"].value(),
        ]
        upper = [
            self._sliders["h_max"].value(),
            self._sliders["s_max"].value(),
            self._sliders["v_max"].value(),
        ]
        return lower, upper

    def _on_slider_changed(self, _value: int) -> None:
        lower, upper = self._current_lower_upper()
        self.values_changed.emit(lower, upper)

    def _resolve_profile_key(self, name: str) -> str:
        """콤보에 입력된 이름과 같은 프로필이 이미 있으면 그 key를 재사용(덮어쓰기)한다.

        없으면 새 프로필로 보고, 입력한 이름을 그대로 key로 쓴다.
        """
        for key, profile in self._profiles.items():
            if profile.get("name", key) == name:
                return key
        return name

    def _on_save_clicked(self) -> None:
        name = self.profile_combo.currentText().strip()
        if not name:
            QMessageBox.warning(self, "프로필 저장", "프로필 이름을 입력해주세요.")
            return

        key = self._resolve_profile_key(name)
        existing = self._profiles.get(key, {})
        lower, upper = self._current_lower_upper()

        profile = {
            "name": name,
            "hsv_lower": lower,
            "hsv_upper": upper,
            "min_area": existing.get("min_area", DEFAULT_MIN_AREA),
            "max_area": existing.get("max_area", DEFAULT_MAX_AREA),
            "min_circularity": existing.get("min_circularity", DEFAULT_MIN_CIRCULARITY),
        }

        self._profiles[key] = profile
        self._refresh_profile_combo()
        self._select_profile_in_combo(key)
        self.saved.emit(key, profile)
