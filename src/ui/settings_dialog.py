"""
설정 다이얼로그: 검출 프로필 관리 + 매트 크기/필터 설정.

HSV 슬라이더로 실시간 튜닝하는 TuningDialog와 달리, 이 다이얼로그는
- 검출 프로필 목록 관리(선택·추가·삭제·이름 변경)
- 매트 실제 크기(mat_width_mm/mat_height_mm)
- 매트 밖 필터(filter_outside_mat) on/off
- 앱 통신(TCP 서버) 사용 여부와 포트
- 렌즈 왜곡 보정(k1) 슬라이더 + 격자선·검출 디버그 표시 (22단계)
같은 '폼' 형태의 설정을 다룬다. 실시간 카메라 미리보기는 필요 없다.

프로필 목록 조작(선택·추가·삭제·이름 변경)은 클릭하는 즉시
config/settings.json에 저장된다. 매트 설정은 '매트 설정 저장' 버튼을
눌러야 저장된다. 렌즈 왜곡 보정은 슬라이더를 움직이면 메인 화면 카메라 영상에
바로 반영되고(미리보기), '저장'을 눌러야 settings.json에 저장된다. '취소'를 누르거나
저장하지 않고 창을 닫으면 이전 값으로 되돌린다. 뭔가 저장될 때마다 settings_changed 시그널을 보내서,
메인 창이 검출기(BallDetector)·캘리브레이션(Calibration)을 새로
불러오게 한다.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
)

from src.calibration import (
    DEFAULT_FILTER_OUTSIDE_MAT,
    DEFAULT_MAT_HEIGHT_MM,
    DEFAULT_MAT_WIDTH_MM,
    Calibration,
)
from src.config import load_settings, save_settings
from src.detector import (
    DEFAULT_DETECTION_SETTINGS,
    DEFAULT_MAX_AREA,
    DEFAULT_MIN_AREA,
    DEFAULT_MIN_CIRCULARITY,
)
from src.lens import DEFAULT_UNDISTORT_K1
from src.protocol_server import DEFAULT_ENABLED, DEFAULT_PORT

PROFILE_KEY_ROLE = Qt.UserRole

# 렌즈 왜곡 보정 슬라이더: 정수 슬라이더 값 / 100 = k1. -60~+50 -> k1 -0.60~+0.50 (0.01 단위)
# 이 카메라(광각)는 k1 약 -0.5에서 가장 곧게 펴졌다. k1 모델은 약 -0.59보다 작아지면 화면 좌우 끝이
# 접혀서(모델이 성립 안 함) 의미가 없으므로 -0.60까지만 둔다.
K1_SLIDER_STEPS_PER_UNIT = 100
K1_SLIDER_MIN = -60
K1_SLIDER_MAX = 50


class SettingsDialog(QDialog):
    """검출 프로필 관리 + 매트 설정 화면."""

    # 프로필/매트 설정이 저장돼서 메인 창이 detector·calibration을 다시 불러와야 할 때
    settings_changed = Signal()
    # 앱 통신(TCP 서버) 설정이 저장됐을 때. 서버를 다시 시작해야 하므로 별도 시그널로 뺐다
    # (프로필을 클릭할 때마다 서버가 재시작되면 앱 접속이 끊기기 때문).
    protocol_settings_changed = Signal()
    # 렌즈 왜곡 보정 슬라이더를 움직였을 때 (미리보기용, 아직 저장 안 됨). 값 = k1
    lens_k1_preview = Signal(float)
    # 카메라 화면에 가로·세로 격자선을 그릴지 (직선이 곧은지 보기 쉽게)
    grid_toggled = Signal(bool)
    # 검출된 덩어리의 면적·지름을 카메라 화면에 글자로 보여줄지 (min_area/max_area 맞출 때)
    detection_debug_toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("설정")
        self.resize(460, 860)
        # settings.json에 저장돼 있는 k1 (취소할 때 이 값으로 되돌린다)
        self._saved_k1: float = DEFAULT_UNDISTORT_K1

        self._profiles: dict = {}
        self._active_profile_key: str = DEFAULT_DETECTION_SETTINGS["active_profile"]

        layout = QVBoxLayout(self)
        layout.addLayout(self._build_profile_section())
        layout.addLayout(self._build_mat_section())
        layout.addLayout(self._build_protocol_section())
        layout.addLayout(self._build_lens_section())
        layout.addStretch(1)

        close_button = QPushButton("닫기")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)

        self.reload_from_settings()

    # ------------------------------------------------------------------
    # 화면 구성
    # ------------------------------------------------------------------
    def _build_profile_section(self) -> QVBoxLayout:
        layout = QVBoxLayout()

        title = QLabel("<b>검출 프로필</b>")
        layout.addWidget(title)

        self.active_profile_label = QLabel("현재 활성 프로필: -")
        layout.addWidget(self.active_profile_label)

        self.profile_list = QListWidget()
        self.profile_list.itemClicked.connect(self._on_profile_clicked)
        layout.addWidget(self.profile_list)

        button_row = QHBoxLayout()
        add_button = QPushButton("추가")
        add_button.clicked.connect(self._on_add_profile)
        rename_button = QPushButton("이름 변경")
        rename_button.clicked.connect(self._on_rename_profile)
        delete_button = QPushButton("삭제")
        delete_button.clicked.connect(self._on_delete_profile)
        for button in (add_button, rename_button, delete_button):
            button_row.addWidget(button)
        layout.addLayout(button_row)

        hint = QLabel("목록에서 클릭하면 바로 활성 프로필로 전환됩니다.")
        layout.addWidget(hint)

        return layout

    def _build_mat_section(self) -> QVBoxLayout:
        layout = QVBoxLayout()

        title = QLabel("<b>매트 설정</b>")
        layout.addWidget(title)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("가로(mm):"))
        self.mat_width_input = QSpinBox()
        self.mat_width_input.setRange(1, 100000)
        size_row.addWidget(self.mat_width_input)
        size_row.addWidget(QLabel("세로(mm):"))
        self.mat_height_input = QSpinBox()
        self.mat_height_input.setRange(1, 100000)
        size_row.addWidget(self.mat_height_input)
        layout.addLayout(size_row)

        self.filter_checkbox = QCheckBox("매트 영역 밖에서 검출된 공은 무시")
        layout.addWidget(self.filter_checkbox)

        save_button = QPushButton("매트 설정 저장")
        save_button.setObjectName("primary")
        save_button.clicked.connect(self._on_save_mat_settings)
        layout.addWidget(save_button)

        return layout

    def _build_protocol_section(self) -> QVBoxLayout:
        layout = QVBoxLayout()

        title = QLabel("<b>앱 통신 설정</b>")
        layout.addWidget(title)

        self.protocol_enabled_checkbox = QCheckBox("앱(Flutter)과 통신 사용")
        layout.addWidget(self.protocol_enabled_checkbox)

        port_row = QHBoxLayout()
        port_row.addWidget(QLabel("포트:"))
        self.protocol_port_input = QSpinBox()
        # 1024 미만은 시스템 예약 포트라 쓰지 않는다
        self.protocol_port_input.setRange(1024, 65535)
        port_row.addWidget(self.protocol_port_input)
        port_row.addStretch(1)
        layout.addLayout(port_row)

        save_button = QPushButton("앱 통신 설정 저장")
        save_button.setObjectName("primary")
        save_button.clicked.connect(self._on_save_protocol_settings)
        layout.addWidget(save_button)

        hint = QLabel("저장하면 통신 서버가 바로 다시 시작되고, 접속 중인 앱은 연결이 끊깁니다.")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        return layout

    def _build_lens_section(self) -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.setSpacing(8)

        title = QLabel("렌즈 왜곡 보정")
        title.setObjectName("section")
        layout.addWidget(title)

        hint = QLabel(
            "격자선을 켜고, 매트 가장자리가 격자선과 나란한 직선이 되도록 슬라이더를 움직이세요. "
            "가운데가 부풀어 보이면 - 쪽, 오목해 보이면 + 쪽입니다."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        slider_row = QHBoxLayout()
        self.k1_slider = QSlider(Qt.Horizontal)
        self.k1_slider.setRange(K1_SLIDER_MIN, K1_SLIDER_MAX)
        self.k1_slider.setSingleStep(1)  # 화살표 키 1칸 = 0.01
        self.k1_slider.setPageStep(5)
        self.k1_slider.valueChanged.connect(self._on_k1_slider_changed)
        slider_row.addWidget(self.k1_slider, 1)
        # 현재 값 표시 (예: k1 = -0.12)
        self.k1_value_label = QLabel("k1 = 0.00")
        self.k1_value_label.setMinimumWidth(90)
        slider_row.addWidget(self.k1_value_label)
        layout.addLayout(slider_row)

        self.grid_checkbox = QCheckBox("카메라 화면에 격자선 표시")
        self.grid_checkbox.toggled.connect(self.grid_toggled.emit)
        layout.addWidget(self.grid_checkbox)

        self.debug_checkbox = QCheckBox("검출 디버그 표시 (공 면적·지름)")
        self.debug_checkbox.toggled.connect(self.detection_debug_toggled.emit)
        layout.addWidget(self.debug_checkbox)

        button_row = QHBoxLayout()
        button_row.setSpacing(12)
        cancel_button = QPushButton("취소")
        cancel_button.clicked.connect(self._on_cancel_lens)
        save_button = QPushButton("저장")
        save_button.setObjectName("primary")
        save_button.clicked.connect(self._on_save_lens)
        button_row.addWidget(cancel_button)
        button_row.addWidget(save_button)
        layout.addLayout(button_row)

        lens_hint = QLabel("값을 바꿔 저장하면 화면 좌표가 달라지므로 캘리브레이션을 다시 해야 합니다.")
        lens_hint.setWordWrap(True)
        layout.addWidget(lens_hint)

        return layout

    # ------------------------------------------------------------------
    # 불러오기 / 새로고침
    # ------------------------------------------------------------------
    def reload_from_settings(self) -> None:
        """config/settings.json에서 최신 프로필·매트 설정을 다시 불러온다.

        메인 창이 이 다이얼로그를 다시 열 때마다 호출해서, 그 사이에
        다른 곳(예: HSV 튜닝 창)에서 바뀐 내용도 반영되게 한다.
        """
        settings = load_settings()

        detection = settings.get("detection", {})
        self._profiles = {
            **DEFAULT_DETECTION_SETTINGS["profiles"],
            **detection.get("profiles", {}),
        }
        self._active_profile_key = detection.get(
            "active_profile", DEFAULT_DETECTION_SETTINGS["active_profile"]
        )

        calibration = settings.get("calibration", {})
        self.mat_width_input.setValue(calibration.get("mat_width_mm", DEFAULT_MAT_WIDTH_MM))
        self.mat_height_input.setValue(calibration.get("mat_height_mm", DEFAULT_MAT_HEIGHT_MM))
        self.filter_checkbox.setChecked(
            calibration.get("filter_outside_mat", DEFAULT_FILTER_OUTSIDE_MAT)
        )

        protocol = settings.get("protocol", {})
        self.protocol_enabled_checkbox.setChecked(protocol.get("enabled", DEFAULT_ENABLED))
        self.protocol_port_input.setValue(protocol.get("port", DEFAULT_PORT))

        camera = settings.get("camera", {})
        k1 = camera.get("undistort_k1", DEFAULT_UNDISTORT_K1)
        if isinstance(k1, bool) or not isinstance(k1, (int, float)):
            k1 = DEFAULT_UNDISTORT_K1
        self._saved_k1 = float(k1)
        self._set_k1_slider(self._saved_k1)

        self._refresh_profile_list()

    def _refresh_profile_list(self) -> None:
        self.profile_list.blockSignals(True)
        self.profile_list.clear()
        active_item = None
        for key, profile in self._profiles.items():
            name = profile.get("name", key)
            item = QListWidgetItem(name)
            item.setData(PROFILE_KEY_ROLE, key)
            if key == self._active_profile_key:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                active_item = item
            self.profile_list.addItem(item)
        if active_item is not None:
            # setSelected()만으로는 currentItem()이 갱신되지 않아서, 새로고침 직후
            # 바로 '이름 변경'/'삭제'를 누르면 아무 항목도 선택 안 된 것처럼 동작하는
            # 문제가 있었다. setCurrentItem()으로 실제 '현재 항목'까지 맞춰준다.
            self.profile_list.setCurrentItem(active_item)
            active_item.setSelected(True)
        self.profile_list.blockSignals(False)

        active_name = self._profiles.get(self._active_profile_key, {}).get(
            "name", self._active_profile_key
        )
        self.active_profile_label.setText(f"현재 활성 프로필: {active_name}")

    # ------------------------------------------------------------------
    # 프로필 목록 조작 (클릭 즉시 저장)
    # ------------------------------------------------------------------
    def _on_profile_clicked(self, item: QListWidgetItem) -> None:
        key = item.data(PROFILE_KEY_ROLE)
        if key == self._active_profile_key:
            return
        self._active_profile_key = key
        self._save_detection_settings()
        self._refresh_profile_list()
        self.settings_changed.emit()

    def _on_add_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "프로필 추가", "새 프로필 이름:")
        name = name.strip()
        if not ok or not name:
            return
        if name in [p.get("name", k) for k, p in self._profiles.items()]:
            QMessageBox.warning(self, "프로필 추가", "이미 있는 이름입니다.")
            return

        # 현재 활성 프로필의 HSV 값을 복사해서 시작점으로 삼는다.
        # ('설정 > 검출 설정...'에서 마스크 미리보기를 보며 다시 튜닝하면 된다)
        template = self._profiles.get(
            self._active_profile_key, DEFAULT_DETECTION_SETTINGS["profiles"]["practice"]
        )
        self._profiles[name] = {
            "name": name,
            "hsv_lower": list(template.get("hsv_lower", [0, 0, 0])),
            "hsv_upper": list(template.get("hsv_upper", [179, 255, 255])),
            "min_area": template.get("min_area", DEFAULT_MIN_AREA),
            "max_area": template.get("max_area", DEFAULT_MAX_AREA),
            "min_circularity": template.get("min_circularity", DEFAULT_MIN_CIRCULARITY),
        }
        self._active_profile_key = name
        self._save_detection_settings()
        self._refresh_profile_list()
        self.settings_changed.emit()

    def _on_rename_profile(self) -> None:
        item = self.profile_list.currentItem()
        if item is None:
            QMessageBox.information(self, "이름 변경", "이름을 바꿀 프로필을 목록에서 선택해주세요.")
            return

        key = item.data(PROFILE_KEY_ROLE)
        old_name = self._profiles[key].get("name", key)
        new_name, ok = QInputDialog.getText(self, "이름 변경", "새 이름:", text=old_name)
        new_name = new_name.strip()
        if not ok or not new_name or new_name == old_name:
            return

        self._profiles[key]["name"] = new_name
        self._save_detection_settings()
        self._refresh_profile_list()
        self.settings_changed.emit()

    def _on_delete_profile(self) -> None:
        item = self.profile_list.currentItem()
        if item is None:
            QMessageBox.information(self, "삭제", "삭제할 프로필을 목록에서 선택해주세요.")
            return

        if len(self._profiles) <= 1:
            QMessageBox.warning(self, "삭제", "프로필은 최소 1개가 있어야 합니다.")
            return

        key = item.data(PROFILE_KEY_ROLE)
        name = self._profiles[key].get("name", key)
        reply = QMessageBox.question(
            self, "삭제", f"'{name}' 프로필을 삭제할까요?"
        )
        if reply != QMessageBox.Yes:
            return

        del self._profiles[key]
        if self._active_profile_key == key:
            self._active_profile_key = next(iter(self._profiles))

        self._save_detection_settings()
        self._refresh_profile_list()
        self.settings_changed.emit()

    def _save_detection_settings(self) -> None:
        settings = load_settings()
        settings["detection"] = {
            "active_profile": self._active_profile_key,
            "profiles": self._profiles,
        }
        save_settings(settings)

    # ------------------------------------------------------------------
    # 매트 설정 ('저장' 버튼을 눌러야 반영)
    # ------------------------------------------------------------------
    def _on_save_mat_settings(self) -> None:
        # 기존에 저장된 모서리·변환 행렬을 그대로 불러온 뒤 크기만 바꾼다 -
        # 모서리가 이미 있으면 재캘리브레이션 없이 새 크기로 행렬이 다시 계산된다.
        calibration = Calibration()
        calibration.set_mat_size(self.mat_width_input.value(), self.mat_height_input.value())
        calibration.filter_outside_mat = self.filter_checkbox.isChecked()
        calibration.save()

        self.settings_changed.emit()

    # ------------------------------------------------------------------
    # 앱 통신 설정 ('저장' 버튼을 눌러야 반영)
    # ------------------------------------------------------------------
    def _on_save_protocol_settings(self) -> None:
        # 기존 protocol 항목(addr_radius_mm, status_hz 등)은 그대로 두고
        # 사용 여부와 포트만 바꾼다.
        settings = load_settings()
        protocol = settings.setdefault("protocol", {})
        protocol["enabled"] = self.protocol_enabled_checkbox.isChecked()
        protocol["port"] = self.protocol_port_input.value()
        save_settings(settings)

        self.protocol_settings_changed.emit()

    # ------------------------------------------------------------------
    # 렌즈 왜곡 보정 (슬라이더는 실시간 미리보기, '저장'을 눌러야 반영)
    # ------------------------------------------------------------------
    def _slider_k1(self) -> float:
        """슬라이더의 정수 값을 k1(실수)로 바꾼다. 예: -12 -> -0.12"""
        return self.k1_slider.value() / K1_SLIDER_STEPS_PER_UNIT

    def _set_k1_slider(self, k1: float) -> None:
        """k1 값에 맞게 슬라이더와 값 표시를 맞춘다 (이때는 미리보기 시그널을 보내지 않는다)."""
        value = round(k1 * K1_SLIDER_STEPS_PER_UNIT)
        value = max(K1_SLIDER_MIN, min(K1_SLIDER_MAX, value))
        self.k1_slider.blockSignals(True)
        self.k1_slider.setValue(value)
        self.k1_slider.blockSignals(False)
        self.k1_value_label.setText(f"k1 = {k1:+.2f}")

    def _on_k1_slider_changed(self, _value: int) -> None:
        k1 = self._slider_k1()
        self.k1_value_label.setText(f"k1 = {k1:+.2f}")
        # 메인 화면이 엔진의 전처리 값을 바로 바꿔서 카메라 영상에 반영한다 (저장은 아직 안 함)
        self.lens_k1_preview.emit(k1)

    def _has_unsaved_k1(self) -> bool:
        return abs(self._slider_k1() - self._saved_k1) > 1e-9

    def _on_save_lens(self) -> None:
        """'저장': 지금 슬라이더 값을 config/settings.json의 camera.undistort_k1에 저장한다."""
        k1 = self._slider_k1()
        settings = load_settings()
        settings.setdefault("camera", {})["undistort_k1"] = k1
        save_settings(settings)
        self._saved_k1 = k1
        self.k1_value_label.setText(f"k1 = {k1:+.2f} (저장됨)")

    def _on_cancel_lens(self) -> None:
        """'취소': 저장된 값으로 슬라이더와 화면을 되돌린다."""
        self._set_k1_slider(self._saved_k1)
        self.lens_k1_preview.emit(self._saved_k1)

    def closeEvent(self, event) -> None:
        """저장하지 않고 창을 닫으면 k1 미리보기를 저장된 값으로 되돌린다.

        격자선·디버그 표시는 저장하는 값이 아니라 '보기 옵션'이라 창을 닫아도 그대로 둔다
        (창을 닫고 공을 여기저기 옮겨 가며 면적을 재기 편하게). 다시 열어서 끄면 된다.
        """
        if self._has_unsaved_k1():
            self._on_cancel_lens()
        super().closeEvent(event)
