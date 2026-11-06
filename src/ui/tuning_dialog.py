"""
HSV 검출 설정(튜닝) 다이얼로그.

H/S/V 각각 최소·최대 슬라이더 6개로 검출 범위를 실시간 조정한다.
슬라이더를 움직이는 동안 values_changed 시그널을 보내서,
메인 창이 카메라 패널에 마스크 미리보기(검출 영역이 흰색으로 보이는
화면)를 그릴 수 있게 한다.

'저장'을 누르면 saved 시그널로 (프로필 키, 프로필 dict)를 보낸다.
실제로 config/settings.json에 쓰는 것은 메인 창이 담당한다.

24단계: 슬라이더를 손으로 맞추기 어려운 경우(특히 채도가 낮은 흰 공)를 위해
"화면에서 공 클릭해 색 잡기"를 추가했다. 버튼을 누르면 메인 카메라 화면이 '색 추출 모드'가
되고, 공을 클릭하면 그 주변 공 픽셀로 HSV 범위를 자동으로 만들어 슬라이더에 채운다
(계산은 src/detector.py의 auto_color_range). 공을 다른 위치에 옮겨 "추가 샘플"을 누르면
샘플을 합쳐서 범위를 다시 만든다. 저장은 사용자가 확인하고 '저장'(현재 프로필 덮어쓰기) 또는
'새 프로필로 저장'을 눌러야 된다. 공 면적으로 min_area/max_area도 제안한다.

Hue 최소가 Hue 최대보다 크면 빨강 경계를 넘는 범위다 (예: 170 ~ 8 = 170~179 + 0~8, 분홍 공).
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
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
    # 화면에서 공을 클릭해 색을 잡겠다는 요청. True면 '추가 샘플'(이전 샘플과 합침)
    pick_requested = Signal(bool)

    def __init__(self, profiles: dict, active_profile: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("검출 설정 (HSV 튜닝)")
        self.resize(460, 620)
        # 클릭으로 추출한 결과가 제안한 min_area / max_area (없으면 None)
        self._suggested_area: tuple[int, int] | None = None
        # 창을 닫아도(X 버튼) 인스턴스가 사라지지 않고 숨겨지기만 하도록
        # 기본 동작을 쓴다 (메인 창이 이 인스턴스를 재사용한다).

        self._profiles = dict(profiles)

        layout = QVBoxLayout(self)
        layout.addLayout(self._build_profile_row())

        self._sliders: dict[str, QSlider] = {}
        self._value_labels: dict[str, QLabel] = {}
        for key, label_text, minimum, maximum in SLIDER_SPECS:
            layout.addLayout(self._build_slider_row(key, label_text, minimum, maximum))

        hue_hint = QLabel("Hue 최소가 최대보다 크면 빨강 경계를 넘는 범위입니다 (예: 170 ~ 8, 분홍 공).")
        hue_hint.setWordWrap(True)
        layout.addWidget(hue_hint)

        layout.addLayout(self._build_pick_section())
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
        self._value_labels[key] = value_label
        return row

    def _build_pick_section(self) -> QVBoxLayout:
        """24단계: 화면에서 공을 클릭해 색 잡기."""
        section = QVBoxLayout()
        section.setSpacing(8)

        title = QLabel("공 색 자동으로 잡기")
        title.setObjectName("section")
        section.addWidget(title)

        row = QHBoxLayout()
        self.pick_button = QPushButton("화면에서 공 클릭해 색 잡기")
        self.pick_button.clicked.connect(lambda: self.pick_requested.emit(False))
        row.addWidget(self.pick_button)
        self.add_sample_button = QPushButton("추가 샘플")
        self.add_sample_button.setEnabled(False)  # 첫 샘플을 잡은 뒤에만
        self.add_sample_button.clicked.connect(lambda: self.pick_requested.emit(True))
        row.addWidget(self.add_sample_button)
        section.addLayout(row)

        self.pick_result_label = QLabel(
            "버튼을 누른 뒤 메인 화면에서 공의 가운데를 클릭하세요. "
            "공을 다른 위치에 옮겨 '추가 샘플'을 하면 조명 차이까지 합쳐집니다."
        )
        self.pick_result_label.setWordWrap(True)
        section.addWidget(self.pick_result_label)

        self.area_checkbox = QCheckBox("면적 제안값(min_area / max_area)도 함께 저장")
        self.area_checkbox.setChecked(True)
        self.area_checkbox.setEnabled(False)
        section.addWidget(self.area_checkbox)
        return section

    def set_picking(self, picking: bool) -> None:
        """메인 화면이 색 추출 모드인 동안 버튼 문구로 알려 준다."""
        self.pick_button.setEnabled(not picking)
        self.add_sample_button.setEnabled(not picking and self._suggested_area is not None)
        if picking:
            self.pick_result_label.setText("메인 화면에서 공의 가운데를 클릭하세요 (오른쪽 클릭: 취소)")

    def apply_color_result(self, result, sample_count: int) -> None:
        """클릭으로 추출한 결과(src.detector.ColorRangeResult)를 슬라이더와 설명에 채운다 (아직 저장 안 함)."""
        if result is None:
            self.pick_result_label.setText("공을 찾지 못했습니다. 공의 가운데를 다시 클릭해 주세요.")
            self.set_picking(False)
            return
        lower, upper = result.hsv_lower, result.hsv_upper
        # 슬라이더 6개를 한꺼번에 바꾸고, 미리보기 갱신은 마지막에 한 번만 보낸다
        for key, value in (
            ("h_min", lower[0]), ("s_min", lower[1]), ("v_min", lower[2]),
            ("h_max", upper[0]), ("s_max", upper[1]), ("v_max", upper[2]),
        ):
            self._sliders[key].blockSignals(True)
            self._sliders[key].setValue(int(value))
            self._sliders[key].blockSignals(False)
            # blockSignals 동안 숫자 라벨이 안 바뀌므로 직접 맞춘다
            self._value_labels[key].setText(str(int(value)))
        self.values_changed.emit(list(lower), list(upper))

        self._suggested_area = (result.suggested_min_area, result.suggested_max_area)
        self.area_checkbox.setEnabled(True)
        found_text = "찾음" if result.ball_found else "못 찾음 (범위를 확인하세요)"
        self.pick_result_label.setText(
            f"샘플 {sample_count}개 · H {lower[0]}~{upper[0]}, S {lower[1]}~{upper[1]}, V {lower[2]}~{upper[2]}"
            f" (여유 x{result.margin_scale:g})\n"
            f"공 {found_text}: 면적 {result.ball_area:.0f}px², 지름 {result.ball_diameter:.1f}px, "
            f"원형도 {result.ball_circularity:.2f} · 매트 위 다른 덩어리 {result.other_blobs}개\n"
            f"면적 제안: min_area {result.suggested_min_area} / max_area {result.suggested_max_area}\n"
            "카메라 화면의 마스크(흰색 = 공으로 보는 곳)를 확인한 뒤 저장하세요."
        )
        self.set_picking(False)

    def _build_button_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch(1)

        save_button = QPushButton("저장")
        save_button.setObjectName("primary")
        save_button.clicked.connect(self._on_save_clicked)
        row.addWidget(save_button)

        # 24단계: 지금 프로필을 덮어쓰지 않고 새 이름으로 저장
        save_new_button = QPushButton("새 프로필로 저장")
        save_new_button.clicked.connect(self._on_save_new_clicked)
        row.addWidget(save_new_button)

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
        """'저장': 콤보에 보이는 이름의 프로필에 저장한다 (같은 이름이 있으면 덮어쓰기)."""
        name = self.profile_combo.currentText().strip()
        if not name:
            QMessageBox.warning(self, "프로필 저장", "프로필 이름을 입력해주세요.")
            return
        self._save_as(name)

    def _on_save_new_clicked(self) -> None:
        """'새 프로필로 저장': 새 이름을 물어보고 그 이름으로 저장한다 (지금 프로필은 그대로)."""
        name, ok = QInputDialog.getText(self, "새 프로필로 저장", "새 프로필 이름:")
        name = name.strip()
        if not ok or not name:
            return
        if any(profile.get("name", key) == name for key, profile in self._profiles.items()):
            QMessageBox.warning(self, "새 프로필로 저장", "이미 있는 이름입니다. 다른 이름을 입력해 주세요.")
            return
        self._save_as(name)

    def _save_as(self, name: str) -> None:
        key = self._resolve_profile_key(name)
        existing = self._profiles.get(key, {})
        lower, upper = self._current_lower_upper()

        min_area = existing.get("min_area", DEFAULT_MIN_AREA)
        max_area = existing.get("max_area", DEFAULT_MAX_AREA)
        if self._suggested_area is not None and self.area_checkbox.isChecked():
            # 클릭으로 잰 공 면적에서 나온 제안값을 쓴다 (사용자가 체크한 경우만)
            min_area, max_area = self._suggested_area

        profile = {
            "name": name,
            "hsv_lower": lower,
            "hsv_upper": upper,
            "min_area": min_area,
            "max_area": max_area,
            "min_circularity": existing.get("min_circularity", DEFAULT_MIN_CIRCULARITY),
        }

        self._profiles[key] = profile
        self._refresh_profile_combo()
        self._select_profile_in_combo(key)
        self.saved.emit(key, profile)
