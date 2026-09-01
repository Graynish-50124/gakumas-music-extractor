from __future__ import annotations

import threading
import traceback
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QModelIndex, QObject, QThread, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import (
    QCloseEvent,
    QDesktopServices,
    QFont,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.config import AppSettings, ConfigStore
from core.extractor import ExtractionCancelled, ExtractionEngine
from core.manifest import ManifestError, load_preferred_manifest
from core.models import (
    KIND_BGM,
    KIND_CHARACTER,
    KIND_GENERAL,
    KIND_LIVE,
    KIND_UNIT,
    DEFAULT_FILTER_TYPES,
    MUSIC_KINDS,
    SINGING_INST,
    SINGING_VOCAL,
    ExtractionOptions,
    ScanResult,
    SongGroup,
    SongMetadata,
)
from core.scanner import filter_groups, scan_music_assets
from gui.extraction_dialog import ExtractionDialog
from gui.settings_dialog import SettingsDialog


class FullCellCheckDelegate(QStyledItemDelegate):
    """Make the whole selection cell a checkbox hit target."""

    toggled = Signal(int, bool, bool)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._suppress_mouse_release = False

    def suppress_next_mouse_release(self) -> None:
        self._suppress_mouse_release = True

    def clear_mouse_release_suppression(self) -> None:
        self._suppress_mouse_release = False

    def editorEvent(self, event, model, option: QStyleOptionViewItem, index: QModelIndex) -> bool:
        if not (index.flags() & Qt.ItemIsEnabled and index.flags() & Qt.ItemIsUserCheckable):
            return False

        extend_range = False
        if event.type() == QEvent.MouseButtonRelease:
            if self._suppress_mouse_release:
                self._suppress_mouse_release = False
                return True
            if event.button() != Qt.LeftButton or not option.rect.contains(event.position().toPoint()):
                return False
            extend_range = bool(event.modifiers() & Qt.ShiftModifier)
        elif event.type() == QEvent.KeyPress:
            if event.key() not in (Qt.Key_Space, Qt.Key_Select):
                return False
            extend_range = bool(event.modifiers() & Qt.ShiftModifier)
        else:
            return False

        current = index.data(Qt.CheckStateRole)
        next_state = Qt.Unchecked if current == Qt.Checked else Qt.Checked
        if not model.setData(index, next_state, Qt.CheckStateRole):
            return False
        self.toggled.emit(index.row(), next_state == Qt.Checked, extend_range)
        return True


class CheckableComboBox(QComboBox):
    """A compact multi-select combo box with a checkbox for every item."""

    selectionChanged = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._check_model = QStandardItemModel(self)
        self.setModel(self._check_model)
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().installEventFilter(self)
        self.view().viewport().installEventFilter(self)

    def add_check_item(self, label: str, value: str, checked: bool = False) -> None:
        item = QStandardItem(label)
        item.setData(value, Qt.UserRole)
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
        item.setData(Qt.Checked if checked else Qt.Unchecked, Qt.CheckStateRole)
        self._check_model.appendRow(item)
        self._update_display_text()

    def checked_data(self) -> list[str]:
        return [
            str(item.data(Qt.UserRole))
            for row in range(self._check_model.rowCount())
            if (item := self._check_model.item(row)) is not None
            and item.data(Qt.CheckStateRole) == Qt.Checked
        ]

    def set_checked_data(self, values: Iterable[str]) -> None:
        selected = {str(value) for value in values}
        for row in range(self._check_model.rowCount()):
            item = self._check_model.item(row)
            if item is not None:
                item.setData(
                    Qt.Checked if str(item.data(Qt.UserRole)) in selected else Qt.Unchecked,
                    Qt.CheckStateRole,
                )
        self._update_display_text()

    def _toggle_index(self, index: QModelIndex) -> bool:
        if not index.isValid():
            return False
        item = self._check_model.itemFromIndex(index)
        if item is None:
            return False
        checked = item.data(Qt.CheckStateRole) == Qt.Checked
        item.setData(Qt.Unchecked if checked else Qt.Checked, Qt.CheckStateRole)
        self._update_display_text()
        self.selectionChanged.emit()
        return True

    def _update_display_text(self) -> None:
        values = self.checked_data()
        if not values:
            text = "未選択"
        elif len(values) <= 2:
            text = "＋".join(values)
        else:
            text = f"{len(values)}種類選択"
        self.setCurrentIndex(-1)
        self.lineEdit().setText(text)
        self.setToolTip("選択中: " + ("、".join(values) if values else "なし"))

    def eventFilter(self, watched: QObject, event) -> bool:
        if watched is self.lineEdit() and event.type() == QEvent.MouseButtonRelease:
            if event.button() == Qt.LeftButton and self.isEnabled():
                self.showPopup()
                return True
        if watched is self.view().viewport():
            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                index = self.view().indexAt(event.position().toPoint())
                return self._toggle_index(index)
            if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Space:
                return self._toggle_index(self.view().currentIndex())
        return super().eventFilter(watched, event)


class ScanWorker(QObject):
    finished = Signal(object)
    failed = Signal(str, str)
    log = Signal(str)

    def __init__(
        self,
        settings: AppSettings,
        song_names: dict[str, str],
        song_metadata: dict[str, SongMetadata],
    ):
        super().__init__()
        self.settings = settings
        self.song_names = song_names
        self.song_metadata = song_metadata

    @Slot()
    def run(self) -> None:
        try:
            self.log.emit("学マスPC版とManifestを検出中...")
            manifest, info = load_preferred_manifest(
                octo_root=self.settings.game_data_dir or None,
                manifest_path=self.settings.manifest_path or None,
                mode=self.settings.manifest_mode,
                online_fallback=self.settings.online_fallback,
            )
            self.log.emit(f"Manifest復号・解析成功: Revision {info.revision}")
            groups = scan_music_assets(manifest, self.song_names, self.song_metadata)
            self.log.emit(f"音楽アセットをグループ化: {len(groups):,}件")
            self.log.emit(f"アルバムアート対応: {sum(group.has_artwork for group in groups):,}件")
            self.log.emit(
                f"正式楽曲情報対応: {sum(group.metadata.has_official_info for group in groups):,}件"
            )
            self.finished.emit(ScanResult(manifest=manifest, info=info, groups=groups))
        except Exception as exc:
            message = str(exc) if isinstance(exc, ManifestError) else "Manifestの読み込みに失敗しました"
            self.failed.emit(message, traceback.format_exc())


class ExtractionWorker(QObject):
    finished = Signal(object)
    failed = Signal(str, str)
    cancelled = Signal(str)
    progress = Signal(int, str)
    log = Signal(str)

    def __init__(
        self,
        selected: list[SongGroup],
        catalog: list[SongGroup],
        options: ExtractionOptions,
        octo_root: Path | None,
        characters: dict[str, str],
    ):
        super().__init__()
        self.selected = selected
        self.catalog = catalog
        self.options = options
        self.octo_root = octo_root
        self.characters = characters
        self.cancel_event = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            engine = ExtractionEngine(
                self.octo_root,
                characters=self.characters,
                progress=lambda value, text: self.progress.emit(value, text),
                log=self.log.emit,
                cancel_event=self.cancel_event,
            )
            self.finished.emit(engine.extract(self.selected, self.catalog, self.options))
        except ExtractionCancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            self.failed.emit(str(exc) or "抽出に失敗しました", traceback.format_exc())

    def request_cancel(self) -> None:
        self.cancel_event.set()


class MainWindow(QMainWindow):
    COLUMNS = (
        "選択", "曲ID", "曲名", "作曲", "収録作品", "キャラクター", "種類", "歌唱", "バージョン",
        "短縮版", "AWB", "MP3", "ACB", "Live", "ジャケット",
    )

    def __init__(
        self,
        settings: AppSettings,
        store: ConfigStore,
        auto_start: bool = True,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.settings = settings
        self.store = store
        self.store.ensure_mapping_files()
        self.characters = self.store.load_mapping("characters.json")
        self.song_names = self.store.load_mapping("song_names.json")
        self.song_metadata = self.store.load_song_metadata()
        self.scan_result: ScanResult | None = None
        self.checked_keys: set[str] = set()
        self._populating = False
        self._scan_thread: QThread | None = None
        self._scan_worker: ScanWorker | None = None
        self._extract_thread: QThread | None = None
        self._extract_worker: ExtractionWorker | None = None
        self._last_check_row: int | None = None
        self._drag_check_start_row: int | None = None
        self._drag_check_last_row: int | None = None
        self._drag_check_start_pos = None
        self._drag_check_active = False

        self.setWindowTitle("Gakumas Music Extractor")
        self.resize(1280, 820)
        self.setMinimumSize(940, 650)
        self._build_ui()
        self.apply_theme(settings.theme)
        if auto_start and settings.auto_scan:
            QTimer.singleShot(0, self.start_scan)

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Gakumas Music Extractor")
        title.setObjectName("appTitle")
        subtitle = QLabel("学マスPC版ローカルデータから音楽アセットを抽出")
        subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch(1)
        self.rescan_button = QPushButton("再スキャン")
        self.rescan_button.setToolTip("Manifest、曲名、正式楽曲情報を読み直します")
        self.rescan_button.clicked.connect(self.start_scan)
        self.settings_button = QPushButton("設定")
        self.settings_button.setToolTip("データ場所、保存形式、表示テーマなどを変更します")
        self.settings_button.clicked.connect(self.open_settings)
        header.addWidget(self.rescan_button)
        header.addWidget(self.settings_button)
        root.addLayout(header)

        manifest_card = QFrame()
        manifest_card.setObjectName("card")
        manifest_layout = QHBoxLayout(manifest_card)
        self.manifest_label = QLabel("Manifest: 未読込")
        self.manifest_label.setObjectName("manifestLabel")
        self.manifest_detail = QLabel("再スキャンでローカルPC版を検出します")
        self.manifest_detail.setObjectName("subtitle")
        manifest_layout.addWidget(self.manifest_label)
        manifest_layout.addStretch(1)
        manifest_layout.addWidget(self.manifest_detail)
        root.addWidget(manifest_card)

        filter_card = QFrame()
        filter_card.setObjectName("card")
        filter_layout = QVBoxLayout(filter_card)
        filter_layout.setContentsMargins(14, 10, 14, 12)
        filter_layout.setSpacing(8)

        filter_header = QHBoxLayout()
        filter_title = QLabel("絞り込み")
        filter_title.setObjectName("sectionTitle")
        self.result_count_label = QLabel("表示: 0 / 0件")
        self.result_count_label.setObjectName("badgeLabel")
        self.reset_filters_button = QPushButton("条件をクリア")
        self.reset_filters_button.setObjectName("compactButton")
        self.reset_filters_button.setToolTip("すべての絞り込み条件を初期状態へ戻します")
        self.reset_filters_button.clicked.connect(self._reset_filters)
        filter_header.addWidget(filter_title)
        filter_header.addWidget(self.result_count_label)
        filter_header.addStretch(1)
        filter_header.addWidget(self.reset_filters_button)
        filter_layout.addLayout(filter_header)

        filters = QGridLayout()
        filters.setHorizontalSpacing(10)
        filters.setVerticalSpacing(4)
        self.character_filter = QComboBox()
        self.character_filter.addItem("全キャラクター", "")
        self.character_filter.setMinimumWidth(220)
        self.character_filter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.character_filter.setToolTip("キャラクター名またはIDで対象を絞り込みます")
        self.type_filter = CheckableComboBox()
        self.type_filter.setMinimumWidth(230)
        selected_types = set(self.settings.filter_types)
        for kind in MUSIC_KINDS:
            self.type_filter.add_check_item(kind, kind, kind in selected_types)
        self.singing_filter = QComboBox()
        self.singing_filter.addItem("すべて", "")
        self.singing_filter.addItem(SINGING_VOCAL, SINGING_VOCAL)
        self.singing_filter.addItem(SINGING_INST, SINGING_INST)
        self.singing_filter.setMinimumWidth(110)
        self.short_filter = QComboBox()
        self.short_filter.addItem("すべて", None)
        self.short_filter.addItem("通常版のみ", False)
        self.short_filter.addItem("短縮版のみ", True)
        self.short_filter.setCurrentIndex(1)
        self.short_filter.setMinimumWidth(130)
        self.search = QLineEdit()
        self.search.setPlaceholderText("曲名、作詞・作曲者、収録作品、内部IDで検索（例: 018）")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(180)

        filter_fields = (
            ("キャラクター", self.character_filter),
            ("種類", self.type_filter),
            ("歌唱", self.singing_filter),
            ("短縮版", self.short_filter),
            ("検索", self.search),
        )
        for column, (label_text, widget) in enumerate(filter_fields):
            label = QLabel(label_text)
            label.setObjectName("fieldLabel")
            filters.addWidget(label, 0, column)
            filters.addWidget(widget, 1, column)
        for column, stretch in enumerate((2, 1, 1, 1, 3)):
            filters.setColumnStretch(column, stretch)
        filter_layout.addLayout(filters)
        root.addWidget(filter_card)

        for widget in (self.character_filter, self.singing_filter):
            widget.currentIndexChanged.connect(self.refresh_table)
        self.type_filter.selectionChanged.connect(self._type_filter_changed)
        self.short_filter.currentIndexChanged.connect(self._short_filter_changed)
        self.search.textChanged.connect(self.refresh_table)

        splitter = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setTextElideMode(Qt.ElideRight)
        self.table.viewport().installEventFilter(self)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.check_delegate = FullCellCheckDelegate(self.table)
        self.check_delegate.toggled.connect(self._check_cell_toggled)
        self.table.setItemDelegateForColumn(0, self.check_delegate)
        self.table.itemChanged.connect(self._item_changed)
        self.table.itemDoubleClicked.connect(self._show_details)
        header_view = self.table.horizontalHeader()
        header_view.setSectionsMovable(True)
        header_view.setMinimumSectionSize(48)
        header_view.setSectionResizeMode(QHeaderView.Interactive)
        header_view.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.horizontalHeaderItem(0).setToolTip(
            "選択セル内のどこでもクリックできます。Shift+クリックで範囲選択、"
            "表をドラッグすると青い範囲だけを連続選択できます。"
        )
        for column, width in enumerate(
            (72, 115, 250, 210, 260, 175, 115, 85, 105, 75, 52, 52, 52, 52, 82)
        ):
            self.table.setColumnWidth(column, width)
        splitter.addWidget(self.table)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        self.log_view.setPlaceholderText("処理ログ")
        fixed_font = QFont("Consolas")
        fixed_font.setStyleHint(QFont.Monospace)
        self.log_view.setFont(fixed_font)
        splitter.addWidget(self.log_view)
        splitter.setSizes([540, 150])
        root.addWidget(splitter, 1)

        actions = QHBoxLayout()
        self.selection_label = QLabel("選択: 0曲")
        self.selection_label.setObjectName("selectionLabel")
        self.select_all_button = QPushButton("表示中をまとめて選択")
        self.select_all_button.setToolTip("現在の絞り込み結果を一括で選択します")
        self.select_all_button.clicked.connect(lambda: self._set_visible_checks(True))
        self.clear_visible_button = QPushButton("表示中をまとめて解除")
        self.clear_visible_button.setToolTip("現在の絞り込み結果だけを一括で選択解除します")
        self.clear_visible_button.clicked.connect(lambda: self._set_visible_checks(False))
        self.clear_button = QPushButton("すべて解除")
        self.clear_button.setToolTip("すべての選択を解除します")
        self.clear_button.clicked.connect(self._clear_checks)
        self.extract_button = QPushButton("抽出")
        self.extract_button.setObjectName("primaryButton")
        self.extract_button.setToolTip("選択した曲の抽出設定を開きます")
        self.extract_button.clicked.connect(self.open_extraction)
        actions.addWidget(self.selection_label)
        actions.addWidget(self.select_all_button)
        actions.addWidget(self.clear_visible_button)
        actions.addWidget(self.clear_button)
        actions.addStretch(1)
        actions.addWidget(self.extract_button)
        root.addLayout(actions)

        progress_row = QHBoxLayout()
        self.status_label = QLabel("準備完了")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.cancel_button = QPushButton("キャンセル")
        self.cancel_button.clicked.connect(self.cancel_extraction)
        self.cancel_button.hide()
        progress_row.addWidget(self.status_label, 1)
        progress_row.addWidget(self.progress_bar, 2)
        progress_row.addWidget(self.cancel_button)
        root.addLayout(progress_row)

        self.setCentralWidget(central)

    @Slot()
    def start_scan(self) -> None:
        if self._scan_thread or self._extract_thread:
            return
        # Reload on every scan so bundled catalog updates and user edits are
        # reflected before the user chooses anything to extract.
        self.store.ensure_mapping_files()
        self.characters = self.store.load_mapping("characters.json")
        self.song_names = self.store.load_mapping("song_names.json")
        self.song_metadata = self.store.load_song_metadata()
        self._set_busy(True, "Manifestをスキャン中...")
        self.progress_bar.setRange(0, 0)
        self.append_log("ローカルoctocacheevaiを再検出")
        thread = QThread(self)
        worker = ScanWorker(self.settings, self.song_names, self.song_metadata)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self.append_log)
        worker.finished.connect(self._scan_finished)
        worker.failed.connect(self._operation_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._scan_thread_finished)
        self._scan_thread = thread
        self._scan_worker = worker
        thread.start()

    @Slot(object)
    def _scan_finished(self, result: ScanResult) -> None:
        self.scan_result = result
        self.checked_keys.clear()
        info = result.info
        self.manifest_label.setText(f"Manifest: {info.source}  •  Revision {info.revision}")
        updated = info.updated_at.strftime("%Y/%m/%d %H:%M") if info.updated_at else "不明"
        self.manifest_detail.setText(f"最終更新: {updated}  •  オブジェクト {info.object_count:,}件")
        if info.manifest_path:
            self.append_log(f"Manifest: {info.manifest_path}")
        self.append_log(f"楽曲グループ: {len(result.groups):,}件")
        named_count = sum(bool(group.title) for group in result.groups)
        self.append_log(f"曲名表示: {named_count:,} / {len(result.groups):,}件")
        short_count = sum(group.is_short_version for group in result.groups)
        self.append_log(f"短縮版を識別: {short_count:,}件")
        metadata_count = sum(group.metadata.has_official_info for group in result.groups)
        self.append_log(f"正式楽曲情報: {metadata_count:,} / {len(result.groups):,}件")
        self._rebuild_character_filter()
        self.refresh_table()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self._set_busy(False, f"スキャン完了: {len(result.groups):,}件")

    @Slot()
    def _scan_thread_finished(self) -> None:
        if self._scan_thread:
            self._scan_thread.deleteLater()
        self._scan_thread = None
        self._scan_worker = None

    def _rebuild_character_filter(self) -> None:
        current = self.character_filter.currentData()
        self.character_filter.blockSignals(True)
        self.character_filter.clear()
        self.character_filter.addItem("全キャラクター", "")
        if self.scan_result:
            values = sorted(
                {group.character_id for group in self.scan_result.groups if group.character_id},
                key=lambda char_id: (self.characters.get(char_id, char_id), char_id),
            )
            for char_id in values:
                name = self.characters.get(char_id)
                label = f"{name} ({char_id})" if name else f"{char_id} (未登録)"
                self.character_filter.addItem(label, char_id)
        index = self.character_filter.findData(current)
        self.character_filter.setCurrentIndex(max(index, 0))
        self.character_filter.blockSignals(False)

    @Slot()
    def refresh_table(self) -> None:
        groups = self._visible_groups()
        self._last_check_row = None
        total = len(self.scan_result.groups) if self.scan_result else 0
        self.result_count_label.setText(f"表示: {len(groups):,} / {total:,}件")
        self._populating = True
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(groups))
        for row, group in enumerate(groups):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            check.setCheckState(Qt.Checked if group.key in self.checked_keys else Qt.Unchecked)
            check.setData(Qt.UserRole, group.key)
            check.setToolTip(
                "セル内のどこでもクリック可。Shift+クリックで範囲選択。"
                "表をドラッグすると青い範囲だけを選択できます"
            )
            char_name = self.characters.get(group.character_id)
            character = (
                f"{char_name} ({group.character_id})"
                if char_name
                else (f"{group.character_id} (未登録)" if group.character_id else "—")
            )
            values = (
                group.internal_id,
                group.title or "—",
                group.metadata.composer or "—",
                group.metadata.album or "—",
                character,
                group.data_type,
                group.singing,
                group.version or "—",
                "はい" if group.is_short_version else ("—" if group.data_type == KIND_BGM else "いいえ"),
                "○" if "AWB" in group.assets else "—",
                "○" if "MP3" in group.assets else "—",
                "○" if "ACB" in group.assets else "—",
                "○" if group.has_live else "—",
                "○" if group.has_artwork else "—",
            )
            self.table.setItem(row, 0, check)
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, group.key)
                item.setToolTip(value)
                if column >= 9:
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        self._populating = False
        self._update_selection_label()

    @Slot()
    def _reset_filters(self) -> None:
        for combo in (self.character_filter, self.singing_filter):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self.type_filter.blockSignals(True)
        self.type_filter.set_checked_data(DEFAULT_FILTER_TYPES)
        self.type_filter.blockSignals(False)
        self.settings.filter_types = list(DEFAULT_FILTER_TYPES)
        self.store.save_settings(self.settings)
        self.short_filter.blockSignals(True)
        self.short_filter.setCurrentIndex(1)
        self.short_filter.blockSignals(False)
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.refresh_table()

    def _visible_groups(self) -> list[SongGroup]:
        if not self.scan_result:
            return []
        return filter_groups(
            self.scan_result.groups,
            character_id=str(self.character_filter.currentData() or ""),
            data_types=self.type_filter.checked_data(),
            singing=str(self.singing_filter.currentData() or ""),
            short_version=self.short_filter.currentData(),
            search=self.search.text(),
        )

    @Slot()
    def _type_filter_changed(self) -> None:
        self.settings.filter_types = self.type_filter.checked_data()
        self.store.save_settings(self.settings)
        self.refresh_table()

    @Slot()
    def _short_filter_changed(self) -> None:
        short_version = self.short_filter.currentData()
        if self.scan_result and short_version is not None:
            allowed = {
                group.key
                for group in self.scan_result.groups
                if group.is_short_version is short_version
            }
            self.checked_keys.intersection_update(allowed)
        self.refresh_table()

    @Slot(QTableWidgetItem)
    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self._populating or item.column() != 0:
            return
        key = str(item.data(Qt.UserRole))
        if item.checkState() == Qt.Checked:
            self.checked_keys.add(key)
        else:
            self.checked_keys.discard(key)
        self._update_selection_label()

    @Slot(int, bool, bool)
    def _check_cell_toggled(self, row: int, checked: bool, extend_range: bool) -> None:
        if extend_range and self._last_check_row is not None:
            first, last = sorted((self._last_check_row, row))
            self._set_row_check_range(first, last, checked)
        self._last_check_row = row

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.table.viewport():
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                index = self.table.indexAt(event.position().toPoint())
                if index.isValid():
                    self._drag_check_start_row = index.row()
                    self._drag_check_last_row = index.row()
                    self._drag_check_start_pos = event.position().toPoint()
                    self._drag_check_active = False
            elif event.type() == QEvent.MouseMove and event.buttons() & Qt.LeftButton:
                if self._drag_check_start_row is not None and self._drag_check_start_pos is not None:
                    position = event.position().toPoint()
                    index = self.table.indexAt(position)
                    if index.isValid():
                        distance = (position - self._drag_check_start_pos).manhattanLength()
                        if index.row() != self._drag_check_start_row or distance >= QApplication.startDragDistance():
                            self._drag_check_active = True
                            first, last = sorted((self._drag_check_start_row, index.row()))
                            self._set_exclusive_row_check_range(first, last)
                            self._drag_check_last_row = index.row()
            elif event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                if self._drag_check_active:
                    index = self.table.indexAt(event.position().toPoint())
                    if index.isValid() and index.column() == 0:
                        self.check_delegate.suppress_next_mouse_release()
                        QTimer.singleShot(0, self.check_delegate.clear_mouse_release_suppression)
                self._reset_drag_check_state()
            elif event.type() in (QEvent.Leave, QEvent.FocusOut):
                if not QApplication.mouseButtons() & Qt.LeftButton:
                    self._reset_drag_check_state()
        return super().eventFilter(watched, event)

    def _reset_drag_check_state(self) -> None:
        self._drag_check_start_row = None
        self._drag_check_last_row = None
        self._drag_check_start_pos = None
        self._drag_check_active = False

    def _set_row_check_range(self, first: int, last: int, checked: bool) -> None:
        self._populating = True
        for target_row in range(max(first, 0), min(last, self.table.rowCount() - 1) + 1):
            item = self.table.item(target_row, 0)
            if not item:
                continue
            item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            key = str(item.data(Qt.UserRole))
            if checked:
                self.checked_keys.add(key)
            else:
                self.checked_keys.discard(key)
        self._populating = False
        self._update_selection_label()

    def _set_exclusive_row_check_range(self, first: int, last: int) -> None:
        """Keep checks only on the rows represented by the active blue drag range."""
        first = max(first, 0)
        last = min(last, self.table.rowCount() - 1)
        self._populating = True
        self.checked_keys.clear()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if not item:
                continue
            checked = first <= row <= last
            item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            if checked:
                self.checked_keys.add(str(item.data(Qt.UserRole)))
        self._populating = False
        self._update_selection_label()

    def _set_visible_checks(self, checked: bool) -> None:
        self._populating = True
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            key = str(item.data(Qt.UserRole))
            item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            if checked:
                self.checked_keys.add(key)
            else:
                self.checked_keys.discard(key)
        self._populating = False
        self._update_selection_label()

    def _clear_checks(self) -> None:
        self.checked_keys.clear()
        self.refresh_table()

    def _update_selection_label(self) -> None:
        self.selection_label.setText(f"選択: {len(self.checked_keys)}曲")

    @Slot(QTableWidgetItem)
    def _show_details(self, item: QTableWidgetItem) -> None:
        if not self.scan_result:
            return
        key = str(item.data(Qt.UserRole))
        group = next((entry for entry in self.scan_result.groups if entry.key == key), None)
        if not group:
            return
        assets = "\n".join(
            f"{fmt}: {asset.name} ({asset.size:,} bytes)" for fmt, asset in sorted(group.assets.items())
        )
        artwork = (
            f"{group.artwork.name} ({group.artwork.size:,} bytes)"
            if group.artwork
            else "なし"
        )
        live = "\n".join(group.related_live_keys) or "なし"
        metadata = group.metadata
        official = (
            f"歌唱（公式表記）: {metadata.performer or '未確認'}\n"
            f"作詞: {metadata.lyricist or '未確認'}\n"
            f"作曲: {metadata.composer or '未確認'}\n"
            f"編曲: {metadata.arranger or '未確認'}\n"
            f"収録作品: {metadata.album or '未確認'}\n"
            f"発売日: {metadata.release_date or '未確認'}\n"
            f"トラック: {metadata.track_number or '未確認'}\n"
            f"確認元: {metadata.source_url or metadata.credit_source_url or '未確認'}"
        )
        box = QMessageBox(self)
        box.setWindowTitle("楽曲詳細")
        box.setText(f"{group.title or group.internal_id}\n{group.data_type} / {group.singing} / {group.version}")
        short_label = "はい" if group.is_short_version else ("対象外" if group.data_type == KIND_BGM else "いいえ")
        box.setInformativeText(
            f"短縮版: {short_label}\n\n利用可能データ:\n{assets}"
            f"\n\n正式楽曲情報:\n{official}"
            f"\n\nアルバムアート:\n{artwork}\n\n関連ライブ:\n{live}"
        )
        box.exec()

    @Slot()
    def open_extraction(self) -> None:
        if not self.scan_result:
            QMessageBox.information(self, "Manifest未読込", "先にManifestをスキャンしてください。")
            return
        selected = [group for group in self.scan_result.groups if group.key in self.checked_keys]
        if not selected:
            QMessageBox.information(self, "曲を選択", "抽出する曲をチェックしてください。")
            return
        dialog = ExtractionDialog(len(selected), self.settings, self)
        if dialog.exec() != QDialog.Accepted:
            return
        self._start_extraction(selected, dialog.options())

    def _start_extraction(self, selected: list[SongGroup], options: ExtractionOptions) -> None:
        if not self.scan_result or self._extract_thread:
            return
        self.settings.output_dir = str(options.output_dir)
        self.settings.filename_format = options.filename_format
        self.store.save_settings(self.settings)
        self._set_busy(True, "抽出を開始します...")
        self.cancel_button.show()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)

        thread = QThread(self)
        short_filter = self.short_filter.currentData()
        catalog = filter_groups(self.scan_result.groups, short_version=short_filter)
        worker = ExtractionWorker(
            selected,
            catalog,
            options,
            self.scan_result.info.octo_root,
            self.characters,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._set_progress)
        worker.log.connect(self.append_log)
        worker.finished.connect(self._extraction_finished)
        worker.failed.connect(self._operation_failed)
        worker.cancelled.connect(self._extraction_cancelled)
        for signal in (worker.finished, worker.failed, worker.cancelled):
            signal.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._extract_thread_finished)
        self._extract_thread = thread
        self._extract_worker = worker
        thread.start()

    @Slot(int, str)
    def _set_progress(self, value: int, message: str) -> None:
        self.progress_bar.setValue(max(0, min(value, 100)))
        self.status_label.setText(message)

    @Slot(object)
    def _extraction_finished(self, paths: list[Path]) -> None:
        self.cancel_button.hide()
        self._set_busy(False, f"抽出完了: {len(paths)}ファイル")
        self.append_log(f"抽出完了: {len(paths)}ファイル")
        if paths:
            box = QMessageBox(self)
            box.setWindowTitle("抽出完了")
            box.setText(f"{len(paths)}ファイルを保存しました。")
            box.setInformativeText(str(paths[0].parent))
            open_button = box.addButton("フォルダを開く", QMessageBox.AcceptRole)
            box.addButton("閉じる", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() == open_button:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths[0].parent)))

    @Slot(str)
    def _extraction_cancelled(self, message: str) -> None:
        self.cancel_button.hide()
        self._set_busy(False, "キャンセルしました")
        self.append_log(message)

    @Slot()
    def _extract_thread_finished(self) -> None:
        if self._extract_thread:
            self._extract_thread.deleteLater()
        self._extract_thread = None
        self._extract_worker = None

    @Slot()
    def cancel_extraction(self) -> None:
        if self._extract_worker:
            self._extract_worker.request_cancel()
            self.cancel_button.setEnabled(False)
            self.status_label.setText("安全な処理単位でキャンセルします...")

    @Slot(str, str)
    def _operation_failed(self, message: str, details: str) -> None:
        self.progress_bar.setRange(0, 100)
        self.cancel_button.hide()
        self.cancel_button.setEnabled(True)
        self._set_busy(False, "エラー")
        self.append_log(f"ERROR: {message}")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle("処理に失敗しました")
        box.setText(message)
        box.setInformativeText("詳細ボタンで技術情報を確認できます。")
        box.setDetailedText(details)
        box.exec()

    @Slot()
    def open_settings(self) -> None:
        if self._scan_thread or self._extract_thread:
            return
        dialog = SettingsDialog(self.settings, self.store, self)
        if dialog.exec() != QDialog.Accepted:
            return
        old_scan_values = (
            self.settings.game_data_dir,
            self.settings.manifest_path,
            self.settings.manifest_mode,
            self.settings.online_fallback,
        )
        self.settings = dialog.result_settings()
        self.store.save_settings(self.settings)
        self.store.ensure_mapping_files()
        self.characters = self.store.load_mapping("characters.json")
        self.song_names = self.store.load_mapping("song_names.json")
        self.song_metadata = self.store.load_song_metadata()
        self.apply_theme(self.settings.theme)
        new_scan_values = (
            self.settings.game_data_dir,
            self.settings.manifest_path,
            self.settings.manifest_mode,
            self.settings.online_fallback,
        )
        if old_scan_values != new_scan_values or self.scan_result is None:
            self.start_scan()
        else:
            self._rebuild_character_filter()
            self.refresh_table()

    def _set_busy(self, busy: bool, status: str) -> None:
        self.status_label.setText(status)
        for widget in (
            self.rescan_button,
            self.settings_button,
            self.extract_button,
            self.select_all_button,
            self.clear_visible_button,
            self.clear_button,
            self.reset_filters_button,
        ):
            widget.setEnabled(not busy)
        if not busy:
            self.cancel_button.setEnabled(True)

    @Slot(str)
    def append_log(self, message: str) -> None:
        self.log_view.appendPlainText(f"[{datetime.now():%H:%M:%S}] {message}")

    def apply_theme(self, theme: str) -> None:
        app = QApplication.instance()
        if not app:
            return
        if theme == "dark":
            app.setStyleSheet(DARK_STYLE)
        elif theme == "light":
            app.setStyleSheet(LIGHT_STYLE)
        else:
            # The bare system palette can report a very dark `mid` color on
            # Windows dark mode, making secondary text nearly invisible.
            # Follow the OS color scheme while keeping the app's full
            # contrast-tested light/dark rules.
            system_style = DARK_STYLE if app.styleHints().colorScheme() == Qt.ColorScheme.Dark else LIGHT_STYLE
            app.setStyleSheet(system_style)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._scan_thread or self._extract_thread:
            QMessageBox.information(
                self,
                "処理中",
                "処理中です。抽出中の場合はキャンセルして、完了後に閉じてください。",
            )
            event.ignore()
            return
        event.accept()


BASE_STYLE = """
QWidget { font-family: "Segoe UI", "Yu Gothic UI"; font-size: 10pt; }
QMainWindow { background: palette(window); }
#appTitle { font-size: 22pt; font-weight: 700; }
#subtitle, #noteLabel { color: palette(mid); }
#card { border: 1px solid palette(midlight); border-radius: 8px; padding: 6px; }
#manifestLabel { font-weight: 600; }
#sectionTitle, #selectionLabel { font-weight: 700; }
#fieldLabel { font-size: 9pt; font-weight: 600; }
#badgeLabel { padding: 2px 9px; border-radius: 9px; }
QPushButton { min-height: 28px; padding: 2px 12px; border-radius: 5px; }
QCheckBox { min-height: 30px; spacing: 10px; padding: 2px 6px; }
QCheckBox::indicator { width: 20px; height: 20px; }
#compactButton { min-height: 24px; padding: 1px 10px; }
#primaryButton { min-width: 132px; min-height: 34px; font-weight: 700; }
QLineEdit, QComboBox { min-height: 30px; padding: 1px 8px; }
QTableWidget { gridline-color: palette(midlight); border-radius: 6px; }
QHeaderView::section { padding: 7px; font-weight: 600; border: none; border-bottom: 1px solid palette(midlight); }
QProgressBar { min-height: 20px; border-radius: 5px; text-align: center; }
QProgressBar::chunk { border-radius: 5px; background: #2f80ed; }
"""

LIGHT_STYLE = BASE_STYLE + """
QMainWindow, QWidget { background: #f5f7fa; color: #18202a; }
#card, QTableWidget, QPlainTextEdit, QLineEdit, QComboBox, QGroupBox { background: white; }
#subtitle, #noteLabel, #fieldLabel { color: #5f6b7a; }
#badgeLabel { color: #24527a; background: #e7f1fb; }
QPushButton { background: #e7edf5; border: 1px solid #cbd5e1; }
QPushButton:hover { background: #dbe7f5; }
#primaryButton { color: white; background: #2166d1; border: 1px solid #2166d1; }
#primaryButton:hover { background: #1957b8; }
"""

DARK_STYLE = BASE_STYLE + """
QMainWindow, QWidget { background: #15191f; color: #e6edf3; }
#card, QTableWidget, QPlainTextEdit, QLineEdit, QComboBox, QGroupBox { background: #1e242c; }
#subtitle, #noteLabel, #fieldLabel { color: #aeb8c4; }
#badgeLabel { color: #a9d2ff; background: #263c54; }
QTableWidget { alternate-background-color: #191f26; selection-background-color: #234d77; }
QPushButton { background: #2a323d; border: 1px solid #46515f; }
QPushButton:hover { background: #354151; }
QPushButton:disabled { color: #6f7883; }
#primaryButton { color: white; background: #2f80ed; border: 1px solid #2f80ed; }
#primaryButton:hover { background: #438fec; }
QHeaderView::section { background: #252c35; }
QToolTip { color: white; background: #303844; border: 1px solid #596575; }
"""
