from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.config import AppSettings
from core.models import (
    FILENAME_ORIGINAL,
    FILENAME_TITLE,
    FILENAME_TITLE_CHARACTER,
    ExtractionOptions,
)


class ExtractionDialog(QDialog):
    def __init__(self, selected_count: int, settings: AppSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("抽出オプション")
        self.setMinimumWidth(560)

        title = QLabel(f"選択した {selected_count} 曲を抽出します")
        title.setObjectName("dialogTitle")

        formats = QGroupBox("保存形式")
        formats_layout = QVBoxLayout(formats)
        self.wav = QCheckBox("WAV（ゲーム音源をPCMへデコード）")
        self.awb = QCheckBox("元AWB")
        self.mp3 = QCheckBox("Manifest収録MP3")
        self.acb = QCheckBox("元ACB")
        self.wav.setChecked(settings.default_wav)
        self.awb.setChecked(settings.default_awb)
        self.mp3.setChecked(settings.default_mp3)
        self.acb.setChecked(settings.default_acb)
        for item in (self.wav, self.awb, self.mp3, self.acb):
            formats_layout.addWidget(item)

        extras = QGroupBox("追加オプション")
        extras_layout = QVBoxLayout(extras)
        self.live = QCheckBox("関連するライブ音源も取得（normal / trueを区別）")
        self.artwork = QCheckBox("アルバムアートをPNG保存し、WAV／MP3に埋め込む")
        self.artwork.setChecked(settings.default_artwork)
        self.artwork.setToolTip("対応画像がない曲は音源のみ保存します")
        extras_layout.addWidget(self.live)
        extras_layout.addWidget(self.artwork)

        self.filename_format = QComboBox()
        self.filename_format.addItem("楽曲名＿キャラクター名［ライブ・短縮版など］", FILENAME_TITLE_CHARACTER)
        self.filename_format.addItem("元のファイル名そのまま", FILENAME_ORIGINAL)
        self.filename_format.addItem("楽曲名［ライブ・短縮版など］", FILENAME_TITLE)
        format_index = self.filename_format.findData(settings.filename_format)
        self.filename_format.setCurrentIndex(format_index if format_index >= 0 else 0)

        self.output = QLineEdit(str(settings.resolved_output_dir))
        browse = QPushButton("参照...")
        browse.clicked.connect(self._browse)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output, 1)
        output_row.addWidget(browse)

        form = QFormLayout()
        form.addRow("ファイル名", self.filename_format)
        form.addRow("出力先", output_row)

        note = QLabel(
            "WAVはゲーム内のHCA等の圧縮音源をPCMへデコードしたものです。\n"
            "CD等のロスレスマスターを復元するものではありません。\n"
            "曲名が未登録の場合は内部名を使用します。同名衝突時だけ識別子を補います。"
        )
        note.setWordWrap(True)
        note.setObjectName("noteLabel")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("抽出開始")
        buttons.button(QDialogButtonBox.Cancel).setText("キャンセル")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(formats)
        layout.addWidget(extras)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        value = QFileDialog.getExistingDirectory(
            self, "出力先を選択", self.output.text() or str(Path.home())
        )
        if value:
            self.output.setText(value)

    def _validate(self) -> None:
        if not any(item.isChecked() for item in (self.wav, self.awb, self.mp3, self.acb)):
            QMessageBox.warning(self, "保存形式", "保存形式を1つ以上選択してください。")
            return
        if not self.output.text().strip():
            QMessageBox.warning(self, "出力先", "出力先を指定してください。")
            return
        self.accept()

    def options(self) -> ExtractionOptions:
        return ExtractionOptions(
            output_dir=Path(self.output.text().strip()).expanduser(),
            save_wav=self.wav.isChecked(),
            save_awb=self.awb.isChecked(),
            save_mp3=self.mp3.isChecked(),
            save_acb=self.acb.isChecked(),
            save_artwork=self.artwork.isChecked(),
            include_live=self.live.isChecked(),
            filename_format=str(self.filename_format.currentData()),
        )
