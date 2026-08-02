"""
설정 다이얼로그: 검출 프로필 관리 + 매트 크기/필터 설정.

HSV 슬라이더로 실시간 튜닝하는 TuningDialog와 달리, 이 다이얼로그는
- 검출 프로필 목록 관리(선택·추가·삭제·이름 변경)
- 매트 실제 크기(mat_width_mm/mat_height_mm)
- 매트 밖 필터(filter_outside_mat) on/off
같은 '폼' 형태의 설정을 다룬다. 실시간 카메라 미리보기는 필요 없다.

프로필 목록 조작(선택·추가·삭제·이름 변경)은 클릭하는 즉시
config/settings.json에 저장된다. 매트 설정은 '매트 설정 저장' 버튼을
눌러야 저장된다. 뭔가 저장될 때마다 settings_changed 시그널을 보내서,
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

PROFILE_KEY_ROLE = Qt.UserRole


class SettingsDialog(QDialog):
    """검출 프로필 관리 + 매트 설정 화면."""

    # 프로필/매트 설정이 저장돼서 메인 창이 detector·calibration을 다시 불러와야 할 때
    settings_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("설정")
        self.resize(420, 520)

        self._profiles: dict = {}
        self._active_profile_key: str = DEFAULT_DETECTION_SETTINGS["active_profile"]

        layout = QVBoxLayout(self)
        layout.addLayout(self._build_profile_section())
        layout.addLayout(self._build_mat_section())
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
