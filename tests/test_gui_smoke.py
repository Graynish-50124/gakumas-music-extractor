from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox

from core.config import AppSettings, ConfigStore
from core.models import FILENAME_TITLE_CHARACTER, KIND_GENERAL, SINGING_VOCAL, SongGroup
from gui.extraction_dialog import ExtractionDialog
from gui.main_window import MainWindow


def test_main_window_constructs(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config")
    window = MainWindow(AppSettings(auto_scan=False), store, auto_start=False)
    assert window.windowTitle() == "Gakumas Music Extractor"
    assert window.table.columnCount() == len(window.COLUMNS)
    assert window.short_filter.count() == 3
    assert window.short_filter.itemText(2) == "短縮版のみ"
    assert window.short_filter.currentData() is False
    assert window.character_filter.minimumWidth() >= 220
    assert window.table.columnWidth(3) >= 175
    assert window.result_count_label.text() == "表示: 0 / 0件"
    window.apply_theme("system")
    assert "#badgeLabel { color:" in app.styleSheet()
    window.close()
    app.processEvents()


def test_extraction_dialog_uses_new_filename_default() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = ExtractionDialog(1, AppSettings())
    assert dialog.filename_format.currentData() == FILENAME_TITLE_CHARACTER
    assert dialog.filename_format.count() == 3
    assert dialog.artwork.isChecked()
    assert all("曲名マッピングが" not in item.text() for item in dialog.findChildren(QCheckBox))
    dialog.close()
    app.processEvents()


def test_selection_cell_hit_area_range_and_visible_batch(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config")
    window = MainWindow(AppSettings(auto_scan=False), store, auto_start=False)
    groups = [
        SongGroup(
            key=f"song-{index}",
            internal_id=f"all-{index:03d}",
            character_id="all",
            data_type=KIND_GENERAL,
            singing=SINGING_VOCAL,
            version="game",
            base_name=f"song-{index}",
            title=f"Song {index}",
        )
        for index in range(3)
    ]
    window.scan_result = SimpleNamespace(groups=groups)
    window.refresh_table()
    window.show()
    app.processEvents()

    first = window.table.item(0, 0)
    first_rect = window.table.visualItemRect(first)
    first_far_edge = first_rect.center()
    first_far_edge.setX(first_rect.right() - 3)
    QTest.mouseClick(window.table.viewport(), Qt.LeftButton, Qt.NoModifier, first_far_edge)
    assert groups[0].key in window.checked_keys

    third = window.table.item(2, 0)
    third_rect = window.table.visualItemRect(third)
    third_far_edge = third_rect.center()
    third_far_edge.setX(third_rect.right() - 3)
    QTest.mouseClick(window.table.viewport(), Qt.LeftButton, Qt.ShiftModifier, third_far_edge)
    assert window.checked_keys == {group.key for group in groups}

    window.clear_visible_button.click()
    assert not window.checked_keys

    window.select_all_button.click()
    assert window.checked_keys == {group.key for group in groups}

    drag_start = window.table.visualItemRect(window.table.item(0, 2)).center()
    drag_end = window.table.visualItemRect(window.table.item(2, 2)).center()
    drag_shrunk_end = window.table.visualItemRect(window.table.item(1, 2)).center()
    QTest.mousePress(window.table.viewport(), Qt.LeftButton, Qt.NoModifier, drag_start)
    QTest.mouseMove(window.table.viewport(), drag_end)
    QTest.mouseMove(window.table.viewport(), drag_shrunk_end)
    QTest.mouseRelease(window.table.viewport(), Qt.LeftButton, Qt.NoModifier, drag_shrunk_end)
    app.processEvents()
    assert window.checked_keys == {groups[0].key, groups[1].key}
    assert window.table.item(2, 0).checkState() == Qt.Unchecked
    assert {index.row() for index in window.table.selectionModel().selectedRows()} == {0, 1}

    window.clear_visible_button.click()
    assert not window.checked_keys
    window.select_all_button.click()
    assert window.checked_keys == {group.key for group in groups}
    window.close()
    app.processEvents()


@pytest.mark.skipif(not (Path.home() / "gakumas" / "octo").exists(), reason="学マスPC版ローカルデータなし")
def test_main_window_async_scan_and_filter(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config")
    window = MainWindow(AppSettings(auto_scan=False, online_fallback=False), store, auto_start=False)
    window.start_scan()
    deadline = time.monotonic() + 20
    while window._scan_thread is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert window.scan_result is not None
    assert window.table.rowCount() > 0
    hrnm_index = window.character_filter.findData("hrnm")
    window.character_filter.setCurrentIndex(hrnm_index)
    window.search.setText("018")
    app.processEvents()
    assert window.table.rowCount() >= 2
    assert window.result_count_label.text().startswith("表示: ")
    window._reset_filters()
    assert window.character_filter.currentIndex() == 0
    assert window.type_filter.currentIndex() == 0
    assert window.singing_filter.currentIndex() == 0
    assert window.short_filter.currentIndex() == 1
    assert window.search.text() == ""
    window.close()
    app.processEvents()
