from __future__ import annotations

import re

from core.extractor import ExtractionEngine
from core.models import KIND_LIVE, KIND_UNIT, SINGING_INST
from core.scanner import filter_groups, scan_music_assets


class FakeObject:
    def __init__(self, object_id: int, name: str, bundle: bool = False):
        self.id = object_id
        self.name = name
        self.objectName = f"O{object_id}"
        self.size = 10
        self.md5 = "0" * 32
        self._url = f"https://example.invalid/O{object_id}"
        if bundle:
            self.crc = 1


class FakeManifest:
    def __init__(self, objects: list[FakeObject]):
        self.objects = objects

    def search(self, pattern: str):
        return [obj for obj in self.objects if re.match(pattern, obj.name, re.IGNORECASE)]


def test_groups_formats_live_unit_inst_and_future_number() -> None:
    manifest = FakeManifest(
        [
            FakeObject(1, "sud_music_general_all-018-hrnm_game.acb"),
            FakeObject(2, "sud_music_general_all-018-hrnm_game.awb"),
            FakeObject(3, "sud_music_general_all-018-hrnm_game.mp3"),
            FakeObject(4, "sud_music_live_all-018-hrnm_true-001.unity3d", True),
            FakeObject(5, "sud_music_general_all-019-zzzz_game.awb"),
            FakeObject(6, "sud_music_general_unit-123-newunit_game-inst.awb"),
            FakeObject(7, "sud_music_live_all-018-hrnm_normal-001.unity3d", True),
        ]
    )
    groups = scan_music_assets(manifest, {"all-018/hrnm": "ねぇ、言っちゃうよ。"})
    vocal = next(group for group in groups if group.base_name.endswith("all-018-hrnm_game"))
    assert set(vocal.assets) == {"ACB", "AWB", "MP3"}
    assert vocal.title == "ねぇ、言っちゃうよ。"
    assert len(vocal.related_live_keys) == 2
    true_live = next(group for group in groups if group.data_type == KIND_LIVE and group.version == "true-001")
    short_live = next(group for group in groups if group.data_type == KIND_LIVE and group.version == "normal-001")
    assert not true_live.is_short_version
    assert short_live.is_short_version
    normal_catalog = filter_groups(groups, short_version=False)
    expanded = ExtractionEngine(None)._expand_live([vocal], normal_catalog)
    assert true_live in expanded
    assert short_live not in expanded
    assert any(group.internal_id == "all-019" and group.character_id == "zzzz" for group in groups)
    unit = next(group for group in groups if group.data_type == KIND_UNIT)
    assert unit.internal_id == "unit-123"
    assert unit.singing == SINGING_INST


def test_filters_search_character_and_kind() -> None:
    groups = scan_music_assets(
        FakeManifest(
            [
                FakeObject(1, "sud_music_general_all-018-hrnm_game.awb"),
                FakeObject(2, "sud_music_live_all-018-hrnm_true-001.unity3d", True),
                FakeObject(3, "sud_music_live_all-018-hrnm_normal-001.unity3d", True),
            ]
        )
    )
    assert len(filter_groups(groups, character_id="hrnm")) == 3
    assert len(filter_groups(groups, data_type=KIND_LIVE)) == 2
    assert len(filter_groups(groups, short_version=True)) == 1
    assert len(filter_groups(groups, short_version=False)) == 2
    assert len(filter_groups(groups, search="短縮版")) == 1
    assert len(filter_groups(groups, search="018")) == 3
