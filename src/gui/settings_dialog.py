from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.config import AppSettings, ConfigStore
from core.models import FILENAME_ORIGINAL, FILENAME_TITLE, FILENAME_TITLE_CHARACTER


class PathField(QWidget):
    def __init__(self, value: str = "", file_mode: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.file_mode = file_mode
        self.edit = QLineEdit(value)
        self.button = QPushButton("参照...")
        self.button.clicked.connect(self._browse)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.button)

    def _browse(self) -> None:
        start = self.edit.text() or str(Path.home())
        if self.file_mode:
            value, _ = QFileDialog.getOpenFileName(
                self, "octocacheevaiを選択", start, "Octo Manifest (octocacheevai);;すべてのファイル (*)"
            )
        else:
            value = QFileDialog.getExistingDirectory(self, "フォルダを選択", start)
        if value:
            self.edit.setText(value)

    def text(self) -> str:
        return self.edit.text().strip()


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, store: ConfigStore, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self.store = store
        self.setWindowTitle("設定")
        self.setMinimumWidth(680)

        tabs = QTabWidget()
        paths_tab = QWidget()
        path_form = QFormLayout(paths_tab)
        self.game_dir = PathField(settings.game_data_dir)
        self.manifest = PathField(settings.manifest_path, file_mode=True)
        self.output = PathField(str(settings.resolved_output_dir))
        self.mode = QComboBox()
        self.mode.addItem("ローカルPC版を優先", "local_preferred")
        self.mode.addItem("オンラインPC版", "online")
        self.mode.setCurrentIndex(max(0, self.mode.findData(settings.manifest_mode)))
        self.online_fallback = QCheckBox("ローカル読込失敗時にオンラインPC版を試す")
        self.online_fallback.setChecked(settings.online_fallback)
        path_form.addRow("学マスデータフォルダ", self.game_dir)
        path_form.addRow("Manifestパス（空欄で自動）", self.manifest)
        path_form.addRow("Manifest取得方法", self.mode)
        path_form.addRow("", self.online_fallback)
        path_form.addRow("デフォルト出力先", self.output)
        tabs.addTab(paths_tab, "パス / Manifest")

        defaults_tab = QWidget()
        defaults_form = QFormLayout(defaults_tab)
        self.wav = QCheckBox("WAV（ゲーム音源をPCMへデコード）")
        self.flac = QCheckBox("FLAC（WAVから変換）")
        self.awb = QCheckBox("元AWB")
        self.mp3 = QCheckBox("Manifest収録MP3")
        self.acb = QCheckBox("元ACB")
        self.artwork = QCheckBox("アルバムアートをWAV／FLAC／MP3に埋め込む")
        self.artwork_file = QCheckBox("アルバムアートを画像ファイルでも保存")
        self.wav.setChecked(settings.default_wav)
        self.flac.setChecked(settings.default_flac)
        self.awb.setChecked(settings.default_awb)
        self.mp3.setChecked(settings.default_mp3)
        self.acb.setChecked(settings.default_acb)
        self.artwork.setChecked(settings.default_artwork)
        self.artwork_file.setChecked(settings.default_artwork_file)
        self.filename_format = QComboBox()
        self.filename_format.addItem("楽曲名＿キャラクター名［ライブ・短縮版など］", FILENAME_TITLE_CHARACTER)
        self.filename_format.addItem("元のファイル名そのまま", FILENAME_ORIGINAL)
        self.filename_format.addItem("楽曲名［ライブ・短縮版など］", FILENAME_TITLE)
        format_index = self.filename_format.findData(settings.filename_format)
        self.filename_format.setCurrentIndex(format_index if format_index >= 0 else 0)
        self.auto_scan = QCheckBox("起動時に自動スキャン")
        self.auto_scan.setChecked(settings.auto_scan)
        self.theme = QComboBox()
        self.theme.addItem("Windows設定に合わせる", "system")
        self.theme.addItem("ダーク", "dark")
        self.theme.addItem("ライト", "light")
        self.theme.setCurrentIndex(max(0, self.theme.findData(settings.theme)))
        for widget in (
            self.wav,
            self.flac,
            self.awb,
            self.mp3,
            self.acb,
            self.artwork,
            self.artwork_file,
            self.auto_scan,
        ):
            defaults_form.addRow("", widget)
        defaults_form.addRow("既定のファイル名", self.filename_format)
        defaults_form.addRow("表示テーマ", self.theme)
        tabs.addTab(defaults_tab, "抽出 / 表示")

        mapping_tab = QWidget()
        mapping_layout = QVBoxLayout(mapping_tab)
        mapping_layout.addWidget(
            QLabel(
                "characters.json と song_names.json は設定フォルダにあります。\n"
                "未知IDや曲名は推測せず、ここへ明示的に登録した値だけを表示します。"
            )
        )
        open_button = QPushButton("マッピング設定フォルダを開く")
        open_button.clicked.connect(self._open_mapping_dir)
        mapping_layout.addWidget(open_button)
        mapping_layout.addStretch(1)
        tabs.addTab(mapping_tab, "名前マッピング")

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("キャンセル")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    def _open_mapping_dir(self) -> None:
        self.store.ensure_mapping_files()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.root)))

    def result_settings(self) -> AppSettings:
        return AppSettings(
            game_data_dir=self.game_dir.text(),
            manifest_path=self.manifest.text(),
            output_dir=self.output.text(),
            default_wav=self.wav.isChecked(),
            default_flac=self.flac.isChecked(),
            default_awb=self.awb.isChecked(),
            default_mp3=self.mp3.isChecked(),
            default_acb=self.acb.isChecked(),
            default_artwork=self.artwork.isChecked(),
            default_artwork_file=self.artwork_file.isChecked(),
            filename_format=str(self.filename_format.currentData()),
            filter_types=list(self.settings.filter_types),
            auto_scan=self.auto_scan.isChecked(),
            online_fallback=self.online_fallback.isChecked(),
            manifest_mode=str(self.mode.currentData()),
            theme=str(self.theme.currentData()),
        )
